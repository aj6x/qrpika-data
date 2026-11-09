# s2s: summit-to-summit links

The fallback copy of the VHF+ S2S summit-to-summit data, served at https://data.aj6x.com/s2s/v1/. The primary is
https://data.qrpika.com/s2s/v1/ (Cloudflare R2). Both hold the same files.

- `s2s/v1/`: the link pack, format 1. `manifest.json` lists every file with its size and SHA-256, and is signed
  (`manifest.json.sig`, Ed25519). Every other file name carries its hash.
- `s2s/s2s_fetch.py`: a reader. Python 3, standard library only. It downloads the areas you choose, checks them and
  writes SQLite or CSV: `python3 s2s_fetch.py --list`, then `python3 s2s_fetch.py --areas W6`.
- `s2s/DATA-FORMAT.md`: the format.

**License:** © 2026 G Blaj (AJ6X), [CC BY-NC-ND 4.0](https://creativecommons.org/licenses/by-nc-nd/4.0/), the
same as aj6x.com: copy and share it with credit to AJ6X, not commercially, and not changed. For anything beyond
that, ask. Sources: the SOTA summits list; terrain from the USGS 3D Elevation Program, Natural Resources Canada's
MRDEM and Copernicus GLO-30 (© DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018, provided under
COPERNICUS by the European Union and ESA); paths from NTIA's Irregular Terrain Model, gas from ITU-R P.676.

I build the data in the VHF+ S2S app repository and publish it here from there (`pipeline/publish.py`,
`docs/RELEASE-DATA.md`). Don't edit files here by hand.
