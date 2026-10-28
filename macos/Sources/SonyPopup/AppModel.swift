import AppKit
import Combine

struct NowPlaying: Equatable {
    var title: String
    var artist: String
    var playing: Bool
    var app: String          // "Spotify" / "Music" — sterowanie przez AppleScript
}

/// Stan współdzielony przez wyspę (SwiftUI), menu bar i kontroler Bluetooth.
final class AppModel: ObservableObject {
    @Published var expanded = false
    @Published var notchSize = CGSize(width: 200, height: 32)

    @Published var headphones: HeadphoneModel = MODELS[0]
    @Published var image: NSImage? = headphoneImage(MODELS[0])
    @Published var connected = false
    @Published var battery: Int? = nil
    @Published var charging = false

    @Published var ncAvailable = false
    @Published var ncConnecting = false
    @Published var nc = NCState()

    @Published var nowPlaying: NowPlaying? = nil
    @Published var mediaAvailable = true

    // akcje z UI
    var requestMode: ((SoundMode, Int?) -> Void)?
    var mediaCommand: ((MediaCommand) -> Void)?
}
