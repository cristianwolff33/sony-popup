"""
Minimalny klient protokołu Sony MDR (Sound Connect): tylko NC/ASM.
v2 (WH-1000XM5/XM6, WH-ULT900N) i v1 (WH-1000XM4).
Format pakietów i komendy na podstawie mos9527/SonyHeadphonesClient (MIT), libmdr:
  ramka:   0x3E  escape( type | seq | len:int32BE | payload | checksum )  0x3C
  escape:  0x3C/0x3D/0x3E -> 0x3D 0x2C/0x2D/0x2E
  typy:    0x0C DATA_MDR (tabela 1), 0x01 ACK
"""
import ctypes
import logging
import socket
import threading
import time

log = logging.getLogger("sony_mdr")

SERVICE_UUID = "956C7B26-D49A-4BA8-B03F-B17D393CB6E2"  # MDR v2: XM5 i nowsze
SERVICE_UUID_V1 = "96CC203E-5068-46AD-B32D-E316F5E069BA"  # MDR v1: XM4, XM3 i starsze

DATA_MDR, ACK = 0x0C, 0x01
START, END, ESC = 0x3E, 0x3C, 0x3D

CONNECT_GET_PROTOCOL_INFO, CONNECT_RET_PROTOCOL_INFO = 0x00, 0x01
NCASM_GET_PARAM, NCASM_RET_PARAM, NCASM_SET_PARAM, NCASM_NTFY_PARAM = 0x66, 0x67, 0x68, 0x69
NC_DUAL_SEAMLESS = 0x17        # MODE_NC_ASM_DUAL_NC_MODE_SWITCH_AND_ASM_SEAMLESS
NC_DUAL_SEAMLESS_NA = 0x19     # ...z noise adaptation (auto ambient) — XM6
V1_NC_ASM = 0x02               # MDR v1: NOISE_CANCELLING_AND_AMBIENT_SOUND_MODE

MODE_NC, MODE_AMBIENT, MODE_OFF = "nc", "ambient", "off"


# ---------- ramkowanie ----------

def _escape(data: bytes) -> bytes:
    out = bytearray()
    for b in data:
        if b in (0x3C, 0x3D, 0x3E):
            out += bytes((ESC, b - 0x10))
        else:
            out.append(b)
    return bytes(out)


def _unescape(data: bytes) -> bytes:
    out, it = bytearray(), iter(data)
    for b in it:
        out.append(next(it) + 0x10 if b == ESC else b)
    return bytes(out)


def pack(dtype: int, seq: int, payload: bytes) -> bytes:
    body = bytes((dtype, seq)) + len(payload).to_bytes(4, "big") + payload
    body += bytes((sum(body) & 0xFF,))
    return bytes((START,)) + _escape(body) + bytes((END,))


def unpack(frame: bytes):
    """frame bez znaczników start/end. Zwraca (type, seq, payload) albo None."""
    raw = _unescape(frame)
    if len(raw) < 7 or (sum(raw[:-1]) & 0xFF) != raw[-1]:
        return None
    size = int.from_bytes(raw[2:6], "big")
    payload = raw[6:-1]
    if len(payload) != size:
        return None
    return raw[0], raw[1], payload


# ---------- Winsock RFCOMM po UUID usługi (bez ręcznego SDP) ----------

class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]


class _SOCKADDR_BTH(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("addressFamily", ctypes.c_ushort), ("btAddr", ctypes.c_ulonglong),
                ("serviceClassId", _GUID), ("port", ctypes.c_ulong)]


def _guid(s: str) -> _GUID:
    import uuid
    u = uuid.UUID(s)
    g = _GUID(u.time_low, u.time_mid, u.time_hi_version)
    g.Data4[:] = u.bytes[8:]
    return g


def rfcomm_connect(bt_addr: int, service_uuid: str = SERVICE_UUID, timeout=8.0) -> socket.socket:
    AF_BTH, BTHPROTO_RFCOMM = 32, 3
    s = socket.socket(AF_BTH, socket.SOCK_STREAM, BTHPROTO_RFCOMM)
    sab = _SOCKADDR_BTH(AF_BTH, bt_addr, _guid(service_uuid), 0)
    ws2 = ctypes.WinDLL("ws2_32")
    ws2.connect.argtypes = [ctypes.c_size_t, ctypes.c_void_p, ctypes.c_int]
    if ws2.connect(s.fileno(), ctypes.byref(sab), ctypes.sizeof(sab)) != 0:
        err = ws2.WSAGetLastError()
        s.close()
        raise OSError(err, f"RFCOMM connect failed (WSA {err})")
    s.settimeout(0.5)
    return s


# ---------- klient ----------

class SonyMDR:
    """
    on_state(dict) dostaje: {"mode": nc|ambient|off, "level": 0-20, "voice": bool}
    Wszystkie callbacki wołane z wątku czytającego.
    """

    def __init__(self, sock_factory, on_state=None, on_disconnect=None, proto="v2"):
        self._factory = sock_factory
        self.proto = proto
        self.on_state = on_state or (lambda s: None)
        self.on_disconnect = on_disconnect or (lambda: None)
        self.sock = None
        self.seq = 0
        self.variant = None
        self.state = {"mode": None, "level": 10, "voice": False,
                      "auto": 0, "sens": 0, "nc_mode": 0, "v1_nc_type": 0x02}
        self._ack = threading.Event()
        self._proto = threading.Event()
        self._nc = threading.Event()
        self._send_lock = threading.Lock()
        self._alive = False

    # --- połączenie ---
    def connect(self):
        self.sock = self._factory()
        self._alive = True
        threading.Thread(target=self._reader, daemon=True, name="mdr-reader").start()
        self._send(bytes((CONNECT_GET_PROTOCOL_INFO, 0x00)))
        if not self._proto.wait(4):
            raise TimeoutError("Brak CONNECT_RET_PROTOCOL_INFO")
        variants = (V1_NC_ASM,) if self.proto == "v1" else (NC_DUAL_SEAMLESS_NA, NC_DUAL_SEAMLESS)
        for variant in variants:
            self._nc.clear()
            self._send(bytes((NCASM_GET_PARAM, variant)))
            if self._nc.wait(2):
                break
        log.info("MDR gotowy, wariant NC/ASM=0x%02X, stan=%s", self.variant or 0, self.state)

    def close(self):
        self._alive = False
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None

    @property
    def connected(self):
        return self._alive

    # --- API ---
    def set_mode(self, mode, level=None, voice=None):
        st = self.state
        if level is not None:
            st["level"] = max(0, min(20, int(level)))
        if voice is not None:
            st["voice"] = bool(voice)
        if mode in (MODE_NC, MODE_AMBIENT):
            st["nc_mode"] = 0 if mode == MODE_NC else 1
        effect = 0 if mode == MODE_OFF else 1
        if self.proto == "v1":
            # v1 (XM4): NC = ncValue DUAL + asmValue 0; otoczenie = ncValue OFF + asmValue = poziom
            nc = st["nc_mode"] == 0
            payload = bytes((NCASM_SET_PARAM, V1_NC_ASM, effect, st["v1_nc_type"],
                             0x02 if nc else 0x00, 0x01, 1 if st["voice"] else 0,
                             0 if nc else max(1, st["level"])))
            self._send(payload)
            st["mode"] = mode
            self.on_state(dict(st))
            return
        variant = self.variant or NC_DUAL_SEAMLESS
        payload = bytes((NCASM_SET_PARAM, variant, 0x01, effect, st["nc_mode"],
                         1 if st["voice"] else 0, st["level"]))
        if variant == NC_DUAL_SEAMLESS_NA:
            payload += bytes((st["auto"], st["sens"]))
        self._send(payload)
        st["mode"] = mode
        self.on_state(dict(st))

    # --- wysyłanie z ACK ---
    def _send(self, payload: bytes, retries=3):
        with self._send_lock:
            for attempt in range(retries):
                self._ack.clear()
                self._raw(pack(DATA_MDR, self.seq, payload))
                if self._ack.wait(1.0):
                    return True
                log.warning("Brak ACK (%d/%d) dla %s", attempt + 1, retries, payload.hex())
            return False

    def _raw(self, data: bytes):
        log.debug("TX %s", data.hex())
        self.sock.sendall(data)

    # --- odbiór ---
    def _reader(self):
        buf = bytearray()
        try:
            while self._alive:
                try:
                    chunk = self.sock.recv(1024)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                buf += chunk
                while True:
                    s = buf.find(START)
                    if s < 0:
                        buf.clear()
                        break
                    e = buf.find(END, s + 1)
                    if e < 0:
                        del buf[:s]
                        break
                    frame = bytes(buf[s + 1:e])
                    del buf[:e + 1]
                    pkt = unpack(frame)
                    if pkt:
                        self._handle(*pkt)
                    else:
                        log.warning("Zły pakiet %s", frame.hex())
        except OSError as ex:
            log.info("RFCOMM zamknięty: %s", ex)
        finally:
            was = self._alive
            self.close()
            if was:
                self.on_disconnect()

    def _handle(self, dtype, seq, payload):
        log.debug("RX type=%02X seq=%d %s", dtype, seq, payload.hex())
        if dtype == ACK:
            self.seq = seq          # następny pakiet idzie z numerem z ACK (1 - poprzedni)
            self._ack.set()
            return
        if dtype in (DATA_MDR, 0x0E):
            self._raw(pack(ACK, 1 - seq, b""))
        if dtype != DATA_MDR or not payload:
            return
        cmd = payload[0]
        if cmd == CONNECT_RET_PROTOCOL_INFO:
            self._proto.set()
        elif self.proto == "v1" and cmd in (NCASM_RET_PARAM, NCASM_NTFY_PARAM) and len(payload) >= 8 \
                and payload[1] == V1_NC_ASM:
            # [cmd, type, effect, ncType, ncValue, asmType, asmId, asmValue]
            _, variant, effect, nc_type, nc_value, _asm_type, asm_id, asm_value = payload[:8]
            self.variant = variant
            st = self.state
            nc = nc_value != 0
            st.update(v1_nc_type=nc_type, nc_mode=0 if nc else 1, voice=bool(asm_id),
                      mode=MODE_OFF if effect == 0 else (MODE_NC if nc else MODE_AMBIENT))
            if not nc and asm_value:
                st["level"] = asm_value
            self._nc.set()
            self.on_state(dict(st))
        elif cmd in (NCASM_RET_PARAM, NCASM_NTFY_PARAM) and len(payload) >= 7 \
                and payload[1] in (NC_DUAL_SEAMLESS, NC_DUAL_SEAMLESS_NA):
            _, variant, _status, effect, nc_mode, amb_mode, level = payload[:7]
            self.variant = variant
            st = self.state
            st.update(nc_mode=nc_mode, voice=bool(amb_mode), level=level,
                      mode=MODE_OFF if not effect else (MODE_NC if nc_mode == 0 else MODE_AMBIENT))
            if variant == NC_DUAL_SEAMLESS_NA and len(payload) >= 9:
                st.update(auto=payload[7], sens=payload[8])
            self._nc.set()
            self.on_state(dict(st))
