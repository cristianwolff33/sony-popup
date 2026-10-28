import AppKit
import SwiftUI

/// Panel, który może być oknem kluczowym bez aktywowania aplikacji (hover i kliknięcia w SwiftUI).
final class IslandPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
}

/// Przezroczysty panel nad notchem. Zwinięty = czarny kształt notcha (niewidoczny),
/// po najechaniu kursorem na notch rozwija się w wyspę.
final class NotchController {
    private let model: AppModel
    private let panel: IslandPanel
    private var screen: NSScreen
    private var notch: NSRect = .zero
    private var timer: Timer?
    private var hoverSince: Date?
    private var outsideSince: Date?
    private var pinnedUntil: Date?

    /// Wołane przy rozwinięciu (odświeżenie baterii i odtwarzacza).
    var onExpand: (() -> Void)?

    private static let panelHeight: CGFloat = 420

    init(model: AppModel) {
        self.model = model
        screen = Self.preferredScreen()
        panel = IslandPanel(contentRect: .zero,
                            styleMask: [.borderless, .nonactivatingPanel],
                            backing: .buffered, defer: false)
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = false
        panel.isMovable = false
        panel.hidesOnDeactivate = false
        panel.isReleasedWhenClosed = false
        panel.acceptsMouseMovedEvents = true
        panel.ignoresMouseEvents = true
        panel.level = NSWindow.Level(rawValue: Int(CGWindowLevelForKey(.mainMenuWindow)) + 3)
        panel.collectionBehavior = [.canJoinAllSpaces, .stationary, .fullScreenAuxiliary, .ignoresCycle]

        let host = NSHostingView(rootView: IslandView(model: model))
        host.wantsLayer = true
        host.layer?.backgroundColor = .clear
        panel.contentView = host

        layout()
        panel.orderFrontRegardless()

        NotificationCenter.default.addObserver(forName: NSApplication.didChangeScreenParametersNotification,
                                               object: nil, queue: .main) { [weak self] _ in self?.layout() }
        timer = Timer.scheduledTimer(withTimeInterval: 1.0 / 30, repeats: true) { [weak self] _ in self?.tick() }
        RunLoop.main.add(timer!, forMode: .common)
    }

    // MARK: geometria

    private static func preferredScreen() -> NSScreen {
        NSScreen.screens.first { $0.safeAreaInsets.top > 0 } ?? NSScreen.main ?? NSScreen.screens[0]
    }

    /// Prostokąt notcha w globalnych współrzędnych; bez notcha — wirtualny obszar na środku paska menu.
    private static func notchRect(_ s: NSScreen) -> NSRect {
        let f = s.frame
        if s.safeAreaInsets.top > 0, let l = s.auxiliaryTopLeftArea, let r = s.auxiliaryTopRightArea {
            let h = s.safeAreaInsets.top
            return NSRect(x: f.minX + l.width, y: f.maxY - h, width: f.width - l.width - r.width, height: h)
        }
        let h = max(24, f.maxY - s.visibleFrame.maxY)
        return NSRect(x: f.midX - 100, y: f.maxY - h, width: 200, height: h)
    }

    private func layout() {
        screen = Self.preferredScreen()
        notch = Self.notchRect(screen)
        model.notchSize = notch.size
        let w = IslandView.width + 40     // zapas na "uszy" i cień
        let h = Self.panelHeight + notch.height
        panel.setFrame(NSRect(x: notch.midX - w / 2, y: screen.frame.maxY - h, width: w, height: h), display: true)
        Log.info("Notch: \(Int(notch.width))×\(Int(notch.height)) na ekranie \(screen.localizedName)")
    }

    /// Aktualny obszar rozwiniętej wyspy (do wykrywania wyjścia kursora).
    private func islandRect() -> NSRect {
        let h = notch.height + model.islandContentHeight
        return NSRect(x: notch.midX - IslandView.width / 2, y: screen.frame.maxY - h,
                      width: IslandView.width, height: h)
    }

    // MARK: hover

    private func tick() {
        let p = NSEvent.mouseLocation
        let f = screen.frame
        // notch + kilka pikseli zapasu; górna krawędź ekranu też się liczy
        let hot = p.x >= notch.minX - 8 && p.x <= notch.maxX + 8 && p.y >= notch.minY - 2 && p.y <= f.maxY + 1
        let now = Date()

        if !model.expanded {
            if hot {
                if let since = hoverSince {
                    if now.timeIntervalSince(since) > 0.12 { expand() }
                } else {
                    hoverSince = now
                }
            } else {
                hoverSince = nil
            }
            return
        }

        let inside = islandRect().insetBy(dx: -12, dy: -12).contains(p)
        if inside || hot {
            outsideSince = nil
            pinnedUntil = nil            // użytkownik przejął popup kursorem
            return
        }
        if let until = pinnedUntil, now < until { return }
        if let since = outsideSince {
            if now.timeIntervalSince(since) > 0.35 { collapse() }
        } else {
            outsideSince = now
        }
    }

    func expand(pinFor seconds: TimeInterval? = nil) {
        if let seconds { pinnedUntil = Date().addingTimeInterval(seconds) }
        outsideSince = nil
        guard !model.expanded else { return }
        model.expanded = true
        panel.ignoresMouseEvents = false
        panel.makeKeyAndOrderFront(nil)     // nonactivatingPanel: nie kradnie fokusu aplikacjom
        onExpand?()
    }

    func collapse() {
        hoverSince = nil
        outsideSince = nil
        pinnedUntil = nil
        guard model.expanded else { return }
        model.expanded = false
        panel.ignoresMouseEvents = true
        panel.resignKey()
        panel.orderFrontRegardless()
    }

    func toggle() {
        model.expanded ? collapse() : expand(pinFor: 6)
    }
}
