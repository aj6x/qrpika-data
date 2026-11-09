"""Ed25519 signing of manifest.json, its own step after the build (docs/DATA-FORMAT.md, "Hosting and fallback").

The app trusts a host only through this signature: it holds the public keys (DataSources.swift), and every other file
is covered by its SHA-256 in the manifest. Only manifest.json is signed. The signature is a detached file next to it,
manifest.json.sig, a small JSON over the manifest's exact bytes:

    {"key_id": "primary", "sig": "<base64 of the 64 byte Ed25519 signature>"}

Keys: by id, as named in DataSources.swift. An id names one key for good (never reused for another key), so every
app version agrees on it. `primary` signs; `backup` stays offline and signs nothing unless the primary is lost or leaks
(then a new spare gets a new id, for example `backup-2`); `dev` is a throwaway pair in this repo (pipeline/dev-signing-key.txt) that only Debug builds of the app accept. A private key is
the base64 of its 32 raw bytes: in $S2S_SIGNING_KEY (a password manager, a GitHub Actions secret), or in a file given
by --key. Never in the repo, except the dev key.

  python3 -B -m pipeline.sign data/packs/v1/manifest.json                  # key from $S2S_SIGNING_KEY, id primary
  python3 -B -m pipeline.sign data/packs/v1/manifest.json --key FILE --key-id backup
  python3 -B -m pipeline.sign data/packs/v1/manifest.json --dev             # the dev key: Debug builds only
  python3 -B -m pipeline.sign --check data/packs/v1/manifest.json          # which app key verifies it
  python3 -B -m pipeline.sign --new-pair primary                           # make a pair; prints both halves
  python3 -B -m pipeline.sign --hosting data/packs/v1/manifest.json        # apply pipeline/hosting.json (then sign)
  python3 -B -m pipeline.sign --which-key ~/.config/vhf-s2s/backup.key     # which app key a private key file is
  python3 -B -m pipeline.sign --which-key -                                # the same, typed in hidden (a paper copy)

In the data repository's GitHub Actions (docs/RELEASE-DATA.md) this file runs on its own, next to a copy of
DataSources.swift:

  python3 tools/sign.py --app-keys tools/DataSources.swift s2s/v1/manifest.json          # $S2S_SIGNING_KEY
  python3 tools/sign.py --app-keys tools/DataSources.swift --check --release --files s2s/v1/manifest.json
"""
import argparse
import base64
import getpass
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

ROOT = Path(__file__).resolve().parents[1]
HOSTING = ROOT / 'pipeline/hosting.json'
APP_KEYS = ROOT / 'Packages/S2SKit/Sources/S2SLinks/DataSources.swift'
DEV_KEY = ROOT / 'pipeline/dev-signing-key.txt'


def b64(b):
    return base64.b64encode(b).decode()


def private_from_text(text):
    """base64 of the 32 raw bytes, or (older files) a PEM."""
    text = text.strip()
    if text.startswith('-----BEGIN'):
        return serialization.load_pem_private_key(text.encode(), password=None)
    for line in text.splitlines():           # the dev file has comment lines
        line = line.strip()
        if line and not line.startswith('#'):
            try:
                raw = base64.b64decode(''.join(line.split()), validate=True)   # typed from paper in groups of four
            except ValueError:
                sys.exit(f'Not a key: {len(line)} characters that are not base64 of 32 bytes (a key is 44, ending in =)')
            if len(raw) != 32:
                sys.exit('A signing key is base64 of 32 bytes')
            return Ed25519PrivateKey.from_private_bytes(raw)
    sys.exit('No key in the text')


def load_key(path=None, dev=False):
    if dev:
        return private_from_text(DEV_KEY.read_text())
    if path:
        return private_from_text(Path(path).expanduser().read_text())
    env = os.environ.get('S2S_SIGNING_KEY')
    if env:
        return private_from_text(env)
    sys.exit('No signing key: set S2S_SIGNING_KEY (base64), pass --key FILE, or --dev for a Debug-only signature. '
             'See docs/DATA-FORMAT.md, "The signing keys".')


def raw_private(k):
    return k.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())


def public_b64(key):
    pub = key.public_key() if isinstance(key, Ed25519PrivateKey) else key
    return b64(pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))


def sig_path(manifest):
    return Path(str(manifest) + '.sig')


def sign(manifest, key, key_id):
    """Writes manifest.json.sig for the manifest's exact bytes."""
    expected = app_keys().get(key_id)
    if expected is None:
        sys.exit(f'The app has no key "{key_id}" (DataSources.swift): its manifest would only make apps ask for an update')
    if expected != public_b64(key):
        sys.exit(f'This key is not the app\'s "{key_id}" key (DataSources.swift): refusing to sign')
    mb = Path(manifest).read_bytes()
    sp = sig_path(manifest)
    sp.write_text(json.dumps({'key_id': key_id, 'sig': b64(key.sign(mb))}) + '\n')
    return sp


def app_keys(release_only=False):
    """{key id: public key} as listed in DataSources.swift: releaseKeys, and devKeys unless release_only."""
    text = APP_KEYS.read_text()
    out = {}
    for block in ['releaseKeys'] + ([] if release_only else ['devKeys']):
        m = re.search(block + r'[^=]*=\s*\[(.*?)\]', text, re.S)
        if m:
            out.update(re.findall(r'"([A-Za-z0-9_-]+)":\s*"([A-Za-z0-9+/=]{44})"', m[1]))
    return out


def verify(manifest, release_only=False):
    """(ok, message) for manifest and its .sig against the app's keys (without dev when release_only)."""
    manifest = Path(manifest)
    sp = sig_path(manifest)
    if not sp.exists():
        return False, f'no {sp.name}'
    try:
        s = json.loads(sp.read_text())
        kid, sig = s['key_id'], base64.b64decode(s['sig'])
    except Exception as e:
        return False, f'{sp.name} unreadable: {e}'
    keys = app_keys(release_only)
    if kid not in keys:
        return False, f'{sp.name} names key "{kid}", which the app ' + ('Release build ' if release_only else '') + 'does not hold'
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(keys[kid])).verify(sig, manifest.read_bytes())
    except Exception:
        return False, f'{sp.name} does not verify with the app\'s "{kid}" key'
    return True, f'{sp.name} verifies with the app\'s "{kid}" key' + ('' if kid in app_keys(True) else ' (Debug builds only)')


def hosting():
    """pipeline/hosting.json: the publishing choices the manifest carries (base_url, fallback_hosts, min_app, notice, free_areas)."""
    h = json.loads(HOSTING.read_text())
    return {k: h.get(k) for k in ('base_url', 'fallback_hosts', 'min_app', 'notice', 'free_areas')}


def apply_hosting(m):
    h = hosting()
    m['base_url'] = h['base_url'] or None
    m['fallback_hosts'] = h['fallback_hosts'] or []
    m['min_app'] = int(h['min_app'] or 1)
    m['notice'] = h['notice'] or None
    ids = {a['id'] for a in m.get('areas', [])}
    unknown = [a for a in h['free_areas'] or [] if a not in ids]
    if unknown:
        sys.exit(f'pipeline/hosting.json free_areas names areas the pack lacks: {unknown}')
    m['free_areas'] = h['free_areas'] or []
    return m


def check_files(manifest):
    """Problems with the files the manifest names, next to it: missing, wrong size or wrong SHA-256."""
    manifest = Path(manifest)
    m = json.loads(manifest.read_text())
    want = [(m['summits'], m.get('summits_bytes'), m.get('summits_sha256'))]
    if m.get('model'):                                      # the codec's model (format 1)
        want += [(m['model'], m.get('model_bytes'), m.get('model_sha256'))]
    want += [(a['file'], a['bytes'], a['sha256']) for a in m['areas']]
    r = m.get('rfi')
    if r:                                                   # the RFI layer (US), in the same folder
        want += [(r['index'], r.get('index_bytes'), r.get('index_sha256'))]
        want += [(a['file'], a['bytes'], a['sha256']) for a in r['areas']]
    problems = []
    for name, size, sha in want:
        f = manifest.parent / name
        if not f.exists():
            problems.append(f'{name}: missing')
        elif size is not None and f.stat().st_size != size:
            problems.append(f'{name}: {f.stat().st_size} bytes, the manifest says {size}')
        elif sha and hashlib.sha256(f.read_bytes()).hexdigest() != sha:
            problems.append(f'{name}: SHA-256 differs from the manifest')
    return len(want), problems


def which_key(path):
    """The app key id a private key belongs to, or None. '-' asks for it, hidden (to check a copy typed from paper)."""
    text = getpass.getpass('Private key (hidden): ') if path == '-' else Path(path).expanduser().read_text()
    pub = public_b64(private_from_text(text))
    return next((kid for kid, v in app_keys().items() if v == pub), None), pub


def write_manifest(path, m):
    Path(path).write_text(json.dumps(m, ensure_ascii=False, indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('manifest', nargs='?', help='manifest.json to sign')
    ap.add_argument('--key', help='file with the private key (base64 of 32 bytes); default $S2S_SIGNING_KEY')
    ap.add_argument('--key-id', default='primary', help='the id of the key in DataSources.swift (default primary)')
    ap.add_argument('--dev', action='store_true', help='sign with the dev key (Debug builds only)')
    ap.add_argument('--check', action='store_true', help='verify the manifest against the app keys')
    ap.add_argument('--hosting', action='store_true', help='apply pipeline/hosting.json to the manifest (sign it after)')
    ap.add_argument('--release', action='store_true', help='with --check: only the keys a Release build trusts')
    ap.add_argument('--files', action='store_true', help='with --check: also check every file the manifest names')
    ap.add_argument('--app-keys', metavar='FILE', help='DataSources.swift to read the app keys from (default: this repo)')
    ap.add_argument('--which-key', metavar='FILE', help='say which app key a private key file is ("-": type it in hidden)')
    ap.add_argument('--new-pair', metavar='ID', help='make a key pair and print both halves')
    a = ap.parse_args()
    global APP_KEYS
    if a.app_keys:
        APP_KEYS = Path(a.app_keys)
    if a.which_key:
        kid, pub = which_key(a.which_key)
        print(f'OK this is the app\'s "{kid}" key' if kid else f'FAIL no app key matches (its public key is {pub})')
        sys.exit(0 if kid else 1)
    if a.new_pair:
        k = Ed25519PrivateKey.generate()
        print(f'Private key ({a.new_pair}), base64. Keep it where docs/DATA-FORMAT.md says, never in git:\n  {b64(raw_private(k))}')
        print(f'Public key, for "{a.new_pair}" in {APP_KEYS.relative_to(ROOT)}:\n  "{public_b64(k)}"')
        return
    if not a.manifest:
        ap.error('which manifest.json?')
    if a.check:
        ok, msg = verify(a.manifest, release_only=a.release)
        print(('OK ' if ok else 'FAIL ') + msg)
        if a.files:
            n, problems = check_files(a.manifest)
            for p in problems:
                print('FAIL ' + p)
            print(f'{"OK" if not problems else "FAIL"} {n - len(problems)} of {n} files match the manifest')
            ok = ok and not problems
        sys.exit(0 if ok else 1)
    if a.hosting:
        m = apply_hosting(json.loads(Path(a.manifest).read_text()))
        write_manifest(a.manifest, m)
        old = sig_path(a.manifest)
        if old.exists():
            old.unlink()                          # the old signature no longer fits; sign again
        print(f'{a.manifest}: hosting fields applied; sign it now')
        return
    key = load_key(a.key, dev=a.dev)
    sp = sign(a.manifest, key, 'dev' if a.dev else a.key_id)
    print(f'{sp} ({verify(a.manifest)[1]})')


if __name__ == '__main__':
    main()
