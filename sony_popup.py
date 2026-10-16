"""
Popup w stylu iOS dla słuchawek Sony (WH-1000XM6, WH-ULT900N) na Windows.
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
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QLinearGradient, QPainter, QPen, QPixmap
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

# Obsługiwane słuchawki: dopasowanie po nazwie Bluetooth; zdjęcie <key>.png obok skryptu/exe
DEVICES = [
    {"key": "wh-1000xm6", "name": "WH-1000XM6", "match": ["WH-1000XM6"], "fallback_img": "headphones"},
    {"key": "wh-ult900n", "name": "WH-ULT900N", "match": ["WH-ULT900N", "ULT WEAR"]},
]
NAME = DEVICES[0]["name"]  # domyślne, zanim cokolwiek się połączy
BATT_KEY = "{104EA319-6EE2-4701-BD47-8DDBF425BBE5} 2"  # DEVPKEY battery level (HFP)
FROZEN = getattr(sys, "frozen", False)  # uruchomione jako SonyPopup.exe (PyInstaller)
HERE = Path(sys._MEIPASS) if FROZEN else Path(__file__).resolve().parent      # zasoby (zdjęcie, ikona)
APP_DIR = Path(sys.executable).parent if FROZEN else HERE                      # log obok exe/skryptu


def device_for(name):
    n = (name or "").lower()
    return next((d for d in DEVICES if any(m.lower() in n for m in d["match"])), None)


def device_image(dev):
    for stem in (dev["key"], dev.get("fallback_img")):
        if not stem:
            continue
        for e in (".png", ".webp", ".jpg", ".jpeg"):
            if (HERE / f"{stem}{e}").exists():
                return HERE / f"{stem}{e}"
    return None

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


def read_battery(name=NAME):
    ps = (
        f"Get-PnpDevice -FriendlyName '*{name}*' | "
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
    connected = Signal(object, str)          # (adres BT — 48 bit, nie int!, klucz urządzenia)
    already_connected = Signal(object, str)  # przy starcie skryptu: tylko sterowanie, bez popupu
    disconnected = Signal(str)
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
    devs = []
    for info in infos:
        d = device_for(info.name)
        if not d:
            continue
        dev = await BluetoothDevice.from_id_async(info.id)
        key = d["key"]
        print(f"Nasłuchuję: {info.name}", flush=True)

        def on_change(sender, _, key=key):
            if sender.connection_status == BluetoothConnectionStatus.CONNECTED:
                bridge.connected.emit(sender.bluetooth_address, key)
            else:
                bridge.disconnected.emit(key)

        dev.add_connection_status_changed(on_change)
        devs.append(dev)
        if dev.connection_status == BluetoothConnectionStatus.CONNECTED:
            bridge.already_connected.emit(dev.bluetooth_address, key)
    if not devs:
        print("Nie znaleziono sparowanych słuchawek: " + ", ".join(d["name"] for d in DEVICES))
        return
    await asyncio.Event().wait()  # trzyma referencje do urządzeń i pętlę przy życiu


# ---------- własne kontrolki ----------

import math

from PySide6.QtCore import QPointF
from PySide6.QtGui import QBrush, QFontMetrics, QRadialGradient
from PySide6.QtWidgets import QAbstractButton


def _lerp(a, b, t):
    return a + (b - a) * t


class HeadphonesView(QWidget):
    """Zdjęcie słuchawek: po najechaniu unosi się, lekko kołysze i przechyla za kursorem."""

    def __init__(self, pixmap, height=140):
        super().__init__()
        self.pix = pixmap
        self.setFixedHeight(height)
        self.setMouseTracking(True)
        self.hover = 0.0          # 0..1 płynne wejście/wyjście
        self.tilt = 0.0           # -1..1 pozycja kursora w poziomie
        self.tilt_y = 0.0
        self._target_hover = 0.0
        self._target_tilt = 0.0
        self._target_tilt_y = 0.0
        self.t = 0.0
        self.pop = 0.0            # "podskok" przy kliknięciu
        self._tick = QTimer(self, interval=16, timeout=self._step)

    def enterEvent(self, e):
        self._target_hover = 1.0
        self._tick.start()

    def leaveEvent(self, e):
        self._target_hover = 0.0
        self._target_tilt = self._target_tilt_y = 0.0
        self._tick.start()

    def mouseMoveEvent(self, e):
        c = self.rect().center()
        self._target_tilt = max(-1.0, min(1.0, (e.position().x() - c.x()) / (self.width() / 2)))
        self._target_tilt_y = max(-1.0, min(1.0, (e.position().y() - c.y()) / (self.height() / 2)))

    def mousePressEvent(self, e):
        self.pop = 1.0
        self._tick.start()

    def _step(self):
        self.t += 0.016
        k = 0.14
        self.hover = _lerp(self.hover, self._target_hover, k)
        self.tilt = _lerp(self.tilt, self._target_tilt, k)
        self.tilt_y = _lerp(self.tilt_y, self._target_tilt_y, k)
        self.pop *= 0.88
        idle = (abs(self.hover) < 0.002 and self._target_hover == 0 and self.pop < 0.002
                and abs(self.tilt) < 0.002)
        if idle:
            self.hover = self.tilt = self.tilt_y = self.pop = 0.0
            self._tick.stop()
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        cx, cy = self.width() / 2, self.height() / 2
        h = self.hover
        bob = math.sin(self.t * 2.4) * 4 * h                    # unoszenie
        sway = math.sin(self.t * 1.7) * 3.0 * h                 # kołysanie (stopnie)
        lift = -6 * h - 10 * self.pop
        scale = 1.0 + 0.07 * h + 0.06 * self.pop
        angle = sway + self.tilt * 7 * h
        p.setPen(Qt.NoPen)
        # poświata za słuchawkami (mocniejsza przy najechaniu)
        gr = self.pix.height() * (0.62 + 0.08 * h)
        glow = QRadialGradient(QPointF(cx, cy - 4 + lift * 0.5), gr)
        glow.setColorAt(0, QColor(255, 255, 255, int(38 + 30 * h)))
        glow.setColorAt(0.55, QColor(255, 255, 255, int(12 + 10 * h)))
        glow.setColorAt(1, QColor(255, 255, 255, 0))
        p.setBrush(QBrush(glow))
        p.drawEllipse(QPointF(cx, cy - 4 + lift * 0.5), gr, gr)
        # cień "na podłodze": mniejszy i bledszy, gdy słuchawki się unoszą
        sh_w = self.pix.width() * (0.55 - 0.15 * h)
        fy = self.height() - 9
        sh = QRadialGradient(QPointF(cx, fy), sh_w / 2)
        sh.setColorAt(0, QColor(0, 0, 0, int(150 - 60 * h)))
        sh.setColorAt(1, QColor(0, 0, 0, 0))
        p.setBrush(QBrush(sh))
        p.drawEllipse(QPointF(cx, fy), sh_w / 2, 6)
        # słuchawki
        p.translate(cx + self.tilt * 5 * h, cy + bob + lift + self.tilt_y * 3 * h)
        p.rotate(angle)
        p.scale(scale, scale)
        # perspektywa: lekkie ściśnięcie po stronie, w którą "obraca się" obraz
        p.shear(0.0, -self.tilt * 0.04 * h)
        p.drawPixmap(QPointF(-self.pix.width() / 2, -self.pix.height() / 2 - 4), self.pix)
        p.end()


class Segmented(QWidget):
    """Przełącznik w stylu iOS z przesuwaną pigułką."""
    changed = Signal(str)

    def __init__(self, items):
        super().__init__()
        self.items = items
        self.current = None
        self.setFixedHeight(40)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self._x = None            # animowana pozycja pigułki (indeks, float)
        self._hover = -1
        self._tick = QTimer(self, interval=16, timeout=self._step)
        self.setFont(QFont("Segoe UI", 10, QFont.DemiBold))

    def set_current(self, key):
        if key == self.current:
            return
        self.current = key
        if self._x is None:
            self._x = float(self._index(key))
        self._tick.start()
        self.update()

    def _index(self, key):
        return next((i for i, (k, _) in enumerate(self.items) if k == key), 0)

    def _step(self):
        target = float(self._index(self.current))
        self._x = _lerp(self._x, target, 0.22)
        if abs(self._x - target) < 0.003:
            self._x = target
            self._tick.stop()
        self.update()

    def _slot(self, pos):
        return max(0, min(len(self.items) - 1, int(pos.x() / (self.width() / len(self.items)))))

    def mouseMoveEvent(self, e):
        i = self._slot(e.position())
        if i != self._hover:
            self._hover = i
            self.update()

    def leaveEvent(self, e):
        self._hover = -1
        self.update()

    def mouseReleaseEvent(self, e):
        if not self.isEnabled():
            return
        key = self.items[self._slot(e.position())][0]
        self.set_current(key)
        self.changed.emit(key)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        n = len(self.items)
        w = r.width() / n
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 22))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        on = self.isEnabled()
        if self._hover >= 0 and on and self.items[self._hover][0] != self.current:
            p.setBrush(QColor(255, 255, 255, 14))
            hr = QRectF(self._hover * w + 3, 3, w - 6, r.height() - 6)
            p.drawRoundedRect(hr, hr.height() / 2, hr.height() / 2)
        if self.current is not None and self._x is not None:
            pill = QRectF(self._x * w + 3, 3, w - 6, r.height() - 6)
            g = QLinearGradient(pill.topLeft(), pill.bottomLeft())
            g.setColorAt(0, QColor("#2b95ff") if on else QColor("#5a5a5e"))
            g.setColorAt(1, QColor("#0a6fe0") if on else QColor("#48484a"))
            p.setBrush(g)
            p.drawRoundedRect(pill, pill.height() / 2, pill.height() / 2)
        f = QFont(self.font())
        for size in (10, 9.5, 9, 8.5, 8):
            f.setPointSizeF(size)
            if max(QFontMetrics(f).horizontalAdvance(l) for _, l in self.items) <= w - 14:
                break
        p.setFont(f)
        for i, (k, label) in enumerate(self.items):
            sel = abs(self._x - i) < 0.5 if self._x is not None else False
            p.setPen(QColor(255, 255, 255) if (sel and on) else QColor(255, 255, 255, 150 if on else 90))
            p.drawText(QRectF(i * w, 0, w, r.height()), Qt.AlignCenter, label)
        p.end()


class RoundButton(QAbstractButton):
    """Okrągły przycisk z animacją najechania i wciśnięcia."""

    def __init__(self, kind, size, primary=False):
        super().__init__()
        self.kind = kind
        self.primary = primary
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self._h = 0.0
        self._p = 0.0
        self._th = 0.0
        self._tp = 0.0
        self._tick = QTimer(self, interval=16, timeout=self._step)

    def set_kind(self, kind):
        self.kind = kind
        self.update()

    def enterEvent(self, e):
        self._th = 1.0
        self._tick.start()

    def leaveEvent(self, e):
        self._th = 0.0
        self._tick.start()

    def mousePressEvent(self, e):
        self._tp = 1.0
        self._tick.start()
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        self._tp = 0.0
        self._tick.start()
        super().mouseReleaseEvent(e)

    def _step(self):
        self._h = _lerp(self._h, self._th, 0.25)
        self._p = _lerp(self._p, self._tp, 0.35)
        if abs(self._h - self._th) < 0.01 and abs(self._p - self._tp) < 0.01:
            self._h, self._p = self._th, self._tp
            self._tick.stop()
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = self.width()
        scale = 1.0 + 0.06 * self._h - 0.10 * self._p
        p.translate(s / 2, s / 2)
        p.scale(scale, scale)
        r = s / 2 - 2
        p.setPen(Qt.NoPen)
        if self.primary:
            p.setBrush(QColor(255, 255, 255, 235 + int(20 * self._h)))
            ic = QColor(28, 28, 30)
        else:
            p.setBrush(QColor(255, 255, 255, 20 + int(26 * self._h)))
            ic = QColor(255, 255, 255)
        p.drawEllipse(QPointF(0, 0), r, r)
        isz = int(s * (0.46 if self.primary else 0.44))
        pm = media_icon(self.kind, ic).pixmap(isz, isz)
        off = 2 if self.kind == "play" else 0   # optyczne wyśrodkowanie trójkąta
        p.drawPixmap(int(-isz / 2 + off), int(-isz / 2), pm)
        p.end()


class PillButton(QAbstractButton):
    """Przycisk 'Gotowe' — pigułka z delikatnym hover/press."""

    def __init__(self, text):
        super().__init__()
        self.setText(text)
        self.setFixedHeight(40)
        self.setCursor(Qt.PointingHandCursor)
        self.setFont(QFont("Segoe UI", 11, QFont.DemiBold))
        self._h = 0.0
        self._th = 0.0
        self._tick = QTimer(self, interval=16, timeout=self._step)

    def enterEvent(self, e):
        self._th = 1.0
        self._tick.start()

    def leaveEvent(self, e):
        self._th = 0.0
        self._tick.start()

    def _step(self):
        self._h = _lerp(self._h, self._th, 0.25)
        if abs(self._h - self._th) < 0.01:
            self._h = self._th
            self._tick.stop()
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self.isDown():
            r.adjust(3, 2, -3, -2)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 26 + int(22 * self._h)))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setPen(QColor(255, 255, 255))
        p.drawText(r, Qt.AlignCenter, self.text())
        p.end()


class BatteryBar(QWidget):
    """Pasek baterii z kolorem zależnym od poziomu i płynnym napełnianiem."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(8)
        self.value = 0.0
        self.target = 0
        self._tick = QTimer(self, interval=16, timeout=self._step)

    def setValue(self, v):
        self.target = v
        self.value = 0.0          # napełnianie od zera przy każdym pokazaniu popupu
        self._tick.start()

    def _step(self):
        self.value = _lerp(self.value, self.target, 0.12)
        if abs(self.value - self.target) < 0.3:
            self.value = self.target
            self._tick.stop()
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 30))
        p.drawRoundedRect(r, 4, 4)
        lvl = self.target
        col = QColor("#30d158") if lvl > 40 else QColor("#ffd60a") if lvl > 15 else QColor("#ff453a")
        fill = QRectF(0, 0, max(8.0, r.width() * self.value / 100), r.height())
        p.setBrush(col)
        p.drawRoundedRect(fill, 4, 4)
        p.end()


class Popup(QWidget):
    W, H = 380, 500
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
        self.title = title

        # zdjęcia wszystkich modeli ładowane raz; brak zdjęcia -> rysowana ikona
        self._pix = {}
        for d in DEVICES:
            path = device_image(d)
            pm = load_image(path) if path else None
            if pm is None:
                pm = tray_icon().pixmap(64, 64).scaledToHeight(110, Qt.SmoothTransformation)
            self._pix[d["key"]] = pm
        first = self._pix[DEVICES[0]["key"]]
        img = HeadphonesView(first, height=max(p.height() for p in self._pix.values()) + 36)
        self.img = img

        self.batt_lbl = QLabel()
        self.batt_lbl.setAlignment(Qt.AlignCenter)
        self.batt_lbl.setFont(QFont("Segoe UI", 12))
        self.bar = BatteryBar()

        # --- tryb dźwięku ---
        seg = Segmented(MODES)
        seg.setEnabled(False)
        seg.changed.connect(lambda m: self.mode_requested.emit(m, None))
        self.seg = seg

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
        pl_lay.setSpacing(22)
        pl_lay.addStretch(1)
        self.play_btn = None
        for kind, vk in (("prev", VK_PREV), ("play", VK_PLAY_PAUSE), ("next", VK_NEXT)):
            big = kind == "play"
            b = RoundButton(kind, 58 if big else 44, primary=big)
            b.clicked.connect(lambda _=False, v=vk: self._media(v))
            pl_lay.addWidget(b)
            if big:
                self.play_btn = b
        pl_lay.addStretch(1)

        btn = PillButton("Gotowe")
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

    def set_device(self, key):
        d = next((d for d in DEVICES if d["key"] == key), DEVICES[0])
        self.title.setText(d["name"])
        self.img.pix = self._pix[d["key"]]
        self.img.update()

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
        self.play_btn.set_kind("pause" if playing else "play")

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
        self.seg.setEnabled(ok)
        self.seg.update()
        self.amb.setEnabled(ok)
        self.nc_lbl.setText("" if ok else "Sterowanie niedostępne")
        self.nc_lbl.setVisible(not ok)

    def set_nc_state(self, st):
        mode = st.get("mode")
        if mode in dict(MODES):
            self.seg.set_current(mode)
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
    current = {"dev": DEVICES[0]}   # ostatnio połączone słuchawki

    def select_device(key):
        current["dev"] = next(d for d in DEVICES if d["key"] == key)
        popup.set_device(key)

    def fetch_battery():
        name = current["dev"]["name"]
        threading.Thread(target=lambda: bridge.battery.emit(read_battery(name)), daemon=True).start()

    ctrl = Controller(bridge)
    popup.mode_requested.connect(ctrl.set_mode)
    bridge.nc_state.connect(popup.set_nc_state)
    bridge.nc_available.connect(popup.set_nc_available)

    # Windows uzupełnia poziom baterii dopiero po zestawieniu HFP, stąd opóźnienie
    def on_connected(addr, key):
        select_device(key)
        QTimer.singleShot(3000, fetch_battery)
        ctrl.start(addr)

    def on_already(addr, key):
        select_device(key)
        ctrl.start(addr, delay=0.5)

    def on_disconnected(key):
        if key == current["dev"]["key"]:
            ctrl.stop()

    bridge.connected.connect(on_connected)
    bridge.already_connected.connect(on_already)
    bridge.disconnected.connect(on_disconnected)

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
        name = current["dev"]["name"]
        tray.setToolTip(f"{name}: {level}%" if level is not None else name)

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
    tray.setToolTip("Sony Popup")
    tray.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
