import AppKit
import IOBluetooth

/// Wykrywa połączenie obsługiwanych słuchawek i utrzymuje sterowanie MDR.
final class BluetoothController: NSObject {
    private let model: AppModel
    private var connectNote: IOBluetoothUserNotification?
    private var disconnectNotes: [String: IOBluetoothUserNotification] = [:]
    private var client: MDRClient?
    private var device: IOBluetoothDevice?
    private var generation = 0

    /// Wołane przy nowym połączeniu (nie przy starcie aplikacji) — pokazuje wyspę.
    var onNewConnection: (() -> Void)?

    init(model: AppModel) {
        self.model = model
        super.init()
    }

    func start() {
        connectNote = IOBluetoothDevice.register(forConnectNotifications: self,
                                                 selector: #selector(deviceConnected(_:device:)))
        let paired = (IOBluetoothDevice.pairedDevices() as? [IOBluetoothDevice]) ?? []
        let known = paired.filter { headphoneModel(forName: $0.name) != nil }
        Log.info("Sparowane słuchawki: \(known.map { $0.name ?? "?" })")
        for d in known where d.isConnected() {
            handle(d, isNew: false)
        }
    }

    @objc private func deviceConnected(_ note: IOBluetoothUserNotification, device: IOBluetoothDevice) {
        // powiadomienie przychodzi też dla urządzeń połączonych już przy rejestracji
        handle(device, isNew: true)
    }

    @objc private func deviceDisconnected(_ note: IOBluetoothUserNotification, device: IOBluetoothDevice) {
        let addr = device.addressString ?? ""
        note.unregister()
        disconnectNotes[addr] = nil
        guard addr == self.device?.addressString else { return }
        Log.info("Rozłączono: \(device.name ?? addr)")
        generation += 1
        client?.close()
        client = nil
        self.device = nil
        model.connected = false
        model.ncAvailable = false
        model.ncConnecting = false
        model.battery = nil
    }

    private func handle(_ d: IOBluetoothDevice, isNew: Bool) {
        guard let m = headphoneModel(forName: d.name) else { return }
        let addr = d.addressString ?? ""
        if addr == device?.addressString, client != nil { return }   // duplikat powiadomienia

        Log.info("Połączono: \(d.name ?? addr) → \(m.name)")
        generation += 1
        client?.close()
        client = nil
        device = d
        model.headphones = m
        model.image = headphoneImage(m)
        model.connected = true
        model.battery = nil
        model.ncAvailable = false
        model.ncConnecting = true
        if disconnectNotes[addr] == nil {
            disconnectNotes[addr] = d.register(forDisconnectNotification: self,
                                               selector: #selector(deviceDisconnected(_:device:)))
        }
        if isNew { onNewConnection?() }
        // profil A2DP/HFP musi się najpierw zestawić
        startControl(d, m, delay: isNew ? 3.0 : 0.5, attempt: 0, gen: generation)
    }

    private func startControl(_ d: IOBluetoothDevice, _ m: HeadphoneModel, delay: TimeInterval, attempt: Int, gen: Int) {
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
            guard let self, gen == self.generation, d.isConnected() else { return }
            let c = MDRClient(proto: m.proto)
            c.onState = { [weak self] s in self?.model.nc = s }
            c.onBattery = { [weak self] level, charging in
                self?.model.battery = level
                self?.model.charging = charging
            }
            c.onReady = { [weak self] ok in
                guard let self, gen == self.generation else { return }
                self.model.ncAvailable = ok
                if ok {
                    self.model.ncConnecting = false
                } else {
                    self.retry(d, m, attempt: attempt, gen: gen)
                }
            }
            c.onClosed = { [weak self] in
                guard let self, gen == self.generation else { return }
                self.model.ncAvailable = false
                self.client = nil
                if d.isConnected() { self.retry(d, m, attempt: attempt, gen: gen) }
            }
            if let err = c.connect(d) {
                Log.info("Sterowanie: próba \(attempt + 1) nieudana: \(err)")
                self.retry(d, m, attempt: attempt, gen: gen)
                return
            }
            self.client = c
        }
    }

    private func retry(_ d: IOBluetoothDevice, _ m: HeadphoneModel, attempt: Int, gen: Int) {
        client?.close()
        client = nil
        guard attempt < 2 else {
            model.ncConnecting = false
            model.ncAvailable = false
            return
        }
        startControl(d, m, delay: 6, attempt: attempt + 1, gen: gen)
    }

    func setMode(_ mode: SoundMode, level: Int?) {
        client?.setMode(mode, level: level)
    }

    func refreshBattery() {
        client?.requestBattery()
    }
}
