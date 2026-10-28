import AppKit
import Combine
import ServiceManagement

final class AppDelegate: NSObject, NSApplicationDelegate {
    private let model = AppModel()
    private var notch: NotchController!
    private var bluetooth: BluetoothController!
    private var statusItem: NSStatusItem!
    private var modeItems: [SoundMode: NSMenuItem] = [:]
    private var modeMenuItem: NSMenuItem!
    private var loginItem: NSMenuItem!
    private var eqMenuItem: NSMenuItem!
    private var bag = Set<AnyCancellable>()
    private var mediaTimer: Timer?

    func applicationDidFinishLaunching(_ notification: Notification) {
        Log.info("Sony Popup start (macOS \(ProcessInfo.processInfo.operatingSystemVersionString))")

        notch = NotchController(model: model)
        bluetooth = BluetoothController(model: model)

        model.requestMode = { [weak self] mode, level in self?.bluetooth.setMode(mode, level: level) }
        model.requestEQPreset = { [weak self] id in self?.bluetooth.setEQPreset(id) }
        model.requestClearBass = { [weak self] v in self?.bluetooth.setClearBass(v) }
        model.mediaCommand = { [weak self] cmd in self?.media(cmd) }

        notch.onExpand = { [weak self] in
            self?.bluetooth.refreshBattery()
            self?.refreshMedia()
        }
        bluetooth.onNewConnection = { [weak self] in
            // pokaż wyspę po połączeniu słuchawek (bateria dojdzie po chwili)
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { self?.notch.expand(pinFor: 6) }
        }

        setupStatusItem()
        bluetooth.start()
        setupLoginItemOnFirstRun()

        // odtwarzacz: odświeżanie tylko przy rozwiniętej wyspie
        mediaTimer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in
            guard let self, self.model.expanded else { return }
            self.refreshMedia()
        }
    }

    // MARK: odtwarzacz

    private func refreshMedia() {
        Media.fetch { [weak self] np in self?.model.nowPlaying = np }
    }

    private func media(_ cmd: MediaCommand) {
        Media.send(cmd, via: model.nowPlaying?.app)
        if cmd == .playPause, var np = model.nowPlaying {   // od razu przełącz ikonę
            np.playing.toggle()
            model.nowPlaying = np
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) { [weak self] in self?.refreshMedia() }
    }

    // MARK: menu bar

    private func setupStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let b = statusItem.button {
            b.image = NSImage(systemSymbolName: "headphones", accessibilityDescription: "Sony Popup")
            b.imagePosition = .imageLeading
        }

        let menu = NSMenu()
        menu.addItem(withTitle: "Pokaż popup", action: #selector(showPopup), keyEquivalent: "").target = self

        let modeMenu = NSMenu()
        for m in SoundMode.allCases {
            let it = NSMenuItem(title: m.label, action: #selector(pickMode(_:)), keyEquivalent: "")
            it.target = self
            it.representedObject = m.rawValue
            modeMenu.addItem(it)
            modeItems[m] = it
        }
        modeMenuItem = NSMenuItem(title: "Tryb dźwięku", action: nil, keyEquivalent: "")
        modeMenuItem.submenu = modeMenu
        menu.addItem(modeMenuItem)

        eqMenuItem = NSMenuItem(title: "Equalizer", action: nil, keyEquivalent: "")
        eqMenuItem.submenu = NSMenu()
        eqMenuItem.isEnabled = false
        menu.addItem(eqMenuItem)

        menu.addItem(.separator())
        loginItem = NSMenuItem(title: "Uruchamiaj przy logowaniu", action: #selector(toggleLogin), keyEquivalent: "")
        loginItem.target = self
        menu.addItem(loginItem)
        menu.addItem(withTitle: "Pokaż log", action: #selector(openLog), keyEquivalent: "").target = self
        menu.addItem(.separator())
        menu.addItem(withTitle: "Zakończ", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        statusItem.menu = menu

        // tytuł: poziom baterii, zaznaczenie trybu
        model.$battery.combineLatest(model.$connected)
            .receive(on: RunLoop.main)
            .sink { [weak self] battery, connected in
                self?.statusItem.button?.title = connected ? (battery.map { " \($0)%" } ?? "") : ""
            }
            .store(in: &bag)
        model.$nc.combineLatest(model.$ncAvailable)
            .receive(on: RunLoop.main)
            .sink { [weak self] nc, ok in
                guard let self else { return }
                self.modeMenuItem.isEnabled = ok
                for (m, it) in self.modeItems { it.state = nc.mode == m ? .on : .off; it.isEnabled = ok }
            }
            .store(in: &bag)
        model.$eq.combineLatest(model.$ncAvailable)
            .receive(on: RunLoop.main)
            .sink { [weak self] eq, ok in self?.rebuildEQMenu(eq, enabled: ok) }
            .store(in: &bag)
        updateLoginItem()
    }

    private func rebuildEQMenu(_ eq: EQState, enabled: Bool) {
        let sub = NSMenu()
        for p in eq.presets {
            let it = NSMenuItem(title: p.name, action: #selector(pickEQ(_:)), keyEquivalent: "")
            it.target = self
            it.tag = Int(p.id)
            it.state = eq.preset == p.id ? .on : .off
            sub.addItem(it)
        }
        eqMenuItem.submenu = sub
        eqMenuItem.isEnabled = enabled && eq.available && !eq.presets.isEmpty
    }

    @objc private func pickEQ(_ sender: NSMenuItem) {
        bluetooth.setEQPreset(UInt8(clamping: sender.tag))
    }

    @objc private func showPopup() { notch.expand(pinFor: 6) }

    @objc private func pickMode(_ sender: NSMenuItem) {
        guard let raw = sender.representedObject as? String, let m = SoundMode(rawValue: raw) else { return }
        bluetooth.setMode(m, level: nil)
    }

    @objc private func openLog() {
        let url = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/SonyPopup.log")
        NSWorkspace.shared.open(url)
    }

    // MARK: autostart

    private func setupLoginItemOnFirstRun() {
        let key = "didSetupLoginItem"
        guard !UserDefaults.standard.bool(forKey: key) else { return }
        UserDefaults.standard.set(true, forKey: key)
        do { try SMAppService.mainApp.register() } catch { Log.info("Autostart: \(error.localizedDescription)") }
        updateLoginItem()
    }

    @objc private func toggleLogin() {
        do {
            if SMAppService.mainApp.status == .enabled {
                try SMAppService.mainApp.unregister()
            } else {
                try SMAppService.mainApp.register()
            }
        } catch {
            Log.info("Autostart: \(error.localizedDescription)")
        }
        updateLoginItem()
    }

    private func updateLoginItem() {
        loginItem?.state = SMAppService.mainApp.status == .enabled ? .on : .off
    }
}
