#!/usr/bin/env python3
"""s2s_fetch: the VHF+ S2S summit-to-summit pack, downloaded and decoded to SQLite (or CSV).

Python 3.8 or newer, standard library only. One file: copy it anywhere.

  python3 s2s_fetch.py --list                         # the areas, their summits and sizes
  python3 s2s_fetch.py --areas W6 W7A                 # download those areas, write s2s-data/s2s.sqlite
  python3 s2s_fetch.py --areas 'W*' 'VE*'             # by pattern
  python3 s2s_fetch.py --all                          # the world, about 66 MB to download
  python3 s2s_fetch.py --areas W6 --csv               # also s2s-data/csv/W6.csv
  python3 s2s_fetch.py --areas W6 --margins           # also every preset's margin and the web page's group letter

Run it again later to update: it asks for the manifest with If-None-Match, and downloads only the area files that
changed (every file name carries its hash). Every file is checked against its SHA-256 in the manifest, and the
manifest against its Ed25519 signature, so a mirror can't hand you wrong data.

License: CC BY-NC-ND 4.0 (see LICENCE below), the same as aj6x.com. The sources keep their own licenses.

The format is documented in docs/DATA-FORMAT.md of the VHF+ S2S app (published next to this file as DATA-FORMAT.md
when available), with test vectors. Its losses are basic transmission loss in dB between antennas 2 m above each
summit: ITM (Longley-Rice) on 6 m to 3 cm, line of sight with atmospheric gas on 1.2 cm, 6 mm and 4 mm.

The SQLite file, s2s-data/s2s.sqlite:
  summits(number, ref, name, lat, lon, z_m, activations, area)       every summit of the world
  links(a, b, band, km, los, h10, h50, h90, v10, v50, v90, canopy_db, dry_db, humid_db)
      one row per summit pair and band, a < b (summit numbers). h/v: horizontal or vertical polarization, the loss
      bettered 10, 50 or 90% of the time. canopy_db: extra loss under a 20 m canopy (vertical, 90%); 255 dB means
      blocked. Line-of-sight bands (1.2cm, 6mm, 4mm): every h/v column is the average day's loss, dry_db and
      humid_db the dry and humid days'. los: the tops see each other (4/3 earth).
  bands(name, mhz, kind), presets(band, id, grp, label, mode, power_dbm, gain_dbi, threshold_dbm, loss, weather)
  margins(a, b, band, preset, margin_db) and groups(a, b, band, weather, grp)      with --margins
  meta(key, value)                                                    build date, source, license and credits
"""
import argparse
import base64
import bisect
import csv
import email.utils
import fnmatch
import hashlib
import json
import math
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

VERSION = '1.0'
USER_AGENT = f's2s_fetch/{VERSION}'
HOSTS = ['https://data.qrpika.com/s2s/', 'https://data.aj6x.com/s2s/']     # primary, then the fallback
FORMAT = 1

# The release keys of the VHF+ S2S app (DataSources.swift releaseKeys), by key id. pipeline/publish.py checks they match.
KEYS = {
    "primary": "Rt3YrHclarBDasou1Qv0T/6Tf8btv+c47iWrcVOVS2A=",
    "backup": "mwx1PidxLD2c5MErQ+ORX6s8//tXmxX8NjP+JGLhwSo=",
}

LICENCE = ('(c) 2026 G Blaj (AJ6X). CC BY-NC-ND 4.0, https://creativecommons.org/licenses/by-nc-nd/4.0/ : '
           'copy and share it with credit to AJ6X, not commercially, and not changed. For anything beyond that, ask.')
CREDITS = ('Summits: the SOTA summits list. Terrain: the USGS 3D Elevation Program, Natural Resources Canada\'s MRDEM, '
           'and elsewhere Copernicus GLO-30 (produced using Copernicus WorldDEM-30, (c) DLR e.V. 2010-2014 and (c) Airbus '
           'Defence and Space GmbH 2014-2018, provided under COPERNICUS by the European Union and ESA, all rights '
           'reserved). Paths: NTIA\'s Irregular Terrain Model; oxygen and water vapor: ITU-R P.676. Calculated from the '
           'terrain, not measured. Path losses: VHF+ S2S, https://aj6x.com/s2s/')


# ---------------------------------------------------------------------------------------------------------------------
# Ed25519 verification (RFC 8032), so the manifest can be checked without any package. Slow, but run once.

_P = 2 ** 255 - 19
_Q = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_I = pow(2, (_P - 1) // 4, _P)


def _add(a, b):
    A, B = (a[1] - a[0]) * (b[1] - b[0]) % _P, (a[1] + a[0]) * (b[1] + b[0]) % _P
    C, D = 2 * a[3] * b[3] * _D % _P, 2 * a[2] * b[2] % _P
    E, F, G, H = B - A, D - C, D + C, B + A
    return E * F % _P, G * H % _P, F * G % _P, E * H % _P


def _mul(s, pt):
    acc = (0, 1, 1, 0)
    while s:
        if s & 1:
            acc = _add(acc, pt)
        pt, s = _add(pt, pt), s >> 1
    return acc


def _x(y, sign):
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * _I % _P
    if (x * x - x2) % _P:
        return None
    return _P - x if (x & 1) != sign else x


def _point(b):
    y = int.from_bytes(b, 'little')
    sign, y = y >> 255, y & ((1 << 255) - 1)
    x = _x(y, sign)
    return None if x is None else (x, y, 1, x * y % _P)


_GY = 4 * pow(5, _P - 2, _P) % _P
_G = (_x(_GY, 0), _GY, 1, _x(_GY, 0) * _GY % _P)


def ed25519_verify(public, message, sig):
    if len(public) != 32 or len(sig) != 64:
        return False
    a, r = _point(public), _point(sig[:32])
    s = int.from_bytes(sig[32:], 'little')
    if a is None or r is None or s >= _Q:
        return False
    h = int.from_bytes(hashlib.sha512(sig[:32] + public + message).digest(), 'little') % _Q
    left, right = _mul(s, _G), _add(r, _mul(h, a))
    return (left[0] * right[2] - right[0] * left[2]) % _P == 0 and (left[1] * right[2] - right[1] * left[2]) % _P == 0


# ---------------------------------------------------------------------------------------------------------------------
# HTTP, kindly: one request at a time, backoff on 429 and 5xx with Retry-After honoured, a few tries, then the next host.

class Fetch:
    def __init__(self, hosts, quiet=False):
        self.hosts = [h if h.endswith('/') else h + '/' for h in hosts]
        self.quiet = quiet
        self.host = None                         # the host that last answered

    def get(self, name, etag=None, tries=4):
        """(status, body, etag) of v<FORMAT>/<name> from the first host that answers."""
        problems = []
        order = ([self.host] if self.host else []) + [h for h in self.hosts if h != self.host]
        for host in order:
            url = f'{host}v{FORMAT}/{name}'
            for attempt in range(tries):
                req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept-Encoding': 'identity'})
                if etag:
                    req.add_header('If-None-Match', etag)
                if name.startswith('manifest.json'):
                    req.add_header('Cache-Control', 'no-cache')
                try:
                    with urllib.request.urlopen(req, timeout=30) as r:
                        self.host = host
                        return r.status, r.read(), r.headers.get('ETag')
                except urllib.error.HTTPError as e:
                    if e.code == 304:
                        self.host = host
                        return 304, b'', etag
                    if e.code == 429 or e.code >= 500:
                        wait = min(2 ** attempt * 5, 120)
                        ra = e.headers.get('Retry-After')
                        if ra:
                            try:
                                wait = max(wait, int(ra))
                            except ValueError:
                                when = email.utils.parsedate_to_datetime(ra)
                                wait = max(wait, when.timestamp() - time.time()) if when else wait
                        if attempt + 1 < tries and wait <= 600:
                            self.say(f'  {url}: {e.code}, waiting {wait:.0f} s')
                            time.sleep(wait)
                            continue
                    problems.append(f'{url}: HTTP {e.code}')
                    break
                except (urllib.error.URLError, OSError) as e:
                    problems.append(f'{url}: {getattr(e, "reason", e)}')
                    break
        raise SystemExit('Could not fetch ' + name + ':\n  ' + '\n  '.join(problems))

    def say(self, text):
        if not self.quiet:
            print(text, file=sys.stderr)


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def check_signature(manifest_bytes, sig_bytes):
    """The key id that signed, or exit."""
    try:
        s = json.loads(sig_bytes)
        kid, sig = s['key_id'], base64.b64decode(s['sig'])
    except Exception as e:
        raise SystemExit(f'manifest.json.sig is unreadable: {e}')
    if kid not in KEYS:
        raise SystemExit(f'The manifest is signed with key "{kid}", which this s2s_fetch {VERSION} does not know. '
                         f'Get the current s2s_fetch.py from {HOSTS[0]}s2s_fetch.py')
    if not ed25519_verify(base64.b64decode(KEYS[kid]), manifest_bytes, sig):
        raise SystemExit(f'manifest.json does not match its signature (key "{kid}"): not using it')
    return kid


# ---------------------------------------------------------------------------------------------------------------------
# The record format (docs/DATA-FORMAT.md, "A record"; pipeline/fmt.py is the reference): one rANS stream per summit,
# coded under the model file the manifest names. Integer arithmetic throughout, as in the app.

ITM_FIELDS = ('e', 's10', 's90', 'pol', 'can')
LOS_FIELDS = ('e', 'dry', 'hum')
_MAG, _ACT, _LVL_E, _LVL = (1, 2, 3, 5, 9, 17), (1, 2, 4, 8), (5, 10, 20, 40, 80), (1, 3, 6, 12)
_NK = (50, 100, 200, 400, 800, 1600, 3200)
_K_ITM = (300, 2250, 20250, 5, 5)
_RANS_L, _SCALE, _MASK = 1 << 23, 15, (1 << 15) - 1


def _bucket(x, edges):
    k = 0
    for e in edges:
        if e > x:
            break
        k += 1
    return k


def _ilog(d2):
    if d2 <= 0:
        return 0
    L = d2.bit_length() - 1
    return L * 16 + (((d2 >> (L - 4)) if L >= 4 else (d2 << (4 - L))) & 15)


class Model:
    """model-<hash>.bin: 'S2SM', u32 layout (1), u32 header length, the header (JSON), u32 table count, then per table
    u64 key (stream << 34 | level << 32 | context), u16 entries, per entry u16 symbol and u16 frequency (of 2^15)."""

    def __init__(self, blob):
        if blob[:4] != b'S2SM' or int.from_bytes(blob[4:8], 'little') != 1:
            raise SystemExit('The model file is not layout 1: get a newer s2s_fetch.')
        hl = int.from_bytes(blob[8:12], 'little')
        h = json.loads(blob[12:12 + hl])
        at = 12 + hl
        count = int.from_bytes(blob[at:at + 4], 'little')
        at += 4
        self.tables = {}
        for _ in range(count):
            key = int.from_bytes(blob[at:at + 8], 'little')
            n = int.from_bytes(blob[at + 8:at + 10], 'little')
            at += 10
            syms, freqs, cums, c = [], [], [], 0
            for i in range(n):
                p = at + 4 * i
                syms.append(blob[p] | blob[p + 1] << 8)
                f = blob[p + 2] | blob[p + 3] << 8
                freqs.append(f)
                cums.append(c)
                c += f
            at += 4 * n
            self.tables[key] = (syms, freqs, cums)
        self.weights = {row[0]: row[1:] for row in h['weights']}
        self.ls = set(h['ls_streams'])
        self.cos, self.dk_edges, self.gas0, self.predict = h['cos_q16'], h['dk_edges'], h['gas0'], h['predict']
        self.shift = h['ls_shift']


class _Rans:
    def __init__(self, buf, model):
        if len(buf) < 4:
            raise ValueError('record too short')
        self.buf, self.m = buf, model
        self.x = int.from_bytes(buf[:4], 'little')
        self.pos = 4

    def _advance(self, start, freq):
        x = freq * (self.x >> _SCALE) + (self.x & _MASK) - start
        buf, pos = self.buf, self.pos
        while x < _RANS_L:
            if pos >= len(buf):
                raise ValueError('record ends early')
            x = (x << 8) | buf[pos]
            pos += 1
        self.x, self.pos = x, pos

    def bits(self, k):
        out = 0
        while k > 0:
            c = min(k, 15)
            k -= c
            freq = 1 << (_SCALE - c)
            part = (self.x & _MASK) >> (_SCALE - c)
            self._advance(part * freq, freq)
            out = (out << c) | part
        return out

    def sym(self, s, fine, K, alpha):
        tables = self.m.tables
        for key in ((s << 34) | (2 << 32) | fine, (s << 34) | (1 << 32) | (fine // K), s << 34):
            t = tables.get(key)
            if t is None:
                continue
            syms, freqs, cums = t
            slot = self.x & _MASK
            e = bisect.bisect_right(cums, slot) - 1
            self._advance(cums[e], freqs[e])
            if syms[e] != alpha:
                return syms[e]
        raise ValueError('no table codes this symbol')


def decode(buf, s, bands, model, latq, lonq):
    """(others, los flags, [{field: {link index: code}} per band]) of summit s's record."""
    m = model
    r = _Rans(buf, m)
    c = r.bits(5)
    n = (1 << c) + r.bits(c) - 1
    nk = _bucket(n, _NK)
    others, pc = [], 21
    for k in range(n):
        cls = r.sym(0, pc * 8 + nk, 8, 40)
        g = (1 << cls) + r.bits(cls) - 1
        others.append(s + ((g >> 1) ^ -(g & 1)) if k == 0 else others[-1] + g + 1)
        pc = min(cls, 20)
    ya, xa = latq[s], lonq[s]
    dk, ld = [], []
    for o in others:
        yb = latq[o]
        dx = abs(lonq[o] - xa)
        if dx > 1800000:
            dx = 3600000 - dx
        dxs = (dx * m.cos[min((abs(ya + yb) // 2) // 1000, 900)]) >> 16
        d2 = dxs * dxs + (yb - ya) ** 2
        dk.append(_bucket(d2, m.dk_edges))
        ld.append(_ilog(d2))
    los, prev = [], 2
    for k in range(n):
        prev = r.sym(1, prev * 10 + dk[k], 10, 2)
        los.append(prev)
    cnt = [0] * n
    last = [[0] * n for _ in range(5)]
    last2 = [[0] * n for _ in range(5)]
    last_los = [0] * n
    resid = [[0] * n for _ in range(5)]
    pres_prev = pres_prev2 = None
    out = []
    for bi, band in enumerate(bands):
        pres = []
        for k in range(n):
            before = pres_prev[k] if bi > 0 else 1
            before2 = pres_prev2[k] if bi > 1 else 1
            prv = pres[k - 1] if k > 0 else 2
            cx = (before * 2 + before2) * 2 + los[k]
            pres.append(r.sym(2 + bi, cx * 30 + prv * 10 + dk[k], 30, 2))
        idx = [k for k in range(n) if pres[k]]
        itm = band['kind'] != 'los'
        fields = ITM_FIELDS if itm else LOS_FIELDS
        vals = {}
        for fi, f in enumerate(fields):
            s_ = 13 + bi * 5 + fi
            lo_v = -128 if (itm and fi == 3) else 0
            v = {}
            prev_sym = 0
            K = _K_ITM[fi] if itm else (10 if fi == 0 else 90)
            for t, k in enumerate(idx):
                ok = t > 0
                q = idx[t - 1] if ok else k
                L, D = los[k], dk[k]
                pm = _bucket(prev_sym, _MAG) if ok else 7
                if itm:
                    lf, lf2 = last[fi], last2[fi]
                    a = v[q] if ok else 0
                    b, c = lf[k], (lf[q] if ok else 0)
                    b2, c2 = lf2[k], (lf2[q] if ok else 0)
                    a2 = v[idx[t - 2]] if t >= 2 else a
                    if not ok:
                        cs = 0
                    elif cnt[k] < 1:
                        cs = 1
                    elif cnt[q] < 1:
                        cs = 2
                    elif cnt[k] >= 2 and cnt[q] >= 2:
                        cs = 4
                    else:
                        cs = 3
                    ldk = ld[k]
                    ldq = ld[q] if ok else ldk
                    feats = ((b, ldk, 1), (a, a2, ldk, ldq, 1), (b, a, a2, ldk, ldq, 1), (b, a, c, a2, ldk, ldq, 1),
                             (b, a, c, b2, c2, a2, ldk, ldq, 1))[cs]
                    w = m.weights.get((s_ * 5 + cs) * 20 + L * 10 + D) if s_ in m.ls else None
                    if w:
                        pred = (sum(wi * x for wi, x in zip(w, feats)) + (1 << (m.shift - 1))) >> m.shift
                    else:
                        pred = feats[0]
                    coarse = (pm * 2 + L) * 5 + cs
                    act = _bucket(abs(a - c), _ACT)
                    if fi == 0:
                        fine = coarse * 300 + (D * 5 + act) * 6 + _bucket(b, _LVL_E)
                    elif fi < 3:
                        rr = ((D * 5 + act) * 5 + _bucket(abs(b), _LVL)) * 9 + min(max(resid[0][k], -4), 4) + 4
                        if fi == 2:
                            rr = rr * 9 + min(max(resid[1][k], -4), 4) + 4
                        fine = coarse * K + rr
                    else:
                        fine = coarse * 5 + _bucket(abs(b), _LVL)
                else:
                    if fi == 0:
                        pred = last_los[k]
                    else:
                        pred = (max(vals['e'][k] - m.gas0, 0) * m.predict[bi][fi] + 128) >> 8
                    coarse = pm * 2 + L
                    fine = coarse * 10 + D if fi == 0 else coarse * 90 + D * 9 + min(max(resid[0][k], -4), 4) + 4
                pred = min(max(pred, lo_v), lo_v + 255)
                sym = r.sym(s_, fine, K, 256)
                d = (sym >> 1) ^ -(sym & 1)
                v[k] = lo_v + ((pred + d - lo_v) & 255)
                resid[fi][k] = d
                prev_sym = sym
            vals[f] = v
        if itm:
            for k in idx:
                for fi, f in enumerate(ITM_FIELDS):
                    last2[fi][k] = last[fi][k]
                    last[fi][k] = vals[f][k]
                cnt[k] += 1
        else:
            for k in idx:
                last_los[k] = vals['e'][k]
        pres_prev2, pres_prev = pres_prev, pres
        out.append(vals)
    if r.x != _RANS_L or r.pos != len(buf):
        raise ValueError(f'record of summit {s} is corrupt')
    return others, [bool(x) for x in los], out


def km(m, lat, lon, a, b):
    p1, p2 = math.radians(lat[a]), math.radians(lat[b])
    dp, dl = p2 - p1, math.radians(lon[b] - lon[a])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * m['earth_radius_m'] * math.asin(math.sqrt(h)) / 1e3


def losses(m, band, codes, d_km):
    """The decoded losses in dB: H10 H50 H90 V10 V50 V90 C, and A DRY HUMID on the los bands."""
    step, off = m['step_db'], m['e_offset_db']
    fs = 20 * math.log10(4 * math.pi * d_km * 1e3 * band['mhz'] / 299.792458)
    if band['kind'] == 'los':
        a = fs + codes['e'] * step - off
        return dict(A=a, DRY=a - codes['dry'] * step, HUMID=a + codes['hum'] * step,
                    H10=a, H50=a, H90=a, V10=a, V50=a, V90=a, C=0.0)
    h50 = fs + codes['e'] * step - off
    h10, h90, pol = h50 - codes['s10'] * step, h50 + codes['s90'] * step, codes['pol'] * step
    return dict(H50=h50, H10=h10, H90=h90, V50=h50 + pol, V10=h10 + pol, V90=h90 + pol, C=codes['can'] * step)


LOSS = {'V90+C': lambda L: L['V90'] + L['C'], 'H90': lambda L: L['H90'], 'H50': lambda L: L['H50'],
        'H10': lambda L: L['H10'], 'max(H90,V90)+C': lambda L: max(L['H90'], L['V90']) + L['C'],
        'H50+C': lambda L: L['H50'] + L['C'], 'H10+C': lambda L: L['H10'] + L['C'],
        'A': lambda L: L['A'], 'DRY': lambda L: L['DRY'], 'HUMID': lambda L: L['HUMID']}


def margins(band, L):
    return {p['id']: p['power_dbm'] + p['gain_dbi'] - LOSS[p['loss']](L) - p['threshold_dbm'] for p in band['presets']}


def groups(band, mg):
    """{weather: letter}: the first preset, in order, with margin >= 0 (DATA-FORMAT.md, "Groups")."""
    out = {}
    for w in (['average', 'dry', 'humid'] if band['kind'] == 'los' else ['average']):
        g = next((p['group'] for p in band['presets'] if p.get('weather', 'average') == w and mg[p['id']] >= 0), None)
        out[w] = g or ('f' if band['name'] in ('6m', '2m', '1.25m', '70cm') else '-')
    return out


class Pack:
    """A manifest, its summit index and the area files in one folder."""

    def __init__(self, folder):
        self.dir = Path(folder)
        self.m = json.loads((self.dir / 'manifest.json').read_text())
        self.s = json.loads((self.dir / self.m['summits']).read_text())
        mb = (self.dir / self.m['model']).read_bytes()
        if hashlib.sha256(mb).hexdigest() != self.m['model_sha256']:
            raise SystemExit(f'{self.m["model"]} does not match the manifest. Delete it and run again.')
        self.model = Model(mb)
        q = lambda x: math.floor(x * 10000 + 0.5)            # the codec's coordinates: 1e-4 degree integers
        self.latq, self.lonq = [q(x) for x in self.s['lat']], [q(x) for x in self.s['lon']]
        self.by_area = {}
        for k, a in enumerate(self.s['area']):
            self.by_area.setdefault(a, []).append(k)        # pack order within each area

    def records(self, area_index):
        """(summit number, record) for every summit of the area, in file order."""
        a = self.m['areas'][area_index]
        data = (self.dir / a['file']).read_bytes()
        at = 0
        for k in self.by_area.get(area_index, []):
            size = self.s['size'][k]
            yield k, data[at:at + size]
            at += size
        if at != len(data):
            raise ValueError(f'{a["file"]}: {len(data) - at} bytes not accounted for')

    def record(self, k):
        area = self.s['area'][k]
        at = sum(self.s['size'][j] for j in self.by_area[area] if j < k)
        with open(self.dir / self.m['areas'][area]['file'], 'rb') as f:
            f.seek(at)
            return f.read(self.s['size'][k])

    def links(self, k, raw):
        """(other, band index, los, km, codes, losses) for every link of summit k."""
        others, los, per_band = decode(raw, k, self.m['bands'], self.model, self.latq, self.lonq)
        lat, lon = self.s['lat'], self.s['lon']
        dist = [km(self.m, lat, lon, k, o) for o in others]
        for bi, (band, cols) in enumerate(zip(self.m['bands'], per_band)):
            if not cols:
                continue
            first = next(iter(cols.values()))
            for i in first:
                codes = {f: cols[f][i] for f in cols}
                yield others[i], bi, los[i], dist[i], codes, losses(self.m, band, codes, dist[i])


# ---------------------------------------------------------------------------------------------------------------------

def refresh(fetch, folder, skip_signature=False):
    """The manifest, fetched again only when it changed (If-None-Match), checked against its signature."""
    folder.mkdir(parents=True, exist_ok=True)
    mpath, epath = folder / 'manifest.json', folder / 'manifest.etag'
    etag = epath.read_text().strip() if epath.exists() and mpath.exists() else None
    status, body, new_etag = fetch.get('manifest.json', etag)
    if status == 304:
        fetch.say(f'manifest unchanged ({fetch.host})')
        body = mpath.read_bytes()
    else:
        _, sig, _ = fetch.get('manifest.json.sig')
        if not skip_signature:
            kid = check_signature(body, sig)
            fetch.say(f'manifest of {json.loads(body)["built"]} from {fetch.host}, signed by "{kid}"')
        (folder / 'manifest.json.sig').write_bytes(sig)
        mpath.write_bytes(body)
        if new_etag:
            epath.write_text(new_etag)
    m = json.loads(body)
    if m.get('format') != FORMAT:
        raise SystemExit(f'The host serves format {m.get("format")}; this s2s_fetch reads format {FORMAT}. Get a newer one.')
    return m


def download(fetch, folder, m, want_areas):
    """Downloads the summit index and the chosen areas' files that aren't here yet, one at a time, each checked."""
    files = [(m['summits'], m['summits_bytes'], m['summits_sha256']), (m['model'], m['model_bytes'], m['model_sha256'])]
    files += [(a['file'], a['bytes'], a['sha256']) for a in m['areas'] if a['id'] in want_areas]
    todo = [f for f in files if not ((folder / f[0]).exists() and (folder / f[0]).stat().st_size == f[1])]
    if todo:
        fetch.say(f'{len(todo)} files to download, {sum(f[1] for f in todo) / 1e6:.1f} MB')
    for name, size, sha in todo:
        _, data, _ = fetch.get(name)
        if len(data) != size or hashlib.sha256(data).hexdigest() != sha:
            raise SystemExit(f'{name} from {fetch.host} does not match the manifest: not using it')
        tmp = folder / (name + '.part')
        tmp.write_bytes(data)
        os.replace(tmp, folder / name)
        fetch.say(f'  {name} {size / 1e6:.2f} MB')
    for name, size, sha in files:                       # every file checked, including those kept from before
        if sha256(folder / name) != sha:
            raise SystemExit(f'{folder / name} does not match the manifest. Delete it and run again.')
    keep = {f[0] for f in files} | {a['file'] for a in m['areas']}
    for f in folder.glob('links-*.bin'):
        if f.name not in keep:
            f.unlink()                                   # an older build's file
    for f in list(folder.glob('summits-*.json')) + list(folder.glob('model-*.bin')):
        if f.name not in (m['summits'], m['model']):
            f.unlink()
    return m


def write_sqlite(pack, area_ids, path, with_margins, host):
    m, s = pack.m, pack.s
    tmp = Path(str(path) + '.part')
    if tmp.exists():
        tmp.unlink()
    db = sqlite3.connect(tmp)
    db.executescript('''
        PRAGMA journal_mode = OFF; PRAGMA synchronous = OFF;
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE summits (number INTEGER PRIMARY KEY, ref TEXT, name TEXT, lat REAL, lon REAL, z_m REAL,
                              activations INTEGER, area TEXT);
        CREATE TABLE bands (name TEXT PRIMARY KEY, mhz REAL, kind TEXT);
        CREATE TABLE presets (band TEXT, id TEXT, grp TEXT, label TEXT, mode TEXT, power_dbm REAL, gain_dbi REAL,
                              threshold_dbm REAL, loss TEXT, weather TEXT, PRIMARY KEY (band, id));
        CREATE TABLE links (a INTEGER, b INTEGER, band TEXT, km REAL, los INTEGER, h10 REAL, h50 REAL, h90 REAL,
                            v10 REAL, v50 REAL, v90 REAL, canopy_db REAL, dry_db REAL, humid_db REAL,
                            PRIMARY KEY (a, b, band)) WITHOUT ROWID;
    ''')
    if with_margins:
        db.executescript('''
            CREATE TABLE margins (a INTEGER, b INTEGER, band TEXT, preset TEXT, margin_db REAL,
                                  PRIMARY KEY (a, b, band, preset)) WITHOUT ROWID;
            CREATE TABLE groups (a INTEGER, b INTEGER, band TEXT, weather TEXT, grp TEXT,
                                 PRIMARY KEY (a, b, band, weather)) WITHOUT ROWID;
        ''')
    meta = dict(format=m['format'], built=m['built'], listed=m.get('listed'), source=host, tool=USER_AGENT,
                areas=' '.join(sorted(area_ids)), licence=LICENCE, credits=CREDITS,
                about='Basic transmission loss in dB, antennas 2 m above each summit. docs/DATA-FORMAT.md of VHF+ S2S.')
    db.executemany('INSERT INTO meta VALUES (?, ?)', [(k, str(v)) for k, v in meta.items()])
    areas = [a['id'] for a in m['areas']]
    db.executemany('INSERT INTO summits VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                   [(k, s['ref'][k], s['name'][k], s['lat'][k], s['lon'][k], s['z'][k], s['activations'][k],
                     areas[s['area'][k]]) for k in range(len(s['ref']))])
    db.executemany('INSERT INTO bands VALUES (?, ?, ?)', [(b['name'], b['mhz'], b['kind']) for b in m['bands']])
    db.executemany('INSERT INTO presets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                   [(b['name'], p['id'], p['group'], p.get('label'), p.get('mode'), p['power_dbm'], p['gain_dbi'],
                     p['threshold_dbm'], p['loss'], p.get('weather')) for b in m['bands'] for p in b['presets']])
    names = [b['name'] for b in m['bands']]
    done = set()                              # areas written: a pair whose other end is in one is already in
    t0, rows = time.time(), 0
    for ai, a in enumerate(m['areas']):
        if a['id'] not in area_ids:
            continue
        L, M, G = [], [], []
        for k, raw in pack.records(ai):
            for o, bi, los, d, codes, lo in pack.links(k, raw):
                if s['area'][o] in done:
                    continue
                x, y = (k, o) if k < o else (o, k)
                band = m['bands'][bi]
                los_band = band['kind'] == 'los'
                L.append((x, y, names[bi], round(d, 6), int(los), *(round(lo[f], 4) for f in
                          ('H10', 'H50', 'H90', 'V10', 'V50', 'V90', 'C')),
                          round(lo['DRY'], 4) if los_band else None, round(lo['HUMID'], 4) if los_band else None))
                if with_margins:
                    mg = margins(band, lo)
                    M.extend((x, y, names[bi], p, round(v, 4)) for p, v in mg.items())
                    G.extend((x, y, names[bi], w, g) for w, g in groups(band, mg).items())
        db.executemany('INSERT OR IGNORE INTO links VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', L)
        if with_margins:
            db.executemany('INSERT OR IGNORE INTO margins VALUES (?, ?, ?, ?, ?)', M)
            db.executemany('INSERT OR IGNORE INTO groups VALUES (?, ?, ?, ?, ?)', G)
        done.add(ai)
        rows += len(L)
        print(f'  {a["id"]}: {len(L):,} rows ({time.time() - t0:.0f} s)', file=sys.stderr)
    db.execute('CREATE INDEX links_b ON links (b)')
    db.commit()
    db.close()
    os.replace(tmp, path)
    return rows


def write_csv(pack, area_id, folder):
    """Every link of the area's summits, from each of them (a pair inside the area appears twice, once per end)."""
    m, s = pack.m, pack.s
    ai = next(i for i, a in enumerate(m['areas']) if a['id'] == area_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f'{area_id}.csv'
    with open(path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['summit', 'other', 'band', 'km', 'los', 'h10', 'h50', 'h90', 'v10', 'v50', 'v90', 'canopy_db',
                    'dry_db', 'humid_db'])
        for k, raw in pack.records(ai):
            for o, bi, los, d, codes, lo in pack.links(k, raw):
                los_band = m['bands'][bi]['kind'] == 'los'
                w.writerow([s['ref'][k], s['ref'][o], m['bands'][bi]['name'], f'{d:.3f}', int(los)]
                           + [f'{lo[x]:.2f}' for x in ('H10', 'H50', 'H90', 'V10', 'V50', 'V90', 'C')]
                           + ([f'{lo["DRY"]:.2f}', f'{lo["HUMID"]:.2f}'] if los_band else ['', '']))
    return path


def check_vectors(pack, path):
    """Compares the decoder with test vectors (data/testvectors/v1.json of VHF+ S2S): losses and margins to 1e-6 dB,
    groups exactly. Uses the area files in the pack folder."""
    t = json.loads(Path(path).read_text())
    bad = n = 0
    names = [b['name'] for b in pack.m['bands']]
    for v in t['summits']:
        k = v['number']
        if not (pack.dir / pack.m['areas'][pack.s['area'][k]]['file']).exists():
            continue
        raw = pack.record(k)
        if hashlib.sha256(raw).hexdigest() != v['sha256']:
            print(f'FAIL {v["ref"]}: record differs'); bad += 1; continue
        got = {(o, names[bi]): (lo, codes, d) for o, bi, los, d, codes, lo in pack.links(k, raw)}
        for p in v['pairs']:
            for b, e in p['bands'].items():
                n += 1
                lo, codes, d = got[(p['other'], b)]
                band = pack.m['bands'][names.index(b)]
                mg = margins(band, lo)
                gr = groups(band, mg)
                ok = (codes == e['codes'] and abs(d - p['km']) < 1e-6
                      and all(abs(lo[x] - y) < 1e-6 for x, y in e['loss'].items())
                      and all(abs(mg[x] - y) < 1e-6 for x, y in e['margin'].items())
                      and (gr == e['group'] if isinstance(e['group'], dict) else gr['average'] == e['group']))
                if not ok:
                    bad += 1
                    print(f'FAIL {v["ref"]} - {p["other_ref"]} {b}')
    sig = t.get('signature')
    if sig and not ed25519_verify(base64.b64decode(sig['public_key']), base64.b64decode(sig['message']),
                                  base64.b64decode(sig['signature'])):
        bad += 1
        print('FAIL the Ed25519 vector')
    print(f'{"OK" if not bad else "FAIL"} {n - bad} of {n} pair-bands match the test vectors' + (', and the Ed25519 vector' if sig and not bad else ''))
    return not bad


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--areas', nargs='+', metavar='ID', help='area ids or patterns (W6, "W7*", HB-N45E005)')
    ap.add_argument('--all', action='store_true', help='every area (about 66 MB)')
    ap.add_argument('--list', action='store_true', help='list the areas and stop')
    ap.add_argument('--dir', default='s2s-data', help='where the files and s2s.sqlite go (default s2s-data)')
    ap.add_argument('--host', action='append', help='a host root to use instead of the built-in ones (repeatable)')
    ap.add_argument('--csv', action='store_true', help='also write csv/<area>.csv')
    ap.add_argument('--margins', action='store_true', help='also every preset\'s margin and the group letters')
    ap.add_argument('--no-sqlite', action='store_true', help='only download (and --csv)')
    ap.add_argument('--offline', action='store_true', help='use the files already in --dir/v1, no network')
    ap.add_argument('--check-vectors', metavar='JSON', help='check the decoder against test vectors, then stop')
    ap.add_argument('--skip-signature', action='store_true', help=argparse.SUPPRESS)   # local test builds only
    a = ap.parse_args()
    folder = Path(a.dir) / f'v{FORMAT}'
    fetch = Fetch(a.host or HOSTS)

    if a.check_vectors:
        sys.exit(0 if check_vectors(Pack(folder), a.check_vectors) else 1)
    if a.offline:
        if not (folder / 'manifest.json').exists():
            raise SystemExit(f'No {folder / "manifest.json"} yet: run once without --offline')
        m = json.loads((folder / 'manifest.json').read_text())
    else:
        m = refresh(fetch, folder, a.skip_signature)
    if a.list or not (a.areas or a.all):
        print(f'Data of {m["built"]}, {len(m["areas"])} areas, {m["total_bytes"] / 1e6:.1f} MB in all')
        for x in m['areas']:
            print(f'  {x["id"]:<12} {x["summits"]:>6} summits {x["bytes"] / 1e6:>7.2f} MB')
        if not a.list:
            print('\nChoose with --areas ID ... or --all (see --help).')
        return
    ids = [x['id'] for x in m['areas']]
    chosen = set(ids) if a.all else {i for pat in a.areas for i in ids if fnmatch.fnmatchcase(i, pat)}
    unknown = [p for p in (a.areas or []) if not any(fnmatch.fnmatchcase(i, p) for i in ids)]
    if unknown:
        raise SystemExit(f'No area matches {unknown}; see --list')
    if not a.offline:
        download(fetch, folder, m, chosen)
    pack = Pack(folder)
    if not a.no_sqlite:
        path = Path(a.dir) / 's2s.sqlite'
        rows = write_sqlite(pack, chosen, path, a.margins, fetch.host or 'local files')
        print(f'{path}: {rows:,} rows (pairs x bands) for {len(chosen)} areas, data of {m["built"]}')
    if a.csv:
        for i in sorted(chosen):
            print(write_csv(pack, i, Path(a.dir) / 'csv'))
    print(f'License: {LICENCE}\nCredits: {CREDITS}', file=sys.stderr)


if __name__ == '__main__':
    main()
