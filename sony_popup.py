"""
Popup w stylu iOS dla Sony WH-1000XM6 na Windows.
pip install PySide6 winrt-runtime winrt-Windows.Devices.Bluetooth winrt-Windows.Devices.Enumeration winrt-Windows.Foundation winrt-Windows.Foundation.Collections
Wymaga sony_mdr.py w tym samym folderze (sterowanie ANC/otoczeniem).
Opcjonalnie: pip install winrt-Windows.Media.Control  (tytuł/wykonawca aktualnego utworu)
Uruchom: pythonw sony_popup.py   (bez konsoli)
Opcjonalnie: obok skryptu plik headphones.png/.jpg/.webp (własna grafika słuchawek).
"""
import asyncio
import faulthandler
import logging
import traceback
import subprocess
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QRectF, QSize, QObject, QPoint, QPropertyAnimation, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import (QApplication, QButtonGroup, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QMenu,
                               QProgressBar, QPushButton, QSlider, QSystemTrayIcon, QVBoxLayout, QWidget)
from winrt.windows.devices.bluetooth import BluetoothConnectionStatus, BluetoothDevice
from winrt.windows.devices.enumeration import DeviceInformation

import sony_mdr
from sony_mdr import MODE_AMBIENT, MODE_NC, MODE_OFF

try:
    from winrt.windows.media.control import \
        GlobalSystemMediaTransportControlsSessionManager as MediaManager
except ImportError:
    MediaManager = None

log = logging.getLogger("sony_popup")
MODES = [(MODE_NC, "Redukcja szumów"), (MODE_AMBIENT, "Otoczenie"), (MODE_OFF, "Wyłączone")]

NAME = "WH-1000XM6"
BATT_KEY = "{104EA319-6EE2-4701-BD47-8DDBF425BBE5} 2"  # DEVPKEY battery level (HFP)
FROZEN = getattr(sys, "frozen", False)  # uruchomione jako SonyPopup.exe (PyInstaller)
HERE = Path(sys._MEIPASS) if FROZEN else Path(__file__).resolve().parent      # zasoby (zdjęcie, ikona)
APP_DIR = Path(sys.executable).parent if FROZEN else HERE                      # log obok exe/skryptu
IMG = next((HERE / f"headphones{e}" for e in (".png", ".webp", ".jpg", ".jpeg")
            if (HERE / f"headphones{e}").exists()), None)
CREATE_NO_WINDOW = 0x08000000


def load_image(path, height=130):
    """Skaluje zdjęcie i usuwa jasne tło (zdjęcia produktowe zwykle mają białe)."""
    img = QImage(str(path))
    if img.isNull():
        return None
    img = img.scaledToHeight(height, Qt.SmoothTransformation).convertToFormat(QImage.Format_ARGB32)
    corners = [img.pixelColor(0, 0), img.pixelColor(img.width() - 1, 0),
               img.pixelColor(0, img.height() - 1), img.pixelColor(img.width() - 1, img.height() - 1)]
    if all(c.alpha() > 250 and min(c.red(), c.green(), c.blue()) > 230 for c in corners):
        for y in range(img.height()):
            for x in range(img.width()):
                c = img.pixelColor(x, y)
                m = min(c.red(), c.green(), c.blue())
                if m > 235:
                    img.setPixelColor(x, y, QColor(0, 0, 0, 0))
                elif m > 215:  # miękka krawędź
                    c.setAlpha(int(255 * (235 - m) / 20))
                    img.setPixelColor(x, y, c)
    return QPixmap.fromImage(img)


# ---------- odtwarzacz (systemowe sterowanie multimediami) ----------

VK_PREV, VK_NEXT, VK_PLAY_PAUSE = 0xB1, 0xB0, 0xB3


def media_key(vk):
    """Wirtualny klawisz multimedialny — trafia do aktywnego odtwarzacza (Spotify, YouTube, VLC...)."""
    import ctypes
    ctypes.windll.user32.keybd_event(vk, 0, 1, 0)       # KEYEVENTF_EXTENDEDKEY
    ctypes.windll.user32.keybd_event(vk, 0, 1 | 2, 0)   # + KEYUP


async def _media_info():
    mgr = await MediaManager.request_async()
    sess = mgr.get_current_session()
    if sess is None:
        return None
    props = await sess.try_get_media_properties_async()
    playing = int(sess.get_playback_info().playback_status) == 4  # PLAYING
    return {"title": props.title or "", "artist": props.artist or "", "playing": playing,
            "app": sess.source_app_user_model_id or ""}


def media_info():
    if MediaManager is None:
        return False  # brak pakietu — pokazujemy same przyciski
    try:
        return asyncio.run(_media_info())
    except Exception:
        log.exception("media_info")
        return None


def media_icon(kind, color=QColor(255, 255, 255)):
    """Ikony rysowane ręcznie — bez zależności od fontów systemowych."""
    pm = QPixmap(48, 48)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QPolygonF

    def tri(x0, x1, cy=24, h=14):  # trójkąt w prawo (x0<x1) albo w lewo (x0>x1)
        p.drawPolygon(QPolygonF([QPointF(x0, cy - h), QPointF(x1, cy), QPointF(x0, cy + h)]))

    if kind == "play":
        tri(16, 36, h=15)
    elif kind == "pause":
        p.drawRoundedRect(QRectF(14, 10, 7, 28), 2, 2)
        p.drawRoundedRect(QRectF(27, 10, 7, 28), 2, 2)
    elif kind == "next":
        tri(8, 24, h=11)
        tri(24, 40, h=11)
    elif kind == "prev":
        tri(40, 24, h=11)
        tri(24, 8, h=11)
    p.end()
    return QIcon(pm)


def read_battery():
    ps = (
        f"Get-PnpDevice -FriendlyName '*{NAME}*' | "
        f"Get-PnpDeviceProperty -KeyName '{BATT_KEY}' -ErrorAction SilentlyContinue | "
        "Where-Object { $_.Data -ne $null } | Select-Object -First 1 -ExpandProperty Data"
    )
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=10,
                             creationflags=CREATE_NO_WINDOW).stdout.strip()
        return int(out) if out.isdigit() else None
    except Exception:
        return None


class Bridge(QObject):
    connected = Signal(object)     # adres BT (48 bit — nie mieści się w int Qt); popup + start sterowania
    already_connected = Signal(object)  # przy starcie skryptu: tylko sterowanie, bez popupu
    disconnected = Signal()
    battery = Signal(object)
    media = Signal(object)
    toggle = Signal()
    nc_state = Signal(dict)
    nc_available = Signal(bool)


class Controller:
    """Trzyma połączenie RFCOMM w osobnym wątku; komendy też idą poza wątkiem GUI (czekają na ACK)."""

    def __init__(self, bridge: Bridge):
        self.bridge = bridge
        self.mdr = None
        self.addr = None
        self._lock = threading.Lock()

    def start(self, addr, delay=4.0, retries=3):
        self.addr = addr
        threading.Thread(target=self._run, args=(delay, retries), daemon=True, name="mdr-connect").start()

    def _run(self, delay, retries):
        import time
        time.sleep(delay)  # profil A2DP/HFP musi się najpierw zestawić
        log.info("Sterowanie: łączę z %012X", self.addr)
        for attempt in range(retries):
            with self._lock:
                if self.mdr and self.mdr.connected:
                    return
                mdr = sony_mdr.SonyMDR(lambda: sony_mdr.rfcomm_connect(self.addr),
                                       on_state=self.bridge.nc_state.emit,
                                       on_disconnect=lambda: self.bridge.nc_available.emit(False))
                try:
                    mdr.connect()
                    self.mdr = mdr
                    self.bridge.nc_available.emit(True)
                    return
                except Exception as ex:
                    log.warning("Sterowanie: próba %d nieudana: %s", attempt + 1, ex)
                    mdr.close()
            time.sleep(8)
        self.bridge.nc_available.emit(False)

    def stop(self):
        with self._lock:
            if self.mdr:
                self.mdr.close()
            self.mdr = None
        self.bridge.nc_available.emit(False)

    def set_mode(self, mode, level=None):
        mdr = self.mdr
        if not (mdr and mdr.connected):
            return
        def job():
            try:
                mdr.set_mode(mode, level=level)
            except Exception:
                log.exception("set_mode")
        threading.Thread(target=job, daemon=True).start()


async def watch(bridge: Bridge):
    sel = BluetoothDevice.get_device_selector_from_pairing_state(True)
    infos = await DeviceInformation.find_all_async_aqs_filter(sel)
    info = next((i for i in infos if NAME.lower() in i.name.lower()), None)
    if info is None:
        print(f"Nie znaleziono sparowanego urządzenia '{NAME}'")
        return
    dev = await BluetoothDevice.from_id_async(info.id)
    print(f"Nasłuchuję: {info.name}", flush=True)

    def on_change(sender, _):
        if sender.connection_status == BluetoothConnectionStatus.CONNECTED:
            bridge.connected.emit(sender.bluetooth_address)
        else:
            bridge.disconnected.emit()

    dev.add_connection_status_changed(on_change)
    if dev.connection_status == BluetoothConnectionStatus.CONNECTED:
        bridge.already_connected.emit(dev.bluetooth_address)
    await asyncio.Event().wait()  # trzyma referencję do dev i pętlę przy życiu


class Popup(QWidget):
    W, H = 380, 470
    mode_requested = Signal(str, object)  # (tryb, poziom otoczenia albo None)

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(self.W, self.H)

        card = QWidget(self)
        card.setObjectName("card")
        card.setGeometry(0, 0, self.W, self.H)
        card.setStyleSheet("""
            #card { background: rgba(28,28,30,240); border-radius: 28px; }
            QLabel { color: white; }
            QProgressBar { background: #3a3a3c; border: none; border-radius: 4px; height: 8px; }
            QProgressBar::chunk { background: #30d158; border-radius: 4px; }
            QPushButton { background: #3a3a3c; color: white; border: none;
                          border-radius: 14px; padding: 10px; font-size: 14px; }
            QPushButton:hover { background: #48484a; }
            QPushButton:disabled { color: #8e8e93; }
            QPushButton[seg="true"] { padding: 8px 4px; font-size: 12px; border-radius: 12px; }
            QPushButton[seg="true"]:checked { background: #0a84ff; }
            QSlider::groove:horizontal { height: 6px; background: #3a3a3c; border-radius: 3px; }
            QSlider::sub-page:horizontal { background: #0a84ff; border-radius: 3px; }
            QSlider::handle:horizontal { background: white; width: 18px; margin: -6px 0; border-radius: 9px; }
        """)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(28, 18, 28, 20)
        lay.setSpacing(10)

        title = QLabel(NAME)
        title.setFont(QFont("Segoe UI", 15, QFont.DemiBold))
        title.setAlignment(Qt.AlignCenter)

        img = QLabel()
        img.setAlignment(Qt.AlignCenter)
        pix = load_image(IMG) if IMG else None
        if pix:
            img.setPixmap(pix)
            glow = QGraphicsDropShadowEffect(img)  # jasna poświata, żeby czarne słuchawki odcinały się od ciemnej karty
            glow.setColor(QColor(255, 255, 255, 110))
            glow.setBlurRadius(28)
            glow.setOffset(0, 0)
            img.setGraphicsEffect(glow)
        else:
            img.setText("\U0001F3A7")
            img.setFont(QFont("Segoe UI Emoji", 54))

        self.batt_lbl = QLabel()
        self.batt_lbl.setAlignment(Qt.AlignCenter)
        self.batt_lbl.setFont(QFont("Segoe UI", 12))
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)

        # --- tryb dźwięku ---
        seg = QWidget()
        seg_lay = QHBoxLayout(seg)
        seg_lay.setContentsMargins(0, 4, 0, 0)
        seg_lay.setSpacing(6)
        self.mode_group = QButtonGroup(self)
        self.mode_btns = {}
        for mode, label in MODES:
            b = QPushButton(label)
            b.setProperty("seg", True)
            b.setCheckable(True)
            b.setEnabled(False)
            b.clicked.connect(lambda _=False, m=mode: self.mode_requested.emit(m, None))
            self.mode_group.addButton(b)
            self.mode_btns[mode] = b
            seg_lay.addWidget(b)

        self.amb_row = QWidget()
        amb_lay = QHBoxLayout(self.amb_row)
        amb_lay.setContentsMargins(4, 0, 4, 0)
        amb_lbl = QLabel("Poziom otoczenia")
        amb_lbl.setFont(QFont("Segoe UI", 10))
        self.amb = QSlider(Qt.Horizontal)
        self.amb.setRange(1, 20)
        self.amb.sliderReleased.connect(lambda: self.mode_requested.emit(MODE_AMBIENT, self.amb.value()))
        amb_lay.addWidget(amb_lbl)
        amb_lay.addWidget(self.amb, 1)
        self.amb_row.setVisible(False)

        self.nc_lbl = QLabel("Łączenie ze sterowaniem…")
        self.nc_lbl.setAlignment(Qt.AlignCenter)
        self.nc_lbl.setFont(QFont("Segoe UI", 9))
        self.nc_lbl.setStyleSheet("color: #8e8e93;")

        # --- odtwarzacz ---
        self.track_lbl = QLabel("")
        self.track_lbl.setAlignment(Qt.AlignCenter)
        self.track_lbl.setFont(QFont("Segoe UI", 10))
        self.track_lbl.setStyleSheet("color: #d1d1d6;")
        self.track_lbl.setVisible(False)

        player = QWidget()
        pl_lay = QHBoxLayout(player)
        pl_lay.setContentsMargins(0, 0, 0, 0)
        pl_lay.setSpacing(18)
        pl_lay.addStretch(1)
        self.play_btn = None
        for kind, vk in (("prev", VK_PREV), ("play", VK_PLAY_PAUSE), ("next", VK_NEXT)):
            b = QPushButton()
            b.setIcon(media_icon(kind))
            big = kind == "play"
            b.setIconSize(QSize(26, 26) if big else QSize(20, 20))
            b.setFixedSize(54 if big else 44, 54 if big else 44)
            b.setStyleSheet(f"QPushButton {{ border-radius: {27 if big else 22}px; padding: 0; }}")
            b.clicked.connect(lambda _=False, v=vk: self._media(v))
            pl_lay.addWidget(b)
            if big:
                self.play_btn = b
        pl_lay.addStretch(1)

        btn = QPushButton("Gotowe")
        btn.clicked.connect(self.slide_out)

        for w in (title, img, self.batt_lbl, self.bar, seg, self.amb_row, self.nc_lbl,
                  self.track_lbl, player, btn):
            lay.addWidget(w)

        self.anim = QPropertyAnimation(self, b"pos", self)
        self.anim.setDuration(380)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.anim.finished.connect(self._after_anim)
        self.timer = QTimer(self, singleShot=True, interval=8000, timeout=self.slide_out)
        self._hiding = False

    def _targets(self):
        g = QApplication.primaryScreen().availableGeometry()
        x = g.center().x() - self.W // 2
        return QPoint(x, g.bottom() - self.H - 24), QPoint(x, g.bottom() + 10)

    def show_battery(self, level):
        if level is None:
            self.batt_lbl.setText("Połączono")
            self.bar.hide()
        else:
            self.batt_lbl.setText(f"{level}%")
            self.bar.setValue(level)
            self.bar.show()
        shown, hidden = self._targets()
        self._hiding = False
        self.move(hidden)
        self.show()
        self.anim.stop()
        self.anim.setStartValue(hidden)
        self.anim.setEndValue(shown)
        self.anim.start()
        self.timer.start()

    def slide_out(self):
        self.timer.stop()
        self._hiding = True
        self.anim.stop()
        self.anim.setStartValue(self.pos())
        self.anim.setEndValue(self._targets()[1])
        self.anim.start()

    media_refresh = Signal()

    def _media(self, vk):
        media_key(vk)
        if vk == VK_PLAY_PAUSE:      # od razu przełącz ikonę, nie czekając na system
            self._set_playing(not self._playing)
        elif not self._playing:      # następny/poprzedni zwykle wznawia odtwarzanie
            self._set_playing(True)
        QTimer.singleShot(600, self.media_refresh.emit)

    _playing = False

    def _set_playing(self, playing):
        self._playing = playing
        self.play_btn.setIcon(media_icon("pause" if playing else "play"))

    def set_media(self, info):
        if info is False:          # brak winrt-Windows.Media.Control
            self.track_lbl.setVisible(False)
            return
        if not info:
            self.track_lbl.setText("Nic nie jest odtwarzane")
            self.track_lbl.setVisible(True)
            self._set_playing(False)
            return
        text = info["title"] + (f" — {info['artist']}" if info["artist"] else "")
        fm = self.track_lbl.fontMetrics()
        self.track_lbl.setText(fm.elidedText(text, Qt.ElideRight, self.W - 70))
        self.track_lbl.setVisible(True)
        self._set_playing(info["playing"])

    # nie chowaj, gdy kursor jest nad popupem
    def enterEvent(self, e):
        self.timer.stop()

    def leaveEvent(self, e):
        if self.isVisible() and not self._hiding:
            self.timer.start()

    def set_nc_available(self, ok):
        for b in self.mode_btns.values():
            b.setEnabled(ok)
        self.amb.setEnabled(ok)
        self.nc_lbl.setText("" if ok else "Sterowanie niedostępne")
        self.nc_lbl.setVisible(not ok)

    def set_nc_state(self, st):
        mode = st.get("mode")
        if mode in self.mode_btns:
            self.mode_btns[mode].setChecked(True)
        self.amb_row.setVisible(mode == MODE_AMBIENT)
        if not self.amb.isSliderDown():
            self.amb.setValue(st.get("level") or 1)

    def _after_anim(self):
        if self._hiding:
            self.hide()


LOG = APP_DIR / "sony_popup.log"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


SHOW_EVENT = "Local\\SonyPopupWHXM6_Show"
HOTKEY = "Ctrl+Alt+Shift+H"
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT, VK_H = 0x1, 0x2, 0x4, 0x4000, 0x48


def single_instance():
    """Druga kopia nie startuje, tylko każe działającej pokazać popup (skrót, Action Ring, menu Start)."""
    import ctypes
    k32 = ctypes.windll.kernel32
    global _mutex
    _mutex = k32.CreateMutexW(None, False, "Local\\SonyPopupWHXM6")
    if k32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        ev = k32.OpenEventW(0x0002, False, SHOW_EVENT)  # EVENT_MODIFY_STATE
        if ev:
            k32.SetEvent(ev)
        sys.exit(0)


def start_triggers(callback):
    """Wątki nasłuchujące: globalny skrót klawiszowy + sygnał od drugiej kopii skryptu."""
    import ctypes
    from ctypes import wintypes
    k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32
    k32.CreateEventW.restype = wintypes.HANDLE
    ev = k32.CreateEventW(None, False, False, SHOW_EVENT)  # auto-reset

    def wait_event():
        while True:
            if k32.WaitForSingleObject(wintypes.HANDLE(ev), 0xFFFFFFFF) == 0:
                callback()

    def hotkey_loop():
        if not u32.RegisterHotKey(None, 1, MOD_CONTROL | MOD_ALT | MOD_SHIFT | MOD_NOREPEAT, VK_H):
            log.warning("Nie udało się zarejestrować skrótu %s (zajęty?)", HOTKEY)
            return
        msg = wintypes.MSG()
        while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312:  # WM_HOTKEY
                callback()

    threading.Thread(target=wait_event, daemon=True, name="show-event").start()
    threading.Thread(target=hotkey_loop, daemon=True, name="hotkey").start()


def ensure_autostart():
    """Wpis w HKCU\\...\\Run — startuje z systemem, odporne na usunięcie skrótu."""
    import os
    import winreg
    if FROZEN:
        cmd = f'"{sys.executable}"'
    else:
        pyw = Path(sys.executable).with_name("pythonw.exe")
        cmd = f'"{pyw}" "{Path(__file__).resolve()}"'
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_ALL_ACCESS) as k:
        try:
            current = winreg.QueryValueEx(k, "SonyPopup")[0]
        except FileNotFoundError:
            current = None
        if current != cmd:
            winreg.SetValueEx(k, "SonyPopup", 0, winreg.REG_SZ, cmd)
    # stary skrót z folderu Autostart dublowałby start
    lnk = Path(os.environ["APPDATA"]) / r"Microsoft\Windows\Start Menu\Programs\Startup\Sony Popup.lnk"
    lnk.unlink(missing_ok=True)


def tray_icon(level=None):
    """Białe słuchawki + kropka baterii (zielona/żółta/czerwona)."""
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    white = QColor(255, 255, 255)
    p.setPen(QPen(white, 7, Qt.SolidLine, Qt.RoundCap))
    p.setBrush(Qt.NoBrush)
    p.drawArc(QRectF(10, 6, 44, 44), 20 * 16, 140 * 16)   # pałąk
    p.setPen(Qt.NoPen)
    p.setBrush(white)
    p.drawRoundedRect(QRectF(6, 28, 14, 26), 6, 6)        # muszle
    p.drawRoundedRect(QRectF(44, 28, 14, 26), 6, 6)
    if level is not None:
        color = QColor("#30d158") if level > 40 else QColor("#ffd60a") if level > 15 else QColor("#ff453a")
        p.setBrush(color)
        p.setPen(QPen(QColor(0, 0, 0), 3))
        p.drawEllipse(QRectF(38, 38, 24, 24))
    p.end()
    return QIcon(pm)


def setup_logging():
    """pythonw nie ma konsoli — błędy i crashe lądują w sony_popup.log."""
    f = open(LOG, "a", encoding="utf-8", buffering=1)
    faulthandler.enable(f)
    if sys.stdout is None or sys.executable.lower().endswith("pythonw.exe"):
        sys.stdout = sys.stderr = f
    logging.basicConfig(stream=f, level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    sys.excepthook = lambda *e: f.write("".join(traceback.format_exception(*e)))
    threading.excepthook = lambda a: f.write("".join(
        traceback.format_exception(a.exc_type, a.exc_value, a.exc_traceback)))


def main():
    setup_logging()
    single_instance()
    try:
        ensure_autostart()
    except Exception:
        traceback.print_exc()
    try:  # własna ikona/grupa na pasku zadań zamiast "Python"
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("SonyPopup.WH1000XM6")
    except Exception:
        pass
    app = QApplication(sys.argv)
    app.setApplicationName("Sony Popup")
    app.setQuitOnLastWindowClosed(False)
    if (HERE / "sony_popup.ico").exists():
        app.setWindowIcon(QIcon(str(HERE / "sony_popup.ico")))

    popup = Popup()
    bridge = Bridge()
    bridge.battery.connect(popup.show_battery)

    def fetch_battery():
        threading.Thread(target=lambda: bridge.battery.emit(read_battery()), daemon=True).start()

    ctrl = Controller(bridge)
    popup.mode_requested.connect(ctrl.set_mode)
    bridge.nc_state.connect(popup.set_nc_state)
    bridge.nc_available.connect(popup.set_nc_available)

    # Windows uzupełnia poziom baterii dopiero po zestawieniu HFP, stąd opóźnienie
    bridge.connected.connect(lambda _a: QTimer.singleShot(3000, fetch_battery))
    bridge.connected.connect(lambda a: ctrl.start(a))
    bridge.already_connected.connect(lambda a: ctrl.start(a, delay=0.5))
    bridge.disconnected.connect(ctrl.stop)

    # info o utworze: odświeżane co 2 s, tylko gdy popup jest widoczny
    def fetch_media():
        threading.Thread(target=lambda: bridge.media.emit(media_info()), daemon=True).start()

    # skrót Ctrl+Alt+Shift+H / ponowne uruchomienie skryptu -> pokaż albo schowaj popup
    def toggle_popup():
        if popup.isVisible() and not popup._hiding:
            popup.slide_out()
        else:
            fetch_battery()

    bridge.toggle.connect(toggle_popup)
    start_triggers(bridge.toggle.emit)

    bridge.media.connect(popup.set_media)
    popup.media_refresh.connect(fetch_media)
    media_timer = QTimer(app, interval=2000, timeout=lambda: popup.isVisible() and fetch_media())
    media_timer.start()
    bridge.battery.connect(lambda _l: fetch_media())
    threading.Thread(target=lambda: asyncio.run(watch(bridge)), daemon=True).start()

    tray = QSystemTrayIcon(tray_icon(), app)

    def update_tray(level):
        tray.setIcon(tray_icon(level))
        tray.setToolTip(f"{NAME}: {level}%" if level is not None else NAME)

    bridge.battery.connect(update_tray)
    menu = QMenu()
    menu.addAction(f"Pokaż popup\t{HOTKEY}", fetch_battery)
    mode_menu = menu.addMenu("Tryb dźwięku")
    mode_group = QActionGroup(mode_menu)
    mode_actions = {}
    for mode, label in MODES:
        a = mode_menu.addAction(label)
        a.setCheckable(True)
        a.setActionGroup(mode_group)
        a.triggered.connect(lambda _=False, m=mode: ctrl.set_mode(m))
        mode_actions[mode] = a
    mode_menu.setEnabled(False)
    bridge.nc_available.connect(mode_menu.setEnabled)
    bridge.nc_state.connect(lambda st: st.get("mode") in mode_actions and mode_actions[st["mode"]].setChecked(True))
    menu.addSeparator()
    menu.addAction("Zamknij", app.quit)
    tray.setContextMenu(menu)
    tray.setToolTip(NAME)
    tray.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
