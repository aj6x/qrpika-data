# VHF+ S2S link pack, format 1

Path loss in dB for every summit pair and band of the Terrain runs, by area, for the app (`S2SKit`) to keep offline
or read by HTTP range. `pipeline/build.py` builds it from the run CSVs. `pipeline/fmt.py` has the reference encoder
and decoder (the app's is `S2SLinks/Codec.swift`), and `pipeline/verify.py` reads a built pack back the way the app
does. Measurements are in the last sections.

## Where it lives

Published at **https://data.qrpika.com/s2s/v1/** (Cloudflare R2), with a fallback copy at
**https://data.aj6x.com/s2s/v1/** (GitHub Pages, repository `aj6x/qrpika-data`). How I publish, and the budgets on
each: [RELEASE-DATA.md](RELEASE-DATA.md). Every app of the family reads it, and so does `s2s_fetch.py` (next to `v1/`
on both hosts), a standard-library Python reader that writes SQLite or CSV.

The app trusts no host as such: the manifest is signed, every file is checked against its hash, and the app falls
back from host to host and to what's on the phone (see "Hosting and fallback"). On the phone, packs go into the App
Group folder (`group.com.aj6x.pika`) under `packs/v<format>/`, by file name (hash), so an area downloaded by one app
serves the others.

This repo builds the pack (`pipeline/`) into the git-ignored `data/packs/`, and keeps the test vectors
(`data/testvectors/`) next to S2SKit's decoder: the format, the decoder and the vectors change together.

## Files

All under one directory per format, `v1/`. Every file but the manifest carries the first 10 hex digits of the
SHA-256 of its own bytes in its name, so a cached file never goes stale: a new build makes new names.

| File | What |
|---|---|
| `manifest.json` | The entry point, the only file without a hash: format, step, codec, bands, presets, the area list, the hosting fields. Fetch with no-cache. |
| `manifest.json.sig` | Its detached Ed25519 signature and the signing key's id (see "Hosting and fallback"). Fetch with no-cache. The only signed file: the others are covered by their SHA-256 in the manifest. |
| `summits-<hash>.json` | Every summit, in pack order: reference, name, position, height, activations, area, record size and record hash. 0.93 MB (about 0.4 MB gzipped on the wire). Also built into the app. |
| `model-<hash>.bin` | The codec's model: the frequency tables and predictor weights every record is decoded with. 1.91 MB (1.0 MB gzipped). A data update keeps it (only a new layout needs a new one), so a phone fetches it once. Also built into the app. |
| `links-<area>-<hash>.bin` | One area: a record per summit, concatenated in pack order, each coded on its own (one rANS stream). Each record holds **all** of its summit's links. |

The same file serves both uses: a free read takes one summit's record by HTTP range, and an area download takes the
whole file.

An **area** is a SOTA association. One whose file would pass 8 MB is cut into 5° tiles by the summits' positions,
named `<assoc>-<N|S><lat><E|W><lon>` after the tile's south-west corner: today only OE (3 tiles). 132 areas. The
largest files are HB, 5.8 MB, and OE-N45E010, 5.5 MB.

### Why one file per area

The other layout I measured had two files per area: an offline one storing each pair once (at its lower-numbered
end, with a list of the lower neighbors holding a summit's other links) and an online one with every summit's
record complete. One file, with complete records, wins:

- Hosting: with byte columns under deflate, 117.6 MB for the world instead of 216 MB (99.0 offline + 116.6 online +
  the index).
- Reading a summit from a downloaded area: one self-contained record instead of its own record plus every lower
  neighbor's (1.44 MB of records for W6/SC-213, 22 to 86 ms on the phone in Release).
- One decoder path for range reads, the recent cache and downloaded areas.
- The cost: downloads are bigger, as each pair is stored at both ends. Under deflate, California 3.44 MB instead of
  2.06, the world 117 MB instead of 99. The codec below more than makes it up.

### Why a codec of its own

A record's values are coded under context models with an rANS coder, not stored as byte columns under deflate. I
found the models in the codec lab ([CODEC-LAB.md](CODEC-LAB.md)), then built them.

- The world's records: **62.8 MB, against 116.6 MB** as byte columns under deflate (−46%); 65.7 MB hosted with the
  index and the model. California 1.86 MB against 3.44. One summit's read: 3.7 KB median against 7.0, 11.0 KB at the
  90th percentile against 20.2.
- Decoding: about 1.3 ms a record on an M-series Mac in Release (a record is about 47,000 symbols), against about
  0.1 ms to inflate a byte-column record. The app decodes one record per summit it opens, so it doesn't show.
- The model is fixed: tables and weights trained once on the world (a model trained on half the summits codes the
  other half within 0.4% of its own best). A data update reuses it, and any data codes under any model of the same
  layout, only a little larger.
- Integer arithmetic only, so the Python encoder, the app and `s2s_fetch.py` agree to the bit. `s2s_fetch.py` decodes
  in plain Python at about 75 ms a record.

### manifest.json

```json
{
 "format": 1, "built": "2026-11-08T22:16:53Z", "listed": "6 November 2026",
 "codec": "rans1",                 // one rANS stream per record under the model (section "A record")
 "model": "model-<hash>.bin", "model_bytes": 1905421, "model_sha256": "...",
 "summits": "summits-<hash>.json", "summits_bytes": 927065, "summits_sha256": "...",
 "e_offset_db": 2.0, "step_db": 1.0, "gas0": 2, "earth_radius_m": 6371008.8, "antenna_height_m": 2.0,
 "bands": [ {"name": "6m", "mhz": 52.525, "kind": "itm", "fields": ["e","s10","s90","pol","can"], "presets": [...]}, ...,
            {"name": "1.2cm", "mhz": 24048.1, "kind": "los", "fields": ["e","dry","hum"], "predict": {"dry": 167, "hum": 231}, "presets": [...]} ],
 "areas": [ {"id": "W6", "file": "links-W6-<hash>.bin", "bytes": 1861076, "sha256": "...", "summits": 605}, ... ],
 "total_bytes": 65667234,          // area files, the summit index and the model
 "base_url": null,                 // where the hashed files live, if not next to this manifest (a CDN); absolute or relative
 "fallback_hosts": ["https://data.aj6x.com/s2s/"],   // mirrors' roots; the app adds "v1/"
 "min_app": 1,                     // the oldest app build (CFBundleVersion) that may use this manifest
 "notice": null,                   // {"id": "dec-1-downtime", "text": "...", "url": "..."}: shown once per id
 "free_areas": ["W5N", "KH6"]      // free offline without the unlock; without it the app keeps its own list
}
```

`bands` is always the 11 bands in this order: 6m, 2m, 1.25m, 70cm, 23cm, 13cm, 6cm, 3cm (kind `itm`, ITM
predictions) and 1.2cm, 6mm, 4mm (kind `los`: line of sight only, the same loss at any time, with the gas of an
average, a dry and a humid day).

A preset is `{"id", "group", "label", "mode", "power_dbm", "gain_dbi", "threshold_dbm", "loss"}`, plus `"weather"`
on the los bands. `gain_dbi` is both ends together, and `loss` names the figure it uses (below). Its margin is

    margin = power_dbm + gain_dbi - loss - threshold_dbm

**Groups, as the web page has them:** the `group` letter of the first preset, in list order, whose margin is ≥ 0;
if none, `f` on 6m, 2m, 1.25m and 70cm, else `-`. On the los bands, groups are taken per weather day, with only
that day's presets. Letters: d duck, e easy, y yagi, l long, f far, - none.

### summits-\<hash\>.json

Columns, each an array of `count` values, index = the summit's pack number: `ref`, `name`, `lat`, `lon` (degrees, 4
decimals, the run's top of the summit), `z` (m), `activations`, `area` (index into the manifest's `areas`), `size`
(bytes of the summit's record). Plus `rec`, one base64 string: per summit in pack order, the first 8 bytes of the
SHA-256 of its record as stored, 101,696 bytes for the world, so a single range read from any host can be checked;
and `regions`, the SOTA region names by prefix, as on the web page.

Pack numbers follow a Hilbert curve over latitude and longitude, so summits near each other have near numbers. A
summit's record starts in its area file at the sum of `size` over the summits of the same area with a lower number.

## Reading a summit

A summit's record holds every link it has, cross-border ones included, so each pair is in the records of both its
ends (in two files when the ends are in two areas, coded the same). One read of `size` bytes, at the sum of `size`
over the area's lower-numbered summits, gives a summit everything: 3.7 KB median, 11.0 KB at the 90th percentile,
17 KB at most. The same offset reads it from a downloaded area file.

## A record

One rANS stream (codec `rans1`) over the summit's byte range, decoded under the model file (`model`). What it holds,
in decoding order:

```
n                                  links of this summit
per link k: the gap to o[k]        o[] the other summits, ascending; s this summit's number
per link:   los                    the two tops see each other (geometric line of sight, 4/3 earth)
for each band in manifest order:
  per link:  present               the band has this link
  then field by field, for the links present on the band, in link order:
    itm bands: e, s10, s90, pol, can
    los bands: e, dry, hum
```

Every value is a code of one byte (pol signed, −128..127; the rest 0..255), coded as its residual against a
prediction: d = the int8 wrap of value − pred, stored as the symbol zz(d) (zz(v) = 2v for v ≥ 0, −2v − 1 for v < 0;
unzz(u) = (u >> 1) ^ −(u & 1)), so value = pred + d wrapped back into the field's range. `pipeline/fmt.py` `walk()`
is the reference: it's the encoder and the decoder in one loop, so they can't drift apart. What follows is the same
in words.

### The coder

rANS with a 32-bit state x, byte-wise renormalization and 15-bit frequencies (ryg_rans's byte coder). The decoder
reads x from the record's first 4 bytes, little endian, and the rest forward:

```
symbol in a table:  slot = x & 32767
                    entry = the last whose cum <= slot (entries are stored most frequent first; cum in that order)
                    x = freq · (x >> 15) + slot − cum
                    while x < 2^23: x = (x << 8) | next byte
raw bits (k ≤ 15):  v = slot >> (15 − k); the same step with cum = v << (15 − k), freq = 1 << (15 − k).
                    More than 15 bits go as 15-bit chunks, high chunk first.
```

A record is good only if it ends with x = 2^23, every byte read, and no table failed: anything else is corrupt.

**Tables.** Each symbol belongs to a stream and a context, an integer `fine = coarse × K + rest` built from what's
already decoded (below). The model holds up to three tables for it: the fine one (the context exactly), the coarse
one (`coarse`), and order 0. The decoder tries them in that order, skipping a table the model doesn't hold; a table
that decodes the escape symbol (= the stream's alphabet size: 40 for gaps, 2 for bits, 256 for values) passes on to
the next. Order 0 holds every symbol, so every value can always be coded.

Streams: 0 gap, 1 los, 2 + band presence, 13 + band × 5 + field values (field in the band's `fields` order).

### What's coded, and its context

Per stream, `coarse` and `rest`, from values decoded earlier in the record, and K (fine = coarse × K + rest).
`bucket(x, edges)` = how many edges are ≤ x. `mag(u)` = bucket(u, [1, 2, 3, 5, 9, 17]) of the previous present link's
symbol on the same stream, and 7 for the band's first link. `L` = the link's los, `D` = its distance class.

| Stream | Context | K |
|---|---|---|
| n | 5 raw bits: c, the class of n + 1 (n + 1 = 2^c + m), then c raw bits: m | |
| gap | class of g + 1 (g + 1 = 2^c + m), then c raw bits: m. g = zigzag(o[0] − s) for the first link, o[k] − o[k−1] − 1 after. coarse: the previous gap's class, at most 20 (21 for the first), rest: bucket(n, [50, 100, 200, 400, 800, 1600, 3200]) | 8 |
| los | coarse: the previous link's los (2 for the first), rest: D | 10 |
| present | coarse: (before × 2 + before2) × 2 + L, before/before2 this link's presence on the band before and the one before that (1 when there isn't one); rest: prev × 10 + D, prev the previous link's presence on this band (2 for the first) | 30 |
| itm e | coarse: (mag × 2 + L) × 5 + case; rest: (D × 5 + act) × 6 + bucket(b, [5, 10, 20, 40, 80]) | 300 |
| itm s10 | coarse as e; rest: ((D × 5 + act) × 5 + lvl) × 9 + clip(dₑ) | 2250 |
| itm s90 | coarse as e; rest: (((D × 5 + act) × 5 + lvl) × 9 + clip(dₑ)) × 9 + clip(dₛ₁₀) | 20250 |
| itm pol, can | coarse as e; rest: lvl | 5 |
| los e | coarse: mag × 2 + L; rest: D | 10 |
| los dry, hum | coarse: mag × 2 + L; rest: D × 9 + clip(dₑ) | 90 |

Here, on an itm band, for the t-th link present (k) with q the one before it (t > 0):

- a = q's value on this band, b = k's value on its latest earlier itm band, c = q's on its latest earlier itm band,
  b2/c2 the same on the band before that, a2 = the value of the link two before on this band (a when t < 2); each 0
  when there's none.
- case: 0 if t = 0; else 1 if k was on no earlier itm band; else 2 if q was on none; else 4 if both were on two or
  more; else 3.
- act = bucket(|a − c|, [1, 2, 4, 8]); lvl = bucket(|b|, [1, 3, 6, 12]); dₑ, dₛ₁₀ = this link's residual d on e and
  s10 of this band, clip(d) = min(max(d, −4), 4) + 4.

**Distance class.** In 1e-4 degree integers (y = floor(lat × 10000 + 0.5), x the same for lon; the index has 4
decimals): dy = y_o − y_s; dx = |x_o − x_s|, and 3,600,000 − dx if over 1,800,000; dxs = (dx × cos[min((|y_s + y_o|
÷ 2) ÷ 1000, 900)]) >> 16 with the model's `cos_q16` table (÷ is integer division); d2 = dxs² + dy². D = bucket(d2,
`dk_edges`) (10 classes, edges at 10 to 400 km); ld = 16 × floor(log2 d2) + the next 4 bits of d2 (0 for d2 = 0).

### Predictions

| field | pred |
|---|---|
| itm, every field | the least-squares prediction of its group: (Σ wᵢ fᵢ + 2048) >> 12 with the model's integer weights for (stream, case, L, D), over the case's features: case 4: b, a, c, b2, c2, a2, ld, ldq, 1; 3: b, a, c, a2, ld, ldq, 1; 2: b, a, a2, ld, ldq, 1; 1: a, a2, ld, ldq, 1; 0: b, ld, 1 (ldq = q's ld, k's for t = 0). A group without weights predicts the first feature |
| e (los) | `last_los[k]`, k's e on the latest earlier los band (0 if none) |
| dry, hum (los) | `(max(e − gas0, 0) × K + 128) >> 8`, K = the model's `predict` (the band's `predict.dry` / `predict.hum`) |

Every prediction is clamped to the field's range (−128..127 for pol, 0..255 else) before the residual is taken.
After each itm band, every link present on it shifts its latest value into "the band before" and takes this band's
as its latest; after each los band, `last_los`.

### The model file

`model-<hash>.bin`, little endian: `S2SM`, u32 layout (1), u32 header length, the header (JSON: `scale_bits` 15,
`ls_shift` 12, `gas0`, `cos_q16` (901 values, cos of 0.05°, 0.15°, … × 65536), `dk_edges`, `predict` (per band
[0, dry, hum]), `ls_streams`, `weights` (rows of [group, w0..w8], group = (stream × 5 + case) × 20 + L × 10 + D),
`streams` (names)), then u32 table count and per table, by ascending key: u64 key = stream << 34 | level << 32 |
context (level 2 fine, 1 coarse, 0 order 0), u16 entries, and per entry u16 symbol, u16 frequency (the table's
frequencies add up to 32768). Trained by `pipeline/build.py` (`fmt.train`), kept in `data/models/`.

### From codes to dB

FS is the free space loss over the haversine distance d between the two summits' `lat`/`lon` (radius
`earth_radius_m`), at the band's `mhz`: `FS = 20 log10(4π d f / c)`, d in m, f in MHz, c = 299.792458. Use double
precision. With step = `step_db`:

itm bands (basic transmission loss in dB, antennas 2 m up; H horizontal, V vertical; H10 the loss bettered 10% of
the time, H90 the loss bettered 90% of the time):

    H50 = FS + e·step − 2
    H10 = H50 − s10·step
    H90 = H50 + s90·step
    V90 = H90 + pol·step         (pol signed)
    C   = can·step               extra loss under a 20 m canopy, V, 90%; can = 255: blocked
    V50 = H50 + pol·step, V10 = H10 + pol·step   (V's spread as H's: the runs have V50 and V10 on 2 m only, where this holds to 0.13 dB)

los bands:

    A     = FS + e·step − 2      average day (7.5 g/m³ of water vapor at sea level)
    DRY   = A − dry·step         2 g/m³
    HUMID = A + hum·step         15 g/m³
    V90 = H90 = H50 = H10 = A, C = 0

Preset `loss` names: `V90+C`, `H90`, `H50`, `H10`, `max(H90,V90)+C`, `H50+C`, `H10+C`, `A`, `DRY`, `HUMID`.

### Accuracy

Every decoded loss is within one step (1 dB) of the run's value, 99% within 0.66 dB. Codes are rounded to nearest,
each against the decoded figure it hangs on, so errors don't add up. Where that would move a pair across a preset's
threshold (about 2% of pair-bands at 1 dB), the codes are rounded the other way, so every preset's margin keeps its
sign by at least 0.0001 dB, and the groups are exactly the web page's. `S2S_STEP=0.5` builds the same format at
0.5 dB.

The largest e on any band is 126 of 255 steps over free space: room for headroom past today's limits.

## Hosting and fallback

The hosting can move or break without breaking installed apps: the app works from what's on the phone first, tries
several hosts, and trusts none of them, only the signature and the hashes. All of it is in the first version of the
app. Code: `S2SLinks/Hosts.swift` (host order, backoff, HTTP), `PackStore.swift` (the checks), `DataSources.swift`
(the one config file: hosts, keys, App Store link); `pipeline/sign.py` signs.

### Where the app looks, in order

0. **On the phone, always first.** Downloaded areas, the recent cache (20 summits), and the manifest, summit index and
   model: the saved copy, or the one built into the app (`ios/VHFS2S/PackSeed.bundle`, 2.9 MB, put there by
   `ios/tools/seed-pack.py`), whichever is newer. So a first launch with no signal has every summit to search and map,
   and only a summit's links need a host. When no host answers, the app keeps working on this and shows a quiet
   "Data from 8 Nov 2026" line.
1. **The last host that answered with a good manifest**, remembered on the phone, then **the primary**,
   `https://data.qrpika.com/s2s/` (Settings > Data host can change it). The manifest is fetched with no-cache and the
   saved ETag (a 304 confirms the copy in use), 5 s timeout.
2. **`base_url`** of the manifest, when set: where the hashed files are fetched first (a CDN). The manifest's own
   folder and every other host come after it, as every host carries the same names.
3. **Fallbacks**: `fallback_hosts` of the last good manifest (saved with it), then the ones built into the app
   (`DataSources.fallbacks`): today both are `https://data.aj6x.com/s2s/`.

A host that fails (timeout, HTTP error, bad signature, bad hash) rests: 15 s after the first failure, doubling up to
an hour, and the next one is tried. When every host is resting, the one whose rest ends first still gets one try, so
a summit tapped on a mountain isn't refused for an earlier failure. No connection at all (`notConnectedToInternet`
and the like) blames no host. Hosts are roots; the app adds `v<format>/`, the highest format it reads.

### Why any host can be trusted

- **The manifest is signed** with Ed25519, and nothing else is. The signature is detached, `manifest.json.sig` next to
  it, over the manifest's exact bytes:

  ```json
  {"key_id": "primary", "sig": "<base64 of the 64 byte signature>"}
  ```

  The app holds the public keys by id (`DataSources.releaseKeys`: `primary` and `backup`) and accepts a signature
  from either, checked with the key its `key_id` names. It refuses a manifest whose signature doesn't verify, keeps
  the copy it has, and tries the next host. A host whose manifest and signature don't match yet (mid-upload) is
  refused the same way, rests, and is asked again later.
- **Every file is checked against the signed manifest:** the summit index by `summits_sha256`, the model by
  `model_sha256`, an area download by its `sha256` and size, and a single range read by its size and the 8-byte
  record hash in the index (`rec`). A forged record can't match 8 bytes (64 bits) in practice, and they cost 0.13 MB
  in the index, so no read rests on "the record decoded cleanly". Records from the recent cache and downloaded areas
  are checked the same way.
- A newer manifest is taken only together with its summit index and model (the model is usually the one the phone
  has), so what's on the phone always fits together. A manifest older than the data in use (a mirror lagging behind)
  is passed over, and the next host is tried.

### Range support

A single summit is one range request. A host that answers it with 200 and the whole file is cut off at the headers
(the app reads the body only on 206), marked as not doing ranges, and not asked for single summits again; it's still
used for manifests and area downloads. Hosts must serve the `.bin` files as stored: no gzip on the way (the app asks
for `identity`), byte ranges, and, for the web page or any browser reader, CORS.

### Versions and updates

- The path carries the format (`/s2s/v1/`). **When a new format comes, keep the old one served for a while**, at
  least until the App Store version that reads the new one has been out a few months, since phones update slowly.
- `min_app`: the oldest app build (CFBundleVersion) that may use this manifest. An older app keeps the data it has
  and says "Please update", with an App Store link, once a day (counted when answered).
- `notice`, `{"id", "text", "url"?}`: a short message shown once per `id`, for example planned downtime.
- `pipeline/hosting.json` holds `base_url`, `fallback_hosts`, `min_app`, `notice` and `free_areas`, and
  `pipeline/build.py` writes them into the manifest. To change them without a rebuild, edit the file, run
  `python3 -B -m pipeline.sign --hosting data/packs/v1/manifest.json`, and sign again.

### The signing keys

Three key pairs, by id:

| Id | Signs | Private key lives | Public key |
|---|---|---|---|
| `primary` | every publish | my password manager, and the data repo's GitHub Actions secret `S2S_SIGNING_KEY` | in the app |
| `backup` | nothing, unless the primary is lost or leaks | offline only: on paper (the base64 line) and an offline USB stick, in two places | in the app |
| `dev` | local packs only | in this repo, `pipeline/dev-signing-key.txt`, public on purpose | Debug builds only |

A private key is the base64 of its 32 raw bytes, one line, so it fits a password manager entry, a GitHub secret or a
sheet of paper. Signing is its own step after the build and needs nothing but the key and the manifest, so it runs
on any Mac or in GitHub Actions. The pack build itself needs no key:

```bash
python3 -B -m pipeline.build                                          # unsigned manifest.json
S2S_SIGNING_KEY="$(pbpaste)" python3 -B -m pipeline.sign data/packs/v1/manifest.json  # key copied from Passwords; writes manifest.json.sig
python3 -B -m pipeline.sign data/packs/v1/manifest.json --key ~/.config/vhf-s2s/primary.key   # or from a file
python3 -B -m pipeline.sign data/packs/v1/manifest.json --dev         # a local pack for Debug builds
python3 -B -m pipeline.sign --check data/packs/v1/manifest.json       # which app key verifies it
```

`sign.py` refuses to sign with a key that isn't the app's key of that id. `ios/tools/seed-pack.py` only takes a
manifest signed by a release key, since the bundled copy ships to everyone.

**Making the pairs** (done once, 2026-11-07, on this Mac): `python3 -B -m pipeline.sign --new-pair primary` (and
`backup`) prints the private half for safekeeping and the public half for `DataSources.swift`. The current pairs are
in `~/.config/vhf-s2s/` (`primary.key`, `backup.key`, base64, mode 600; the `.pem` files are the same keys in PEM).
Where each copy goes, how to check it and when to delete them from this Mac: [RELEASE-DATA.md](RELEASE-DATA.md),
part A. To start over with new keys before the first release, make two new pairs and replace both public keys in
`DataSources.releaseKeys`.

**Signing:** on this Mac with the key from Passwords (`publish.py manifest --sign`), or, once set up
(RELEASE-DATA.md A4), in the data repository's Publish workflow (`pipeline/data-repo/publish.yml`, copied there by
`pipeline/publish.py` with `sign.py` and `DataSources.swift`). The workflow signs with the secret `S2S_SIGNING_KEY`,
under the id in the repository variable `S2S_KEY_ID` (default `primary`), then checks the signature against the
Release keys only, and every file's hash:
`python3 tools/sign.py --app-keys tools/DataSources.swift --check --release --files s2s/v1/manifest.json`.

**Key ids are names, not roles.** An id names one key for good and is never reused for another key, so every app
version agrees on what `"key_id": "backup"` means. "Primary" and "backup" describe how a key is used today; after a
recovery the key called `backup` may be the one that signs, and its new spare gets a new id (`backup-2`, `backup-3`, …).
`sign.py` refuses an id the app doesn't list.

**What an app does with a manifest's signature:**

| The signature | The app |
|---|---|
| verifies with the key its `key_id` names | uses the manifest |
| names a key the app holds, but doesn't verify | refuses it, rests the host, tries the next. No prompt. |
| names a `key_id` the app doesn't know | trusts **nothing** in it: not its data, files, hosts, `min_app` or `notice`. Keeps its local data and last verified manifest, and shows its own "Newer data is available, please update the app", at most once a day, with the App Store link built into the app. |

An unknown key id is what an older app sees after a key change: the new app version knows the key, the old one
doesn't. Since nothing from that manifest is shown or followed, a forged one can do no more than raise that prompt.

**If the primary is lost** (not leaked: nobody else has it):

1. Get the backup's base64 line from paper or the USB stick (check a typed copy with
   `python3 -B -m pipeline.sign --which-key -`). Put it in the data repository's `S2S_SIGNING_KEY` secret, set the
   repository variable `S2S_KEY_ID` to `backup`, and re-run Publish. Every installed app already holds `backup`, so
   they all take it; nothing else changes for users.
2. Make a new spare: `python3 -B -m pipeline.sign --new-pair backup-2`. Put its private half on paper and a USB
   stick, offline, as before.
3. In `DataSources.releaseKeys`, remove `primary` and add `backup-2`. Ship that app version.
4. Keep signing with `backup` (it's now the signing key: put it in the password manager and the GitHub secret).
   `backup-2` is the new offline spare. Installed apps of every version keep working throughout.

**If the primary leaks** (someone else may have it): the same steps, at once, plus:

1. Sign with `backup` right away, as above, and replace the GitHub secret.
2. Ship the app version without `primary` as soon as possible (step 3 above). Until a user has it, their app still
   trusts the leaked key. What the key allows: signing a manifest that a mirror or an attacker's host would serve.
   Every file in it must still match its hash, so it can point the app at forged data only from a host the attacker
   controls, and only for apps that reach that host. Keep an eye on the hosts meanwhile.
3. Optionally raise `min_app` to the new version once it's out, so older apps say "Please update".

**If the backup is lost or leaks** (the primary is fine): make `backup-2`, add it, remove `backup`, ship that app
version. Keep signing with `primary`.

**If both are lost:** installed apps can never take new data. They keep working on what they have. Ship an app
version with new keys under new ids (for example `primary-2`, `backup-3`) and sign with those: older apps then see
an unknown key id and show "Newer data is available, please update the app", which is the way out. Hence the paper
copy of the backup, kept apart from the primary.

**When changing keys on purpose** (no loss), add the new key to the app first, wait until most users have that
version, then start signing with it. Apps that haven't updated show the update prompt and keep their data.

**Dev builds:** `DataSources.devKeys` holds the dev public key, trusted only `#if DEBUG` (a unit test checks the
Release build leaves it out). Local packs for the simulator or a Debug build on the phone can be signed with
`--dev`. `ios/tools/serve-packs.py --min-app` / `--notice` re-sign with the dev key unless given another.

### Choosing the fallbacks

A fallback must be on a **different domain, DNS provider and service** from the primary, or it fails together with
it. Today the primary is Cloudflare R2 on qrpika.com (Cloudflare DNS), and the fallback is GitHub Pages on aj6x.com
(Namecheap DNS).

- A GitHub Pages `*.github.io` address is no fallback: once a custom domain is set, it redirects there, so a broken
  DNS record breaks both. R2's `*.r2.dev` is off (rate-limited, for testing only).
- Each host needs Range and no transcoding of `.bin`, and serves every file of `v1/` under the same names.
- A new fallback goes in `fallback_hosts` (it reaches every installed app with the next manifest) and in
  `DataSources.fallbacks` (for installs that never saw a manifest).
- **Publish order** on every host: the summit index, the model and the `.bin` files first (new hashed names, so
  nothing in use changes), then `manifest.json.sig` and `manifest.json`. Until both are up and match, the app refuses
  that host's manifest and keeps what it has, so a slow upload is safe. The previous build's files stay until the
  next publish, since phones may still read records by the old names. `pipeline/publish.py` does it in this order.

### Tested

`DataSourceTests.swift` (16 tests, a three-summit pack signed with a key made in the test, served by a fake network):
host order, backoff, a down primary and the last good host remembered, a bad signature, a key the app doesn't hold, a
bad hash on a range read and on a download, a host that ignores Range, every host down (bundled data, the recent
cache, offline blames no host), `min_app`, a newer manifest with a notice and fallbacks, ETag 304, a stale mirror,
`base_url`, and keys by id (the backup key, a signature under the wrong id, the dev key only in Debug, an unknown key
id trusting nothing but asking for an update, and a bad signature from a known key raising no prompt).

`ios/tools/serve-packs.py` acts out each failure for the app itself (`--down`, `--hang`, `--ignore-range`,
`--bad-signature`, `--unknown-key`, `--corrupt`, `--min-app N`, `--notice ID:TEXT`). Run several on other ports and
start the app with `-host http://localhost:8766/ -fallbacks "http://localhost:8765/"`.

## The RFI layer

An estimate of the field from licensed transmitters over each US summit's activation zone, by bin of spectrum, for
`S2SRFI` to turn into desense for any band, antenna and radio (docs/PHASE4.md has the model and how I built it).
`pipeline/rfi.py` builds it into the same folder, after the link pack, and the same signature covers it. The layer
has its own format number, 1. No station lists: a record holds fields per zone point and bin, and one source
position per bin.

| File | What |
|---|---|
| `rfi-index-<hash>.json` | `{"format": 1, "count", "number": [...], "area": [...], "size": [...], "rec": "<base64>"}`: the covered summits by pack number, ascending; each one's RFI area (index into `rfi.areas`), its record's size (0: quiet, nothing over the floor in the zone, no record) and the record's 8-byte SHA-256 prefix (zeros for size 0). A record starts in its area file at the sum of `size` over the area's lower-numbered summits. 73 KB. |
| `rfi-<area>-<hash>.bin` | One area (the link pack's area ids): the records of its covered summits, concatenated in pack order, each raw deflate. |

The manifest's `rfi` block:

```json
"rfi": {"format": 1, "codec": "deflate", "index": "rfi-index-<hash>.json", "index_bytes": 73371, "index_sha256": "...",
        "radius_km": 30, "floor_dbuv": 75.0, "base_dbuv": 60, "rx_height_m": 2.0, "zone_drop_m": 25.0,
        "built": "2026-11-08T03:25:44Z",
        "sources": {"uls": "2026-10-31", "uls_states": 59, "broadcast_states": ["AK", "CA", ...], "nwr": true},
        "bins": [{"lo": 0, "hi": 30}, {"lo": 30, "hi": 47}, ...],          // 54 bins of spectrum, MHz
        "areas": [{"id": "W6", "file": "rfi-W6-<hash>.bin", "bytes": 1279021, "sha256": "...", "summits": 605}, ...],
        "total_bytes": 5764145}
```

An app that doesn't know the block ignores it. The manifest's `built` is set to the layer's build time, so apps take
the new manifest.

Reading a summit: look its pack number up in the index (not there: not covered; size 0: quiet), read `size` bytes at
the offset in its RFI area file by range (or from the downloaded file, or the recent cache), check the 8-byte hash,
inflate. An area download takes the area's RFI file with its link file (`PackStore.download(area:)`), checked against
`sha256` and `bytes`.

### A record

Varints are unsigned LEB128 (7 bits a byte, low first, the high bit set on all but the last), zz the zigzag of
section "A record" above.

```
varint step                     grid spacing, m (10; 20, 40… for a large zone)
varint zz(x0), zz(y0)           the grid's south-west cell, in steps from the top (the top is cell 0, 0)
varint w, h                     columns and rows
varint zz(top_x), zz(top_y)     the terrain's top, m east and north of the summit's lat/lon in the summit index
                                (zone positions are m from this top; 111,320 m a degree, as the pipeline)
bitmap mask                     w·h bits, rows south to north, west to east; bit k in byte k/8, least significant first
byte   paging                   strongest paging station in sight at the top: field byte (below), 0 none
byte   paging_min               the same at the zone point where it's weakest
varint paging_khz               its frequency, kHz (0 none)
varint nb                       bins with anything over the floor in the zone
nb ×   byte bin                 index into rfi.bins
       varint khz               the bin's frequency: its strongest station at the top
       varint zz(dx), zz(dy), zz(dz)   the bin's dominant station (strongest at the most points): m east, north, and
                                its antenna's height over the top
nb ×   column of P bytes        P = the mask's set bits, in mask order; byte k = zz(v[k] − v[k−1]) mod 256 (v[−1] = 0),
                                v the field byte of the power sum of the bin's stations: dBµV/m − base_dbuv, 1 dB
                                steps, 0 under floor_dbuv
```

A zone point (column c, row r of the grid) is at x = (x0 + c)·step m east of the top and y = (y0 + r)·step m north.
The top is at lat + top_y/111320, lon + top_x/(111320·cos lat) of the summit index's position; a zone point at that
plus y/111320 and x/(111320·cos lat_top). `RFIZone.coordinate` does this, so the zone sits on a map. A field byte
b > 0 is b + base_dbuv dBµV/m. `pipeline/rfi.py` `decode()` is the reference, `S2SRFI/Zone.swift` the app's.

### Sizes (2026-11-08)

US: 3,254 summits, 3,176 with a record, 78 quiet. **6.09 MB** with the index. One summit's read: median 1.54 KB, 90th
percentile 3.7 KB, at most 12.1 KB. California (W6) 1.28 MB. Test vectors: `data/testvectors/rfi-v1.json`.

## Test vectors

`data/testvectors/v1.json`: 40 summits (W6/SC-213, W6/CT-006, G/LD-001 and random ones), 1,044 pairs and 6,361
pair-bands across every band, including canopy links and links to other areas. Per summit: pack number, area, byte
offset and size of its record in the area file, the SHA-256 of the record (`sha256`) and its link count, and `rec`,
the record's 8-byte hash as the index keeps it. Per pair: the other summit, `los`, `km`, and per band the codes, the
decoded losses, every preset's margin and the group. Losses and margins should match to 1e-6 dB, groups exactly.
`signature` is an Ed25519 check between pipeline/sign.py and CryptoKit: a throwaway key (not the signing key), a
message and its signature. `model` names the model the vectors were built with.

## Checked

`pipeline/verify.py` on the whole build (2026-11-08): the manifest's signature verifies with the app's key, and the
index, the 132 area files and all 12,712 records match their hashes. 132 areas, 12,712 summits, 7,905,952 pairs, each
in the records of exactly its two ends, with the same codes at both. 0 groups differing from the web page on all 11
bands and the dry and humid days (each pair graded once, 42.3 M pair-bands with the weather days). Largest error
1.00 dB (step 1 dB). The app's decoder (`TestVectorTests.swift`) decodes all 12,712 records, every one ending exactly
at its last byte, and every pair's codes agree at its two ends; `s2s_fetch.py` matches the 6,361 pair-bands of the
vectors.

## Sizes

1 dB, each pair at both ends, rANS under the model. "Read" is a free user's one range request for one summit, median /
90th percentile. Links and pair-bands count both ends.

| | Summits | Link ends | Pair-band ends | Today's web pack (groups) | This pack | Bytes / pair-band end | One summit's read |
|---|---|---|---|---|---|---|---|
| California (W6) | 605 | 340,334 | 2,015,793 | 0.86 MB | **1.86 MB** | 0.92 | 2.9 / 3.9 KB |
| US | 3,254 | 1,597,578 | 10,014,320 | 3.77 MB | 9.36 MB | 0.93 | 2.9 / 4.1 KB |
| Europe | 7,114 | 12,854,260 | 61,635,018 | 22.08 MB | 47.70 MB | 0.77 | 6.3 / 12.2 KB |
| World | 12,712 | 15,811,904 | 79,897,844 | 28.47 MB | **62.83 MB** | 0.79 | 3.7 / 11.0 KB |

Plus the 0.93 MB summit index and the 1.91 MB model: 65.7 MB hosted for the world. The target, California under
10 MB with all bands, is met 5× over.

What I measured against:
- Byte columns under deflate, one record per summit (the codec lab's baseline): California 3.44 MB, US 17.08, Europe
  87.89, world 116.62 MB, reads of 7.0 / 20.2 KB.
- The same byte columns with each pair stored once per area (an offline file): California 2.06 MB, US 12.84, Europe
  78.24, world 98.03 MB, but a free read of 0.70 / 4.00 MB for the world (a summit's record and its lower
  neighbors').

The codec and step tables below were measured on the byte columns with each pair once; the ratios between general
codecs hold with each pair at both ends.

### Codecs, world, the same records

| Codec | World | California | Note |
|---|---|---|---|
| none | 364.3 MB | 6.34 MB | |
| **deflate** (raw, level 9) | **98.0 MB** | **2.06 MB** | Native on iOS (Compression) and Android (java.util.zip). |
| zstd 19 | 87.6 MB | 1.88 MB | |
| zstd 19 + 112 KB trained dictionary | 85.8 MB | 1.80 MB | −12%, but needs libzstd built into the app on both platforms. |
| brotli 11 | 79.2 MB | 1.74 MB | −19%; iOS 15+ has it built in, Android doesn't; 10 min to build the world. |
| deflate of a whole area file (bulk) | 96.1 MB | 1.96 MB | Not worth losing per-summit reads. |

At first a custom coder didn't look worth it (order-0 entropy of the columns, about 60 MB at 1 dB with each pair
once). Context models made it worth it: with each pair at both ends, they code the world in 62.8 MB against
deflate's 116.6, a third below what brotli would do ([CODEC-LAB.md](CODEC-LAB.md)).

### What each step bought (world)

| Layout | World |
|---|---|
| 0.5 dB, both ends of each pair, plain bytes, by reference order, deflate | 198.6 MB |
| + delta from the band before (mod 256) | 145.3 MB |
| + Hilbert numbering | 137.7 MB |
| 0.5 dB, each pair once per area file (cross-border pairs in both areas), MED predictor | 116.2 MB (California 2.44 MB) |
| 1 dB, the same | 98.0 MB (California 2.06 MB) |
| 1 dB, each pair at both ends | 116.6 MB (California 3.44 MB) |
| **the same, context models and rANS (this format)** | **62.8 MB** (California 1.86 MB) |

Also measured, at 1 dB with each pair once (order-0 entropy of the columns, world): sorting a summit's links by
distance or bearing instead of by number made e cost 21.5–22.3 MB against 19.4 MB, and the spread more too; neighbors
by number (Hilbert) predict best. The MED predictor over band and neighbor took the spread from 16.2 to 13.0 MB,
against deltas from the band alone. The step: 1 dB 39 MB, 0.5 dB 51 MB, 0.25 dB 65 MB, 0.1 dB 87 MB of columns.

Areas: with each pair stored once, storing each cross-border pair in both areas cost 1.66× the pair-bands of storing
everything once (10° tiles 1.28×, 20° tiles 1.08×). With every pair at both ends, as here, areas cost nothing extra.

## What the runs hold, and what they don't

Per pair and band the app wanted median loss, the spread to 90% and 10% of the time, a line-of-sight flag, and the
canopy loss where it differs. From the run CSVs (`Terrain/data/s2s/<tag>-10[-<band>].csv`, joined as
`terrain/webpage.py` joins them):

- **Median and spread:** H50, H10, H90 on every ITM band. V only at 90% (V90), except 2 m, which also has V50 and V10.
  On 2 m, V's spread equals H's to 0.13 dB at most (median 0.00), so V50 and V10 are taken as H's spread on V90, and
  the pack stores only the V90 − H90 offset.
- **Line of sight:** only in the 2 m runs (`clear`). It's the same geometry on every band, and a pair missing from the
  2 m runs is never in sight (two tops in sight always pass 2 m's 190 dB limit), so the flag is complete.
- **Canopy:** V90_trees, 90% vertical only, on every ITM band; none on the los bands, where a path is kept only if 60%
  of the first Fresnel zone clears the canopy. The US and Canada runs add a 20 m canopy to bare earth (NED), so there
  it differs from V90 on 98% of pairs (median +0.8 dB). Everywhere else the runs use a surface model with the trees
  already in it, so V90_trees = V90 and C = 0. About 4% of North American pairs have V90_trees below V90 (by up to
  29 dB), which max(…, 0) drops, as the groups do.
- **Not carried:** ITM's `warnings` flags and 2 m's `blank_km` (path over no data). Either is a byte away if the app
  wants a "check this path" note.
- **Which pairs, and headroom:** the runs keep a pair on a band only when the band's biggest setup closes it 10% of
  the time ("far": 2 m 190 dB, two 6.75 dBi yagis, 5 W, CW). So every web page category is in, and pairs past "far"
  were never computed: bigger custom stations can't be evaluated beyond that edge without new runs. Pairs grow 1.2–1.45×
  per 10 dB of limit on the ITM bands (from the margins in the runs); the los bands barely change, as geometry limits
  them. **Headroom: none, as of 2026-11-07.** I decided on +20 dB over "far", then dropped it the same day: a
  California test on 2 m was too slow and its output too large. So the app treats "far" as the edge of the data: when
  a custom station still has margin at a pair's cut-off, it says "beyond the calculated range" rather than "not
  workable". The e byte keeps room for headroom later (largest 126 of 255), if a faster run makes it worth it.
- **Reach:** 6 m 1,000 km, the other ITM bands 750 km, line of sight 580 km. 1.25 m exists only for the US and Canada.
