import AppKit
import ApplicationServices

enum MediaCommand { case previous, playPause, next }

/// Odtwarzacz: Spotify / Muzyka przez AppleScript (tytuł + sterowanie),
/// pozostałe aplikacje (np. YouTube w przeglądarce) przez systemowe klawisze multimedialne.
enum Media {
    private static let queue = DispatchQueue(label: "media", qos: .userInitiated)

    static func fetch(_ done: @escaping (NowPlaying?) -> Void) {
        queue.async {
            var result: NowPlaying? = nil
            for app in ["Spotify", "Music"] {
                let script = """
                if application "\(app)" is running then
                  tell application "\(app)"
                    if player state is stopped then return ""
                    return (player state as string) & "||" & (name of current track) & "||" & (artist of current track)
                  end tell
                end if
                return ""
                """
                guard let out = osascript(script), !out.isEmpty else { continue }
                let parts = out.components(separatedBy: "||")
                guard parts.count >= 3 else { continue }
                let np = NowPlaying(title: parts[1], artist: parts[2], playing: parts[0] == "playing", app: app)
                if np.playing || result == nil { result = np }
                if np.playing { break }
            }
            DispatchQueue.main.async { done(result) }
        }
    }

    static func send(_ cmd: MediaCommand, via app: String?) {
        if let app {
            let verb: String
            switch cmd {
            case .previous: verb = "previous track"
            case .playPause: verb = "playpause"
            case .next: verb = "next track"
            }
            queue.async { _ = osascript("tell application \"\(app)\" to \(verb)") }
        } else {
            switch cmd {
            case .previous: mediaKey(18)    // NX_KEYTYPE_PREVIOUS
            case .playPause: mediaKey(16)   // NX_KEYTYPE_PLAY
            case .next: mediaKey(17)        // NX_KEYTYPE_NEXT
            }
        }
    }

    /// Klawisz multimedialny — wymaga zgody w Ustawienia → Prywatność → Dostępność.
    private static func mediaKey(_ key: Int32) {
        let opts = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
        guard AXIsProcessTrustedWithOptions(opts) else {
            Log.info("Media: brak zgody na Dostępność — klawisze multimedialne nie zadziałają")
            return
        }
        for down in [true, false] {
            let flags = NSEvent.ModifierFlags(rawValue: down ? 0xA00 : 0xB00)
            let data1 = Int((key << 16) | ((down ? 0xA : 0xB) << 8))
            let ev = NSEvent.otherEvent(with: .systemDefined, location: .zero, modifierFlags: flags,
                                        timestamp: 0, windowNumber: 0, context: nil,
                                        subtype: 8, data1: data1, data2: -1)
            ev?.cgEvent?.post(tap: .cghidEventTap)
        }
    }

    private static func osascript(_ source: String) -> String? {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        p.arguments = ["-e", source]
        let out = Pipe()
        p.standardOutput = out
        p.standardError = Pipe()
        do { try p.run() } catch { return nil }
        p.waitUntilExit()
        guard p.terminationStatus == 0 else { return nil }
        let data = out.fileHandleForReading.readDataToEndOfFile()
        return String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}
