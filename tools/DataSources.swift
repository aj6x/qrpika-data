// Where the app looks for the link pack, and whom it trusts: the one file to edit when the data is published
// (docs/DATA-FORMAT.md, "Hosting and fallback"). pipeline/sign.py reads the keys from here, so keep each as
// "id": "base64" on its own line.
import Foundation

public enum DataSources {
    /// The primary host's root: Cloudflare R2 (bucket `qrpika-data`) behind qrpika.com's Cloudflare DNS. The app adds
    /// the format folder (`v1/`) itself. docs/RELEASE-DATA.md.
    public static let primary = URL(string: "https://data.qrpika.com/s2s/")!

    /// Mirrors tried after the primary and the manifest's own `fallback_hosts`, in this order. Each serves the same
    /// files under the same `v<format>/` folder, on a different domain, DNS provider and service from the primary:
    /// GitHub Pages (repository aj6x/qrpika-data), aj6x.com's DNS at Namecheap. Hosts on `.invalid` are skipped.
    public static let fallbacks: [URL] = [
        URL(string: "https://data.aj6x.com/s2s/")!,
    ]

    /// Ed25519 public keys (base64 of the 32 raw bytes) by key id. manifest.json.sig names the key that signed; either
    /// release key is accepted. `primary` signs every publish; `backup` stays offline (paper or a USB stick) and signs only
    /// when the primary is lost or leaks; the app update after that adds a new backup. docs/DATA-FORMAT.md, "The signing keys".
    public static let releaseKeys: [String: String] = [
        "primary": "Rt3YrHclarBDasou1Qv0T/6Tf8btv+c47iWrcVOVS2A=",
        "backup": "mwx1PidxLD2c5MErQ+ORX6s8//tXmxX8NjP+JGLhwSo=",
    ]

    /// DEV ONLY: the throwaway pair in pipeline/dev-signing-key.txt (its private half is in the repo on purpose), for
    /// packs built and served on a Mac. Accepted by Debug builds only.
    public static let devKeys: [String: String] = [
        "dev": "D57YjvS0TCA/oW+H+wxmlhL/2v58hVo3IxW5NbBRn+A=",
    ]

    /// The keys this build trusts: the release keys, plus the dev key in Debug.
    public static var trustedKeys: [String: String] {
        #if DEBUG
        releaseKeys.merging(devKeys) { a, _ in a }
        #else
        releaseKeys
        #endif
    }

    /// The App Store page, for "Please update". PLACEHOLDER until the app has an App Store id.
    public static let appStore = URL(string: "https://apps.apple.com/app/id0000000000")!

    /// Per request, a manifest fetch gives up after this long and the next host is tried.
    public static let manifestTimeout: TimeInterval = 5
    /// Range reads and the summit index: a little longer, they carry data.
    public static let fileTimeout: TimeInterval = 10

    /// True for placeholder hosts (`.invalid`), which are never tried.
    public static func isPlaceholder(_ url: URL) -> Bool { url.host?.hasSuffix(".invalid") ?? true }
}
