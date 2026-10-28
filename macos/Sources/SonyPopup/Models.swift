import AppKit

enum MDRProto { case v1, v2 }

/// Obsługiwane słuchawki — ta sama lista co w wersji na Windows (sony_popup.py: DEVICES).
struct HeadphoneModel: Equatable {
    let key: String
    let name: String
    let match: [String]
    let proto: MDRProto
    var eqAllowed: Set<UInt8>? = nil     // presety EQ przyjmowane przez model (nil = wszystkie)
}

let MODELS: [HeadphoneModel] = [
    // XM6 (FW 3.x) przyjmuje tylko część presetów — wg SonyHeadphonesClient
    .init(key: "wh-1000xm6", name: "WH-1000XM6", match: ["WH-1000XM6"], proto: .v2,
          eqAllowed: [0x00, 0x30, 0x31, 0x32, 0x33, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5]),
    .init(key: "wh-1000xm5", name: "WH-1000XM5", match: ["WH-1000XM5"], proto: .v2),
    .init(key: "wh-1000xm4", name: "WH-1000XM4", match: ["WH-1000XM4"], proto: .v1),
    .init(key: "wh-ult900n", name: "WH-ULT900N", match: ["WH-ULT900N", "ULT WEAR"], proto: .v2),
]

func headphoneModel(forName name: String?) -> HeadphoneModel? {
    let n = (name ?? "").lowercased()
    return MODELS.first { m in m.match.contains { n.contains($0.lowercased()) } }
}

private var imageCache: [String: NSImage] = [:]

/// Zdjęcie modelu: z .app (Contents/Resources) albo — przy `swift run` — z katalogu głównego repo.
func headphoneImage(_ m: HeadphoneModel) -> NSImage? {
    if let img = imageCache[m.key] { return img }
    var candidates: [URL] = []
    if let u = Bundle.main.url(forResource: m.key, withExtension: "png") { candidates.append(u) }
    let repoRoot = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent()   // SonyPopup
        .deletingLastPathComponent()   // Sources
        .deletingLastPathComponent()   // macos
        .deletingLastPathComponent()   // repo
    candidates.append(repoRoot.appendingPathComponent("\(m.key).png"))
    for u in candidates {
        if let img = NSImage(contentsOf: u) {
            imageCache[m.key] = img
            return img
        }
    }
    return nil
}
