import Foundation
import IOBluetooth

// Protokół Sony MDR (Sound Connect) — port sony_mdr.py.
// Format pakietów na podstawie mos9527/SonyHeadphonesClient (MIT):
//   0x3E escape( type | seq | len:int32BE | payload | checksum ) 0x3C

enum SoundMode: String, CaseIterable {
    case nc, ambient, off

    var label: String {
        switch self {
        case .nc: return "Redukcja szumów"
        case .ambient: return "Otoczenie"
        case .off: return "Wyłączone"
        }
    }

    var symbol: String {
        switch self {
        case .nc: return "headphones"
        case .ambient: return "ear"
        case .off: return "circle.slash"
        }
    }
}

struct NCState: Equatable {
    var mode: SoundMode? = nil
    var level: Int = 10          // poziom otoczenia 1–20
    var voice = false            // skupienie na głosie
    var auto: UInt8 = 0          // v2 0x19: auto ambient
    var sens: UInt8 = 0          // v2 0x19: czułość adaptacji
    var ncMode: UInt8 = 0        // 0 = NC, 1 = ASM
    var v1NcType: UInt8 = 0x02
}

enum MDRFrame {
    static let start: UInt8 = 0x3E, end: UInt8 = 0x3C, esc: UInt8 = 0x3D

    static func escape(_ data: [UInt8]) -> [UInt8] {
        var out: [UInt8] = []
        out.reserveCapacity(data.count + 4)
        for b in data {
            if b == 0x3C || b == 0x3D || b == 0x3E {
                out.append(esc)
                out.append(b &- 0x10)
            } else {
                out.append(b)
            }
        }
        return out
    }

    static func unescape(_ data: [UInt8]) -> [UInt8]? {
        var out: [UInt8] = []
        var i = 0
        while i < data.count {
            if data[i] == esc {
                guard i + 1 < data.count else { return nil }
                out.append(data[i + 1] &+ 0x10)
                i += 2
            } else {
                out.append(data[i])
                i += 1
            }
        }
        return out
    }

    static func pack(type: UInt8, seq: UInt8, payload: [UInt8]) -> [UInt8] {
        let n = UInt32(payload.count)
        var body: [UInt8] = [type, seq,
                             UInt8((n >> 24) & 0xFF), UInt8((n >> 16) & 0xFF),
                             UInt8((n >> 8) & 0xFF), UInt8(n & 0xFF)]
        body.append(contentsOf: payload)
        body.append(body.reduce(UInt8(0)) { $0 &+ $1 })
        return [start] + escape(body) + [end]
    }

    /// `frame` bez znaczników start/end.
    static func unpack(_ frame: [UInt8]) -> (type: UInt8, seq: UInt8, payload: [UInt8])? {
        guard let raw = unescape(frame), raw.count >= 7 else { return nil }
        let sum = raw.dropLast().reduce(UInt8(0)) { $0 &+ $1 }
        guard sum == raw[raw.count - 1] else { return nil }
        let size = Int(raw[2]) << 24 | Int(raw[3]) << 16 | Int(raw[4]) << 8 | Int(raw[5])
        let payload = Array(raw[6..<(raw.count - 1)])
        guard payload.count == size else { return nil }
        return (raw[0], raw[1], payload)
    }
}

/// Klient MDR na kanale RFCOMM (IOBluetooth). Wszystkie callbacki na głównym wątku.
final class MDRClient: NSObject, IOBluetoothRFCOMMChannelDelegate {
    static let uuidV2 = "956C7B26-D49A-4BA8-B03F-B17D393CB6E2"   // XM5 i nowsze
    static let uuidV1 = "96CC203E-5068-46AD-B32D-E316F5E069BA"   // XM4 i starsze

    private static let DATA_MDR: UInt8 = 0x0C, DATA_MDR_NO2: UInt8 = 0x0E, ACK: UInt8 = 0x01
    private static let NCASM_GET: UInt8 = 0x66, NCASM_RET: UInt8 = 0x67, NCASM_SET: UInt8 = 0x68, NCASM_NTFY: UInt8 = 0x69
    private static let V2_DUAL: UInt8 = 0x17, V2_DUAL_NA: UInt8 = 0x19, V1_NC_ASM: UInt8 = 0x02

    let proto: MDRProto
    private(set) var state = NCState()
    private(set) var variant: UInt8?

    var onState: ((NCState) -> Void)?
    var onBattery: ((Int, Bool) -> Void)?
    var onReady: ((Bool) -> Void)?        // true = sterowanie ANC dostępne
    var onClosed: (() -> Void)?

    private var channel: IOBluetoothRFCOMMChannel?
    private var seq: UInt8 = 0
    private var queue: [[UInt8]] = []
    private var inflight: [UInt8]?
    private var retries = 0
    private var ackTimer: Timer?
    private var rx: [UInt8] = []
    private var gotProtocolInfo = false
    private var ready = false
    private var probeVariants: [UInt8] = []
    private var probeTimer: Timer?

    init(proto: MDRProto) {
        self.proto = proto
        super.init()
    }

    // MARK: połączenie

    /// Otwiera kanał RFCOMM usługi Sony. Zwraca opis błędu albo nil.
    func connect(_ device: IOBluetoothDevice) -> String? {
        let uuidString = proto == .v1 ? Self.uuidV1 : Self.uuidV2
        guard let uuid = UUID(uuidString: uuidString) else { return "zły UUID" }
        var bytes = withUnsafeBytes(of: uuid.uuid) { Array($0) }
        let sdp: IOBluetoothSDPUUID? = IOBluetoothSDPUUID(bytes: &bytes, length: 16)
        guard let sdpUUID = sdp else { return "SDP UUID" }
        let rec: IOBluetoothSDPServiceRecord? = device.getServiceRecord(for: sdpUUID)
        guard let record = rec else {
            return "brak usługi Sony (\(uuidString)) na urządzeniu"
        }
        var channelID: BluetoothRFCOMMChannelID = 0
        guard record.getRFCOMMChannelID(&channelID) == kIOReturnSuccess else { return "brak kanału RFCOMM" }
        var ch: IOBluetoothRFCOMMChannel? = nil
        let r = device.openRFCOMMChannelAsync(&ch, withChannelID: channelID, delegate: self)
        guard r == kIOReturnSuccess else { return String(format: "openRFCOMMChannelAsync 0x%08x", r) }
        channel = ch
        Log.info("MDR: otwieram RFCOMM kanał \(channelID) (\(proto == .v1 ? "v1" : "v2"))")
        return nil
    }

    func close() {
        ackTimer?.invalidate()
        probeTimer?.invalidate()
        if let ch = channel {
            _ = ch.setDelegate(nil)
            _ = ch.closeChannel()
        }
        channel = nil
    }

    // MARK: API

    func requestBattery() {
        guard ready || gotProtocolInfo else { return }
        if proto == .v1 {
            send([0x10, 0x00])
        } else {
            send([0x22, 0x00])
            send([0x22, 0x08])   // wariant "with threshold" (zależnie od modelu)
        }
    }

    func setMode(_ mode: SoundMode, level: Int? = nil, voice: Bool? = nil) {
        if let level { state.level = max(1, min(20, level)) }
        if let voice { state.voice = voice }
        if mode != .off { state.ncMode = mode == .nc ? 0 : 1 }
        let effect: UInt8 = mode == .off ? 0 : 1

        if proto == .v1 {
            // v1 (XM4): NC = ncValue DUAL + asmValue 0; otoczenie = ncValue OFF + asmValue = poziom
            let nc = state.ncMode == 0
            send([Self.NCASM_SET, Self.V1_NC_ASM, effect, state.v1NcType,
                  nc ? 0x02 : 0x00, 0x01, state.voice ? 1 : 0, nc ? 0 : UInt8(max(1, state.level))])
        } else {
            let v = variant ?? Self.V2_DUAL
            var p: [UInt8] = [Self.NCASM_SET, v, 0x01, effect, state.ncMode,
                              state.voice ? 1 : 0, UInt8(state.level)]
            if v == Self.V2_DUAL_NA { p += [state.auto, state.sens] }
            send(p)
        }
        state.mode = mode
        onState?(state)
    }

    // MARK: wysyłanie z ACK

    private func send(_ payload: [UInt8]) {
        queue.append(payload)
        pump()
    }

    private func pump() {
        guard inflight == nil, !queue.isEmpty else { return }
        inflight = queue.removeFirst()
        retries = 0
        transmit()
    }

    private func transmit() {
        guard let p = inflight else { return }
        write(MDRFrame.pack(type: Self.DATA_MDR, seq: seq, payload: p))
        ackTimer?.invalidate()
        ackTimer = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: false) { [weak self] _ in
            self?.ackTimeout()
        }
    }

    private func ackTimeout() {
        retries += 1
        if retries < 3 {
            transmit()
        } else {
            Log.info("MDR: brak ACK dla \(hex(inflight ?? []))")
            inflight = nil
            pump()
        }
    }

    private func write(_ bytes: [UInt8]) {
        guard let ch = channel else { return }
        var buf = bytes
        let r = buf.withUnsafeMutableBytes { ptr -> IOReturn in
            ch.writeSync(ptr.baseAddress, length: UInt16(ptr.count))
        }
        if r != kIOReturnSuccess { Log.info(String(format: "MDR: writeSync 0x%08x", r)) }
    }

    // MARK: IOBluetoothRFCOMMChannelDelegate

    func rfcommChannelOpenComplete(_ rfcommChannel: IOBluetoothRFCOMMChannel!, status error: IOReturn) {
        guard error == kIOReturnSuccess else {
            Log.info(String(format: "MDR: otwarcie kanału nieudane 0x%08x", error))
            onReady?(false)
            return
        }
        channel = rfcommChannel
        Log.info("MDR: kanał otwarty, MTU \(rfcommChannel.getMTU())")
        send([0x00, 0x00])   // CONNECT_GET_PROTOCOL_INFO
        probeTimer = Timer.scheduledTimer(withTimeInterval: 4, repeats: false) { [weak self] _ in
            guard let self, !self.gotProtocolInfo else { return }
            Log.info("MDR: brak CONNECT_RET_PROTOCOL_INFO")
            self.onReady?(false)
        }
    }

    func rfcommChannelData(_ rfcommChannel: IOBluetoothRFCOMMChannel!, data dataPointer: UnsafeMutableRawPointer!, length dataLength: Int) {
        guard let dataPointer, dataLength > 0 else { return }
        let buf = UnsafeBufferPointer(start: dataPointer.assumingMemoryBound(to: UInt8.self), count: dataLength)
        rx.append(contentsOf: buf)
        parse()
    }

    func rfcommChannelClosed(_ rfcommChannel: IOBluetoothRFCOMMChannel!) {
        Log.info("MDR: kanał zamknięty")
        channel = nil
        ackTimer?.invalidate()
        probeTimer?.invalidate()
        onClosed?()
    }

    // MARK: odbiór

    private func parse() {
        while true {
            guard let s = rx.firstIndex(of: MDRFrame.start) else { rx.removeAll(); return }
            guard let e = rx[(s + 1)...].firstIndex(of: MDRFrame.end) else {
                if s > 0 { rx.removeFirst(s) }
                return
            }
            let frame = Array(rx[(s + 1)..<e])
            rx.removeFirst(e + 1)
            if let pkt = MDRFrame.unpack(frame) {
                handle(pkt.type, pkt.seq, pkt.payload)
            } else {
                Log.info("MDR: zły pakiet \(hex(frame))")
            }
        }
    }

    private func handle(_ type: UInt8, _ sq: UInt8, _ p: [UInt8]) {
        if type == Self.ACK {
            seq = sq                     // następny pakiet idzie z numerem z ACK
            ackTimer?.invalidate()
            inflight = nil
            pump()
            return
        }
        if type == Self.DATA_MDR || type == Self.DATA_MDR_NO2 {
            write(MDRFrame.pack(type: Self.ACK, seq: 1 &- sq, payload: []))
        }
        guard type == Self.DATA_MDR, let cmd = p.first else { return }

        switch cmd {
        case 0x01:   // CONNECT_RET_PROTOCOL_INFO
            if !gotProtocolInfo {
                gotProtocolInfo = true
                probeTimer?.invalidate()
                requestBattery()
                probeVariants = proto == .v1 ? [Self.V1_NC_ASM] : [Self.V2_DUAL_NA, Self.V2_DUAL]
                nextProbe()
            }
        case Self.NCASM_RET, Self.NCASM_NTFY:
            parseNC(p)
        case 0x23, 0x25:   // v2 POWER_RET/NTFY_STATUS
            if proto == .v2, p.count >= 3, p[1] == 0x00 || p[1] == 0x08 {
                onBattery?(Int(p[2]), p.count > 3 && p[3] == 0x01)
            }
        case 0x11, 0x13:   // v1 COMMON_RET/NTFY_BATTERY_LEVEL
            if proto == .v1, p.count >= 3, p[1] == 0x00 {
                onBattery?(Int(p[2]), p.count > 3 && p[3] == 0x01)
            }
        default:
            break
        }
    }

    private func nextProbe() {
        guard !probeVariants.isEmpty else {
            if !ready { Log.info("MDR: urządzenie nie odpowiedziało na NC/ASM"); onReady?(false) }
            return
        }
        let v = probeVariants.removeFirst()
        send([Self.NCASM_GET, v])
        probeTimer = Timer.scheduledTimer(withTimeInterval: 2, repeats: false) { [weak self] _ in
            self?.nextProbe()
        }
    }

    private func parseNC(_ p: [UInt8]) {
        if proto == .v1 {
            // [cmd, type, effect, ncType, ncValue, asmType, asmId, asmValue]
            guard p.count >= 8, p[1] == Self.V1_NC_ASM else { return }
            variant = p[1]
            let nc = p[4] != 0
            state.v1NcType = p[3]
            state.ncMode = nc ? 0 : 1
            state.voice = p[6] != 0
            state.mode = p[2] == 0 ? .off : (nc ? .nc : .ambient)
            if !nc, p[7] > 0 { state.level = Int(p[7]) }
        } else {
            // [cmd, type, status, effect, ncMode, ambientMode, level, (auto, sens)]
            guard p.count >= 7, p[1] == Self.V2_DUAL || p[1] == Self.V2_DUAL_NA else { return }
            variant = p[1]
            state.ncMode = p[4]
            state.voice = p[5] != 0
            state.level = Int(p[6])
            state.mode = p[3] == 0 ? .off : (p[4] == 0 ? .nc : .ambient)
            if p[1] == Self.V2_DUAL_NA, p.count >= 9 {
                state.auto = p[7]
                state.sens = p[8]
            }
        }
        if !ready {
            ready = true
            probeTimer?.invalidate()
            probeVariants = []
            Log.info(String(format: "MDR: gotowy, wariant NC/ASM 0x%02x", variant ?? 0))
            onReady?(true)
        }
        onState?(state)
    }
}

func hex(_ b: [UInt8]) -> String { b.map { String(format: "%02x", $0) }.joined() }
