# qrpika-data

Fallback copy of the small QRPika datasets (VHF+ S2S links, RFI, manifests), served by GitHub Pages at
https://data.aj6x.com/. The primary host is https://data.qrpika.com/ (Cloudflare R2), which also holds the maps.

Files are content-hashed and listed in signed manifests (`<dataset>/v<format>/manifest.json`), so apps verify
whatever they read from either host. Crawlers are asked to stay out (`robots.txt`).
