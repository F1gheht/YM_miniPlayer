# -*- coding: utf-8 -*-
"""
YM Mini — мини-плеер для десктопной Яндекс Музыки (Windows).

* Трек, обложка, перемотка и кнопки — через Windows Media Controls (SMTC),
  куда Яндекс Музыка сама публикует текущий трек.
* Громкость — громкость приложения «Яндекс Музыка» в микшере Windows.
* Окно всплывает по горячим клавишам / кнопкам мыши, поверх всех окон,
  не забирая фокус у активного окна.
* Может работать незаметно и появляться только пока запущена Яндекс Музыка.
"""
import os
import sys
import json
import time
import asyncio
import threading
import traceback
import queue
import re
import shutil
import subprocess
import urllib.request
import ctypes
from ctypes import wintypes

from PyQt5.QtCore import (Qt, QObject, QEvent, pyqtSignal, QTimer, QPoint, QPointF, QRectF,
                          QPropertyAnimation, QEasingCurve)
from PyQt5.QtGui import (QPainter, QColor, QPixmap, QImage, QPainterPath, QIcon, QFont, QPolygonF,
                         QPen, QCursor, QLinearGradient)
from PyQt5.QtWidgets import (QApplication, QWidget, QLabel, QSlider, QHBoxLayout,
                             QVBoxLayout, QAbstractButton, QSystemTrayIcon, QMenu, QAction,
                             QDialog, QFormLayout, QPushButton, QSpinBox, QCheckBox,
                             QDialogButtonBox, QMessageBox, QSizePolicy, QStyle)
from PyQt5.QtNetwork import QLocalServer, QLocalSocket

IS_WIN = sys.platform == "win32"
APP_NAME = "YM Mini"
ACCENT = QColor("#FFCC00")                # фирменный жёлтый
BACKGROUND = "--background" in sys.argv   # запуск из автозагрузки


def is_ym_name(name):
    """Похоже ли имя процесса / приложения на Яндекс Музыку
    (а не на Яндекс Диск или Яндекс Браузер)."""
    n = (name or "").lower()
    return ("музык" in n or "music" in n) and ("яндекс" in n or "yandex" in n)


# ---------------------------------------------------------------- настройки

ACTION_NAMES = {
    "toggle":     "Показать / скрыть окно",
    "play_pause": "Пауза / воспроизведение",
    "next":       "Следующий трек",
    "prev":       "Предыдущий трек",
    "seek_fwd":   "Перемотка вперёд",
    "seek_back":  "Перемотка назад",
    "vol_up":     "Громкость +",
    "vol_down":   "Громкость −",
    "like":       "Нравится / не нравится",
    "shuffle":    "Перемешать",
    "repeat":     "Повтор",
}

DEFAULTS = {
    "hotkeys": {"toggle": "Ctrl+Alt+M", "play_pause": "", "next": "", "prev": "",
                "seek_fwd": "", "seek_back": "", "vol_up": "", "vol_down": "",
                "like": "", "shuffle": "", "repeat": ""},
    "autohide_sec": 4,
    "show_on_hotkey": True,
    "any_player_fallback": False,
    "volume_step": 5,
    "seek_step": 10,
    "launch_with_ym": True,
    "popup_on_ym_start": False,
    "pos": None,
    "show_tray": False,
    "ym_control": True,       # кнопки ♥ ⇄ ↻ через порт управления Яндекс Музыки
    "ym_exe": "",
    "cfg_version": 5,
}


def config_path():
    base = os.environ.get("APPDATA") or os.path.expanduser("~/.config")
    d = os.path.join(base, "YMMini")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "settings.json")


def load_config():
    cfg = json.loads(json.dumps(DEFAULTS))
    try:
        with open(config_path(), encoding="utf-8") as f:
            data = json.load(f)
        hk = data.pop("hotkeys", {}) or {}
        cfg.update({k: v for k, v in data.items() if k in DEFAULTS})
        cfg["hotkeys"].update({k: v for k, v in hk.items() if k in ACTION_NAMES})
        ver = int(data.get("cfg_version", 1))
        if ver < 2:
            cfg["launch_with_ym"] = True
        if ver < 3:
            cfg["show_tray"] = False
        if ver < 4:
            cfg["popup_on_ym_start"] = False     # окно только по горячей клавише
        if ver < 5:
            cfg["ym_control"] = True             # кнопки ♥ ⇄ ↻ включены по умолчанию
        if ver < 5:
            cfg["cfg_version"] = 5
            save_config(cfg)
    except Exception:
        pass
    return cfg


def log_path():
    return os.path.join(os.path.dirname(config_path()), "error.log")


def log_error(where, text):
    try:
        with open(log_path(), "a", encoding="utf-8") as f:
            f.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] {where}\n{text}\n")
    except Exception:
        pass


def save_config(cfg):
    try:
        with open(config_path(), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        traceback.print_exc()


# ---------------------------------------------------------------- автозапуск

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "YMMini"


def autostart_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --background'
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pyw):
        exe = pyw
    return f'"{exe}" "{os.path.abspath(__file__)}" --background'


def is_autostart():
    if not IS_WIN:
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_NAME)
            return True
    except OSError:
        return False


def set_autostart(on):
    if not IS_WIN:
        return
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(k, RUN_NAME)
            except OSError:
                pass


def ym_processes():
    """Процессы Яндекс Музыки: [(имя, путь к exe)]. Ищем и по имени, и по пути."""
    try:
        import psutil
    except Exception:
        return []
    found = []
    try:
        for p in psutil.process_iter(["name", "exe"]):
            name, exe = p.info.get("name") or "", p.info.get("exe") or ""
            if is_ym_name(name) or is_ym_name(exe):
                found.append((name, exe))
    except Exception:
        pass
    return found


def ym_process_running():
    return bool(ym_processes())


CDP_PORT = 9223
CDP_FLAG = f"--remote-debugging-port={CDP_PORT}"


def ym_proc_objects():
    try:
        import psutil
    except Exception:
        return []
    res = []
    try:
        for p in psutil.process_iter(["name", "exe", "create_time"]):
            if is_ym_name(p.info.get("name")) or is_ym_name(p.info.get("exe")):
                res.append(p)
    except Exception:
        pass
    return res


def cdp_port_open():
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=0.4):
            return True
    except Exception:
        return False


# --- ярлыки Яндекс Музыки: сразу запускать её с портом управления, без перезапуска

def ym_exe_guess(known=""):
    if known and os.path.exists(known):
        return known
    d = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "YandexMusic")
    if os.path.isdir(d):
        for f in os.listdir(d):
            if f.lower().endswith(".exe") and is_ym_name(f):
                return os.path.join(d, f)
    for p in ym_proc_objects():
        if p.info.get("exe"):
            return p.info["exe"]
    return ""


def _shortcut_dirs():
    env = os.environ.get
    dirs = [os.path.join(env("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs"),
            os.path.join(env("APPDATA", ""), r"Microsoft\Internet Explorer\Quick Launch"),
            os.path.join(env("USERPROFILE", ""), "Desktop"),
            os.path.join(env("PUBLIC", ""), "Desktop"),
            os.path.join(env("PROGRAMDATA", ""), r"Microsoft\Windows\Start Menu\Programs")]
    for od in (env("OneDrive"), env("OneDriveConsumer")):
        if od:
            dirs.append(os.path.join(od, "Desktop"))
            dirs.append(os.path.join(od, "Рабочий стол"))
    try:   # настоящая папка рабочего стола (бывает перенесена в OneDrive)
        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buf) == 0:
            dirs.append(buf.value)
    except Exception:
        pass
    seen, res = set(), []
    for d in dirs:
        k = os.path.normcase(d)
        if d and k not in seen and os.path.isdir(d):
            seen.add(k)
            res.append(d)
    return res


_FLAG_RE = re.compile(r"\s*--remote-debugging-port=\d+")


def _with_flag(args, enable):
    args = _FLAG_RE.sub("", args or "").strip()
    return (args + " " + CDP_FLAG).strip() if enable else args


def _backup_dir():
    d = os.path.join(os.path.dirname(config_path()), "shortcut-backup")
    os.makedirs(d, exist_ok=True)
    return d


def _backup_index():
    try:
        with open(os.path.join(_backup_dir(), "index.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_backup_index(idx):
    with open(os.path.join(_backup_dir(), "index.json"), "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=1)


def patch_ym_launchers(exe, enable):
    """Добавить (enable=True) или убрать параметр порта в ярлыках и автозапуске Яндекс Музыки.
    Перед первым изменением ярлык копируется в резерв, при отключении он возвращается как был."""
    if not IS_WIN:
        return 0
    changed = 0
    idx = _backup_index()
    if not enable:                               # вернуть сохранённые оригиналы
        for orig, bak in list(idx.items()):
            try:
                if os.path.exists(bak) and os.path.exists(os.path.dirname(orig)):
                    shutil.copy2(bak, orig)
                    changed += 1
                os.remove(bak)
            except Exception:
                pass
            idx.pop(orig, None)
        _save_backup_index(idx)
    if exe:
        import comtypes
        import comtypes.client
        comtypes.CoInitialize()
        try:
            sh = comtypes.client.CreateObject("WScript.Shell", dynamic=True)
            exe_n = os.path.normcase(os.path.abspath(exe))
            for d in _shortcut_dirs():
                for root, _, files in os.walk(d):
                    in_pins = "user pinned" in root.lower()
                    for f in files:
                        low = f.lower()
                        if not low.endswith(".lnk"):
                            continue
                        if not in_pins and not any(w in low for w in ("яндекс", "yandex", "музык", "music")):
                            continue
                        full = os.path.join(root, f)
                        try:
                            lnk = sh.CreateShortcut(full)
                            if os.path.normcase(os.path.abspath(lnk.TargetPath or "?")) != exe_n:
                                continue
                            old = lnk.Arguments or ""
                            new = _with_flag(old, enable)
                            if new == old:
                                continue
                            if enable and full not in idx:
                                bak = os.path.join(_backup_dir(), f"{len(idx)}_{f}")
                                shutil.copy2(full, bak)
                                idx[full] = bak
                                _save_backup_index(idx)
                            lnk.Arguments = new
                            lnk.Save()
                            changed += 1
                        except Exception:
                            pass                    # ярлык только для чтения и т.п. — пропускаем
        finally:
            comtypes.CoUninitialize()
        changed += _patch_run_registry(exe_n, enable)
    return changed


def _patch_run_registry(exe_n, enable):
    """Если Яндекс Музыка сама стоит в автозагрузке — запускать её и оттуда с портом."""
    import winreg
    changed = 0
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_READ | winreg.KEY_SET_VALUE) as k:
            i, items = 0, []
            while True:
                try:
                    items.append(winreg.EnumValue(k, i))
                    i += 1
                except OSError:
                    break
            for name, val, typ in items:
                if name == RUN_NAME or not isinstance(val, str):
                    continue
                if exe_n not in os.path.normcase(val):
                    continue
                if val.startswith('"'):
                    end = val.find('"', 1)
                    head, args = val[:end + 1], val[end + 1:]
                else:
                    head, args = val.split(" ", 1)[0], (val.split(" ", 1) + [""])[1]
                new = (head + " " + _with_flag(args, enable)).strip()
                if new != val:
                    winreg.SetValueEx(k, name, 0, typ, new)
                    changed += 1
    except Exception:
        pass
    return changed


def restart_ym_with_port(exe):
    """Закрыть Яндекс Музыку и открыть её снова с портом управления
    (только на 127.0.0.1, сайты в браузере к нему подключиться не могут)."""
    import psutil
    procs = ym_proc_objects()
    if not exe:
        for p in procs:
            exe = p.info.get("exe") or exe
    if not exe:
        return False
    for p in procs:
        try:
            p.terminate()
        except Exception:
            pass
    gone, alive = psutil.wait_procs(procs, timeout=6)
    for p in alive:
        try:
            p.kill()
        except Exception:
            pass
    time.sleep(1.0)
    subprocess.Popen([exe, CDP_FLAG], close_fds=True, creationflags=0x00000008)   # DETACHED_PROCESS
    return True


# ---------------------------------------------------------------- сочетания (клавиатура + мышь)
#
# Сочетание хранится строкой: "Ctrl+Alt+M", "Ctrl+Mouse4", "Shift+WheelUp", "Z+Mouse5".
# Модификаторы: Ctrl, Alt, Shift, Win. Последним идёт клавиша и/или кнопка мыши.

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY, WM_QUIT, WM_APP_RELOAD = 0x0312, 0x0012, 0x8001
MOD_NAMES = [("Ctrl", MOD_CONTROL), ("Alt", MOD_ALT), ("Shift", MOD_SHIFT), ("Win", MOD_WIN),
             ("Meta", MOD_WIN)]   # Meta — для совместимости со старыми настройками

MOUSE_TOKENS = ["MouseLeft", "MouseRight", "MouseMiddle", "Mouse4", "Mouse5", "WheelUp", "WheelDown"]
NEEDS_KEY = ("MouseLeft", "MouseRight", "WheelUp", "WheelDown")
MOUSE_DISPLAY = {"MouseLeft": "ЛКМ", "MouseRight": "ПКМ", "MouseMiddle": "Колесо (клик)",
                 "Mouse4": "Мышь 4 (назад)", "Mouse5": "Мышь 5 (вперёд)",
                 "WheelUp": "Колесо ↑", "WheelDown": "Колесо ↓"}

VK_NAMES = {
    0x20: "Space", 0x25: "Left", 0x26: "Up", 0x27: "Right", 0x28: "Down", 0x24: "Home",
    0x23: "End", 0x21: "PgUp", 0x22: "PgDown", 0x2D: "Ins", 0x2E: "Del", 0x13: "Pause",
    0x09: "Tab", 0x08: "Backspace", 0x0D: "Enter", 0x1B: "Esc", 0x2C: "PrintScreen",
    0x91: "ScrollLock", 0x14: "CapsLock", 0xC0: "`", 0xBD: "-", 0xBB: "=", 0xDB: "[",
    0xDD: "]", 0xDC: "\\", 0xBA: ";", 0xDE: "'", 0xBC: ",", 0xBE: ".", 0xBF: "/",
    0x6A: "Num*", 0x6B: "NumPlus", 0x6D: "Num-", 0x6E: "Num.", 0x6F: "Num/",
    0xB3: "MediaPlay", 0xB0: "MediaNext", 0xB1: "MediaPrev", 0xB2: "MediaStop",
    0xAD: "VolumeMute", 0xAE: "VolumeDown", 0xAF: "VolumeUp",
}
VK_NAMES.update({0x60 + i: f"Num{i}" for i in range(10)})
NAME_TO_VK = {v.lower(): k for k, v in VK_NAMES.items()}
NAME_TO_VK.update({"pageup": 0x21, "pagedown": 0x22, "insert": 0x2D, "delete": 0x2E,
                   "escape": 0x1B, "return": 0x0D, "print": 0x2C})
MODIFIER_VKS = {0x10, 0x11, 0x12, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5}


def vk_name(vk):
    if 0x41 <= vk <= 0x5A or 0x30 <= vk <= 0x39:
        return chr(vk)
    if 0x70 <= vk <= 0x87:
        return f"F{vk - 0x6F}"
    return VK_NAMES.get(vk, f"VK{vk:02X}")


def _vk_from_char(ch):
    """Виртуальный код для символа по всем установленным раскладкам
    (чтобы старые сочетания вида «Ctrl+Alt+Ь» тоже работали)."""
    if not IS_WIN:
        return None
    u = ctypes.windll.user32
    u.VkKeyScanExW.argtypes = [ctypes.c_wchar, ctypes.c_void_p]
    u.VkKeyScanExW.restype = ctypes.c_short
    n = u.GetKeyboardLayoutList(0, None)
    layouts = (ctypes.c_void_p * max(n, 1))()
    if n:
        u.GetKeyboardLayoutList(n, layouts)
    for hkl in [u.GetKeyboardLayout(0)] + list(layouts):
        for c in (ch, ch.lower()):
            r = u.VkKeyScanExW(c, hkl)
            if r != -1 and (r & 0xFF) != 0xFF:
                return r & 0xFF
    return None


def name_vk(tok):
    t = tok.strip()
    if not t:
        return None
    if len(t) == 1 and ("A" <= t.upper() <= "Z" or "0" <= t <= "9"):
        return ord(t.upper())
    low = t.lower()
    if low[0] == "f" and low[1:].isdigit() and 1 <= int(low[1:]) <= 24:
        return 0x6F + int(low[1:])
    if low in NAME_TO_VK:
        return NAME_TO_VK[low]
    if low.startswith("vk"):
        try:
            return int(low[2:], 16)
        except ValueError:
            return None
    if len(t) == 1:
        return _vk_from_char(t)
    return None


def parse_binding(text):
    """'Ctrl+Mouse4' -> {'mods': MOD_CONTROL, 'vk': 0, 'mouse': 'Mouse4'} или None."""
    s = (text or "").strip()
    if not s:
        return None
    mods = 0
    changed = True
    while changed:
        changed = False
        for name, flag in MOD_NAMES:
            if s.lower().startswith(name.lower() + "+"):
                mods |= flag
                s = s[len(name) + 1:]
                changed = True
    mouse = None
    for tok in MOUSE_TOKENS:
        if s.lower() == tok.lower():
            mouse, s = tok, ""
            break
        if s.lower().endswith("+" + tok.lower()):
            mouse, s = tok, s[:-(len(tok) + 1)]
            break
    vk = 0
    if s:
        vk = name_vk(s)
        if not vk:
            return None
    if not mouse and not vk:
        return None
    if mouse in NEEDS_KEY and not mods and not vk:
        return None   # голые ЛКМ/ПКМ/колесо запрещены — иначе сломаются клики и прокрутка
    return {"mods": mods, "vk": vk or 0, "mouse": mouse}


def format_binding(mods, vk, mouse):
    parts = [name for name, flag in MOD_NAMES[:4] if mods & flag]
    if vk:
        parts.append(vk_name(vk))
    if mouse:
        parts.append(mouse)
    return "+".join(parts)


def display_binding(text):
    b = parse_binding(text)
    if not b:
        return text or ""
    parts = [name for name, flag in MOD_NAMES[:4] if b["mods"] & flag]
    if b["vk"]:
        parts.append(vk_name(b["vk"]))
    if b["mouse"]:
        parts.append(MOUSE_DISPLAY[b["mouse"]])
    return " + ".join(parts)


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class HotkeyManager(QObject):
    """Клавиатурные сочетания — RegisterHotKey, сочетания с мышью — низкоуровневый хук.
    Всё живёт в отдельном потоке со своим циклом сообщений."""
    triggered = pyqtSignal(str)
    failed = pyqtSignal(list)

    WM_MOUSEMOVE = 0x200
    DOWN = {0x201: "MouseLeft", 0x204: "MouseRight", 0x207: "MouseMiddle", 0x20B: "X"}
    UP_FOR = {"MouseLeft": 0x202, "MouseRight": 0x205, "MouseMiddle": 0x208,
              "Mouse4": 0x20C, "Mouse5": 0x20C}

    def __init__(self):
        super().__init__()
        self._pending = {}
        self._lock = threading.Lock()
        self._tid = None
        self._ready = threading.Event()
        self._mouse_binds = []        # [(mods, vk, token, action)]
        self._hook = None
        self._swallow_up = None
        self.status = "поток горячих клавиш не запущен"
        if IS_WIN:
            threading.Thread(target=self._run, daemon=True).start()
            self._ready.wait(3)

    def set_hotkeys(self, mapping):
        with self._lock:
            self._pending = dict(mapping)
        if self._tid:
            ctypes.windll.user32.PostThreadMessageW(self._tid, WM_APP_RELOAD, 0, 0)

    def stop(self):
        if self._tid:
            ctypes.windll.user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)

    # --- хук мыши
    def _mods_now(self):
        g = self._u.GetAsyncKeyState
        m = 0
        if g(0x11) & 0x8000: m |= MOD_CONTROL
        if g(0x12) & 0x8000: m |= MOD_ALT
        if g(0x10) & 0x8000: m |= MOD_SHIFT
        if (g(0x5B) & 0x8000) or (g(0x5C) & 0x8000): m |= MOD_WIN
        return m

    def _mouse_proc(self, n, wparam, lparam):
        try:
            if n == 0 and wparam != self.WM_MOUSEMOVE and self._mouse_binds:
                if self._swallow_up is not None and wparam == self._swallow_up:
                    self._swallow_up = None
                    return 1
                tok = None
                if wparam in self.DOWN or wparam == 0x20A:
                    info = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                    hi = (info.mouseData >> 16) & 0xFFFF
                    if wparam == 0x20A:
                        tok = "WheelUp" if ctypes.c_short(hi).value > 0 else "WheelDown"
                    elif wparam == 0x20B:
                        tok = "Mouse4" if hi == 1 else "Mouse5"
                    else:
                        tok = self.DOWN[wparam]
                if tok:
                    mods = self._mods_now()
                    for m, vk, t, act in self._mouse_binds:
                        if t == tok and m == mods and (not vk or self._u.GetAsyncKeyState(vk) & 0x8000):
                            self.triggered.emit(act)
                            self._swallow_up = self.UP_FOR.get(tok)
                            return 1          # «съедаем» нажатие, чтобы оно не ушло в другое окно
        except Exception:
            pass
        return self._u.CallNextHookEx(None, n, wparam, lparam)

    def _install_hook(self):
        if self._hook:
            return
        self._hook = self._u.SetWindowsHookExW(14, self._proc_ref,            # WH_MOUSE_LL
                                               ctypes.windll.kernel32.GetModuleHandleW(None), 0)

    def _remove_hook(self):
        if self._hook:
            self._u.UnhookWindowsHookEx(self._hook)
            self._hook = None

    def _run(self):
        u, k = ctypes.windll.user32, ctypes.windll.kernel32
        self._u = u
        u.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        u.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        u.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        u.GetAsyncKeyState.argtypes = [ctypes.c_int]
        u.GetAsyncKeyState.restype = ctypes.c_short
        HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        u.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
        u.SetWindowsHookExW.restype = ctypes.c_void_p
        u.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
        u.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        u.CallNextHookEx.restype = ctypes.c_ssize_t
        k.GetModuleHandleW.restype = wintypes.HINSTANCE
        self._proc_ref = HOOKPROC(self._mouse_proc)

        msg = wintypes.MSG()
        u.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)   # создаём очередь сообщений
        self._tid = k.GetCurrentThreadId()
        self._ready.set()
        ids = {}
        while True:
            r = u.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r in (0, -1):
                break
            try:
                if msg.message == WM_HOTKEY:
                    act = ids.get(int(msg.wParam))
                    if act:
                        self.triggered.emit(act)
                elif msg.message == WM_APP_RELOAD:
                    for i in ids:
                        u.UnregisterHotKey(None, i)
                    ids.clear()
                    self._remove_hook()
                    with self._lock:
                        mapping = dict(self._pending)
                    failed, mouse, n = [], [], 1
                    for act, text in mapping.items():
                        if not text:
                            continue
                        b = parse_binding(text)
                        if not b:
                            failed.append(text)
                        elif b["mouse"]:
                            mouse.append((b["mods"], b["vk"], b["mouse"], act))
                        elif u.RegisterHotKey(None, n, b["mods"] | MOD_NOREPEAT, b["vk"]):
                            ids[n] = act
                        else:
                            failed.append(text)
                        n += 1
                    self._mouse_binds = mouse
                    if mouse:
                        self._install_hook()
                        if not self._hook:
                            failed += [format_binding(m, v, t) for m, v, t, _ in mouse]
                    self.status = (f"клавиатурных: {len(ids)}, с мышью: {len(mouse)}"
                                   f"{' (хук мыши ок)' if self._hook else ''}"
                                   + (f"; не назначены: {', '.join(failed)}" if failed else ""))
                    if failed:
                        self.failed.emit(failed)
            except Exception:
                traceback.print_exc()
        for i in ids:
            u.UnregisterHotKey(None, i)
        self._remove_hook()


# ---------------------------------------------------------------- трек и управление (SMTC)

def _secs(x):
    """TimeSpan из winrt -> секунды (в разных версиях пакета он выглядит по-разному)."""
    if x is None:
        return 0.0
    if hasattr(x, "total_seconds"):
        return x.total_seconds()
    d = getattr(x, "duration", None)
    if d is not None:
        return d / 1e7
    try:
        return float(x) / 1e7
    except Exception:
        return 0.0


def _unix(x):
    """DateTime из winrt -> unix-время или None."""
    try:
        if hasattr(x, "timestamp"):
            return x.timestamp()
        ut = getattr(x, "universal_time", None)
        if ut:
            return ut / 1e7 - 11644473600
    except Exception:
        pass
    return None


class MediaWorker(QObject):
    state_changed = pyqtSignal(object)
    cover_changed = pyqtSignal(bytes)
    timeline_changed = pyqtSignal(float, float, bool, bool)   # позиция, длительность, играет, можно перематывать

    def __init__(self, cfg):
        super().__init__()
        self.fallback = bool(cfg.get("any_player_fallback"))
        self.loop = None
        self.manager = None
        self._session = None
        self._last_state = None
        self._key = None
        self._cover = b""
        self._thumb_reads = 0
        self._start = 0.0
        self.aumids = []
        self.error = ""

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    # winrt импортируется в рабочем потоке, чтобы его COM-апартамент
    # не конфликтовал с апартаментом Qt в главном потоке
    def _run(self):
        try:
            try:
                from winrt.windows.media.control import \
                    GlobalSystemMediaTransportControlsSessionManager as MM
                from winrt.windows.storage.streams import Buffer, DataReader, InputStreamOptions
            except ImportError:
                from winsdk.windows.media.control import \
                    GlobalSystemMediaTransportControlsSessionManager as MM
                from winsdk.windows.storage.streams import Buffer, DataReader, InputStreamOptions
        except Exception:
            self.error = traceback.format_exc()
            log_error("winrt import", self.error)
            self._emit({"ok": False, "title": "Не установлен модуль winrt",
                        "artist": "pip install -r requirements.txt"})
            return
        self._MM, self._Buffer, self._DataReader, self._ISO = MM, Buffer, DataReader, InputStreamOptions
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._main())
        except Exception:
            traceback.print_exc()

    async def _main(self):
        self.manager = await self._MM.request_async()
        while True:
            try:
                await self._poll()
            except Exception:
                err = traceback.format_exc()
                if err != self.error:
                    self.error = err
                    log_error("media poll", err)
                self._session = None
                self._emit({"ok": False})
            await asyncio.sleep(0.5)

    def _find_session(self):
        sessions = list(self.manager.get_sessions())
        self.aumids = [s.source_app_user_model_id or "?" for s in sessions]
        for s in sessions:
            if is_ym_name(s.source_app_user_model_id):
                return s, True
        for s in sessions:
            a = (s.source_app_user_model_id or "").lower()
            if ("yandex" in a or "яндекс" in a) and "browser" not in a:
                return s, True
        if self.fallback:
            s = self.manager.get_current_session()
            if s is not None:
                return s, False
        return None, False

    def _emit(self, st):
        if st != self._last_state:
            self._last_state = st
            self.state_changed.emit(st)

    async def _poll(self):
        s, is_ym = self._find_session()
        self._session = s
        if s is None:
            self._emit({"ok": False})
            self.timeline_changed.emit(0.0, 0.0, False, False)
            return
        props = await s.try_get_media_properties_async()
        info = s.get_playback_info()
        playing = int(info.playback_status) == 4          # 4 = Playing
        title, artist = props.title or "", props.artist or ""
        key = (title, artist)
        if key != self._key:
            # новый трек: обложка приходит позже названия, иногда через несколько секунд,
            # а первые чтения ещё могут вернуть картинку прошлого трека
            self._key, self._thumb_reads, self._old_cover = key, 24, self._cover
            self._polls = 0
        self._polls = getattr(self, "_polls", 0) + 1
        if self._thumb_reads > 0 or self._polls % 20 == 0:   # 12 с после смены трека, потом раз в 10 с
            self._thumb_reads = max(0, self._thumb_reads - 1)
            data = await self._read_thumb(props.thumbnail)
            waited = 24 - self._thumb_reads              # сколько чтений прошло (по 0,5 с)
            if data and data != self._cover and (data != self._old_cover or waited >= 6):
                self._cover = data
                self.cover_changed.emit(data)
            elif not data and waited >= 6 and self._cover and self._cover == self._old_cover:
                self._cover = b""                       # у нового трека обложки нет — убрать старую
                self.cover_changed.emit(b"")
        self._emit({"ok": True, "ym": is_ym, "title": title, "artist": artist, "playing": playing})

        # --- позиция трека
        try:
            tl = s.get_timeline_properties()
            start, end = _secs(tl.start_time), _secs(tl.end_time)
            dur = max(0.0, end - start)
            pos = _secs(tl.position) - start
            upd = _unix(tl.last_updated_time)
            if playing and upd:
                elapsed = time.time() - upd
                if 0 < elapsed < max(dur, 1.0):
                    pos += elapsed               # приложение обновляет позицию не каждую секунду
            pos = max(0.0, min(pos, dur)) if dur else max(0.0, pos)
            try:
                can_seek = bool(info.controls.is_playback_position_enabled)
            except Exception:
                can_seek = dur > 0
            self._start = start
            self.timeline_changed.emit(pos, dur, playing, can_seek and dur > 0)
        except Exception:
            self.timeline_changed.emit(0.0, 0.0, playing, False)

    async def _read_thumb(self, ref):
        if ref is None:
            return b""
        try:
            stream = await ref.open_read_async()
            size = int(stream.size)
            if size <= 0 or size > 20_000_000:
                return b""
            data = bytearray()
            while len(data) < size:                      # Windows может отдать картинку частями
                left = size - len(data)
                buf = await stream.read_async(self._Buffer(left), left, self._ISO.READ_AHEAD)
                chunk = self._buf_bytes(buf)
                if not chunk:
                    break
                data += chunk
            try:
                stream.close()
            except Exception:
                pass
            if len(data) < size:
                return b""
            img = QImage()
            return bytes(data) if img.loadFromData(bytes(data)) else b""   # только целые картинки
        except Exception:
            return b""

    def _buf_bytes(self, buf):
        try:
            return bytes(memoryview(buf))
        except TypeError:
            pass
        n = int(buf.length)
        if not n:
            return b""
        reader = self._DataReader.from_buffer(buf)
        arr = bytearray(n)
        res = reader.read_bytes(arr)
        if isinstance(res, (bytes, bytearray, list)) and len(res):
            return bytes(res)
        return bytes(arr)

    def command(self, name):
        if self.loop:
            asyncio.run_coroutine_threadsafe(self._cmd(name), self.loop)

    def seek(self, seconds):
        if self.loop:
            asyncio.run_coroutine_threadsafe(self._seek(seconds), self.loop)

    async def _seek(self, seconds):
        s = self._session
        if s is None:
            return
        try:
            await s.try_change_playback_position_async(int((self._start + max(0.0, seconds)) * 10_000_000))
        except Exception:
            traceback.print_exc()

    async def _cmd(self, name):
        s = self._session or self._find_session()[0]
        if s is None:
            return
        if name == "play_pause":
            await s.try_toggle_play_pause_async()
        elif name == "next":
            await s.try_skip_next_async()
        elif name == "prev":
            await s.try_skip_previous_async()
        elif name == "play":
            await s.try_play_async()
        await asyncio.sleep(0.2)
        try:
            await self._poll()
        except Exception:
            pass


# ---------------------------------------------------------------- кнопки самой Яндекс Музыки (через порт управления)

_FIND_JS = r"""
const bars = [...document.querySelectorAll('[data-test-id*="PLAYERBAR"]')];
const bar = bars.find(b => b.querySelector('[data-test-id="LIKE_BUTTON"]')) || bars[0] || document;
const byAria = re => [...bar.querySelectorAll('button')].find(b => re.test(b.getAttribute('aria-label') || ''));
const find = {
  like:    () => bar.querySelector('[data-test-id="LIKE_BUTTON"]') || byAria(/^нравится$/i),
  shuffle: () => bar.querySelector('[data-test-id*="SHUFFLE"]') || byAria(/перемеш|shuffle/i),
  repeat:  () => bar.querySelector('[data-test-id*="REPEAT"]') || byAria(/повтор|repeat/i),
};
"""

STATE_JS = "(() => {" + _FIND_JS + r"""
const info = el => !el ? {found: false} : {
  found: true,
  pressed: el.getAttribute('aria-pressed'),
  label: el.getAttribute('aria-label') || '',
  disabled: !!(el.disabled || el.getAttribute('aria-disabled') === 'true' || el.getAttribute('data-disabled') === 'true'),
  cls: String(el.className || '')
};
const img = bar.querySelector('[data-test-id*="COVER"] img, img[data-test-id*="COVER"]') || bar.querySelector('img');
const cover = img ? (img.currentSrc || img.src || '') : '';
return JSON.stringify({like: info(find.like()), shuffle: info(find.shuffle()), repeat: info(find.repeat()),
                       cover: cover});
})()"""

CLICK_JS = "(() => {" + _FIND_JS + r"""
const el = find[%s]();
if (!el) return 'missing';
el.click();
return 'ok';
})()"""


def _btn_on(info):
    """Включена ли кнопка приложения (по aria-pressed, подписи или классу)."""
    if not info or not info.get("found"):
        return False
    pr = (info.get("pressed") or "").lower()
    if pr in ("true", "mixed"):
        return True
    if pr == "false":
        return False
    lab = (info.get("label") or "").lower()
    if any(w in lab for w in ("выключить", "отключить")):
        return True
    return any(w in (info.get("cls") or "").lower() for w in ("active", "selected", "enabled", "_on"))


class YMBridge(QObject):
    """Подключение к Яндекс Музыке через её отладочный порт: читает и нажимает
    кнопки «Нравится», «Перемешать», «Повтор» прямо в приложении."""
    state_changed = pyqtSignal(object)       # dict или None (нет подключения)
    cover_ready = pyqtSignal(bytes)          # обложка из самого приложения (b"" — нет)

    def __init__(self):
        super().__init__()
        self._cover_url = None
        self._q = queue.Queue()
        self._ws = None
        self._id = 0
        self._last = "init"
        self.connected = False
        threading.Thread(target=self._run, daemon=True).start()

    def click(self, name):
        if self.connected:
            self._q.put(name)

    def _emit(self, st):
        if st != self._last:
            self._last = st
            self.state_changed.emit(st)

    def _connect(self):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/list", timeout=0.5) as r:
                targets = json.loads(r.read().decode("utf-8"))
        except Exception:
            return
        pages = [t for t in targets if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
        pages.sort(key=lambda t: 0 if (t.get("url") or "").startswith("music-application") else 1)
        if not pages:
            return
        import websocket
        self._ws = websocket.create_connection(pages[0]["webSocketDebuggerUrl"], timeout=4,
                                               suppress_origin=True)

    def _eval(self, expr):
        self._id += 1
        my = self._id
        self._ws.send(json.dumps({"id": my, "method": "Runtime.evaluate",
                                  "params": {"expression": expr, "returnByValue": True}}))
        while True:
            msg = json.loads(self._ws.recv())
            if msg.get("id") == my:
                return msg.get("result", {}).get("result", {}).get("value")

    def _update_cover(self, url):
        if url == self._cover_url:
            return
        self._cover_url = url
        if not url.startswith("http"):
            self.cover_ready.emit(b"")
            return
        urls = [url]
        if "avatars." in url:                          # попросить картинку покрупнее
            big = re.sub(r"/\d+x\d+(?=$|\?)", "/400x400", url)
            if big != url:
                urls.insert(0, big)
        for u in urls:
            try:
                req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0 YMMini"})
                with urllib.request.urlopen(req, timeout=6) as r:
                    data = r.read(8_000_000)
                if data and QImage().loadFromData(data):
                    if url == self._cover_url:              # трек не успел смениться
                        self.cover_ready.emit(data)
                    return
            except Exception:
                continue
        if url == self._cover_url:
            self.cover_ready.emit(b"")

    def _run(self):
        while True:
            try:
                if self._ws is None:
                    self.connected = False
                    self._cover_url = None
                    self._connect()
                if self._ws is None:
                    while not self._q.empty():
                        self._q.get_nowait()
                    self._emit(None)
                    time.sleep(2)
                    continue
                self.connected = True
                try:
                    cmd = self._q.get(timeout=0.6)
                except queue.Empty:
                    cmd = None
                if cmd:
                    self._eval(CLICK_JS % json.dumps(cmd))
                    time.sleep(0.15)
                val = self._eval(STATE_JS)
                st = json.loads(val) if val else None
                if st is not None:
                    self._update_cover(st.pop("cover", "") or "")
                self._emit(st)
            except Exception:
                try:
                    self._ws and self._ws.close()
                except Exception:
                    pass
                self._ws = None
                self.connected = False
                time.sleep(1)


# ---------------------------------------------------------------- громкость приложения

class VolumeController:
    def __init__(self):
        self._sessions, self._ts = [], 0.0
        self.audio_names = []
        self.error = ""
        try:
            from pycaw.pycaw import AudioUtilities
            self._AU = AudioUtilities
        except Exception as e:
            self._AU = None
            self.error = repr(e)

    def _get(self):
        if self._AU is None:
            return []
        if not self._sessions or time.time() - self._ts > 3:
            res, names = [], []
            try:
                for s in self._AU.GetAllSessions():
                    p = s.Process
                    if not p:
                        continue
                    try:
                        name = p.name()
                    except Exception:
                        continue
                    try:
                        exe = p.exe()
                    except Exception:
                        exe = ""
                    names.append(name)
                    if is_ym_name(name) or is_ym_name(exe):
                        res.append(s.SimpleAudioVolume)
            except Exception:
                traceback.print_exc()
            self._sessions, self._ts = res, time.time()
            self.audio_names = names
        return self._sessions

    def get(self):
        """(громкость 0..1 или None, mute)"""
        for sv in self._get():
            try:
                return sv.GetMasterVolume(), bool(sv.GetMute())
            except Exception:
                self._ts = 0
        return None, False

    def set(self, v):
        for sv in self._get():
            try:
                sv.SetMasterVolume(max(0.0, min(1.0, v)), None)
                if v > 0 and sv.GetMute():
                    sv.SetMute(0, None)
            except Exception:
                self._ts = 0

    def set_mute(self, m):
        for sv in self._get():
            try:
                sv.SetMute(1 if m else 0, None)
            except Exception:
                self._ts = 0


# ---------------------------------------------------------------- виджеты

class IconButton(QAbstractButton):
    """Кнопка с иконкой, нарисованной вручную (не зависит от шрифтов)."""

    def __init__(self, kind, size=30, big=False, parent=None):
        super().__init__(parent)
        self.kind, self.big, self._hover = kind, big, False
        self.active, self.badge = False, ""
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)

    def setKind(self, kind):
        if kind != self.kind:
            self.kind = kind
            self.update()

    def setActive(self, on, badge=""):
        if (on, badge) != (self.active, self.badge):
            self.active, self.badge = on, badge
            self.update()

    def _paint_line_icon(self, p, w, h):
        enabled = self.isEnabled()
        if self.active and enabled:
            col = QColor("#FF4D6D") if self.kind == "heart" else QColor(ACCENT)
        else:
            col = QColor(255, 255, 255, (235 if self._hover else 170) if enabled else 60)
        pen = QPen(col, 1.6)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        cx, cy, s = w / 2, h / 2, w * 0.28
        if self.kind == "heart":
            path = QPainterPath()
            path.moveTo(cx, cy + s * 0.95)
            path.cubicTo(cx - s * 1.7, cy - s * 0.05, cx - s * 0.85, cy - s * 1.35, cx, cy - s * 0.42)
            path.cubicTo(cx + s * 0.85, cy - s * 1.35, cx + s * 1.7, cy - s * 0.05, cx, cy + s * 0.95)
            if self.active and enabled:
                p.setBrush(col)
            p.drawPath(path)
        elif self.kind == "shuffle":
            a = s * 0.35
            p.drawLine(QPointF(cx - s, cy - s * 0.6), QPointF(cx + s, cy + s * 0.6))
            p.drawLine(QPointF(cx - s, cy + s * 0.6), QPointF(cx + s, cy - s * 0.6))
            for y, d in ((cy - s * 0.6, 1), (cy + s * 0.6, -1)):
                p.drawLine(QPointF(cx + s, y), QPointF(cx + s - a, y))
                p.drawLine(QPointF(cx + s, y), QPointF(cx + s, y + d * a))
        elif self.kind == "repeat":
            r = QRectF(cx - s, cy - s * 0.62, 2 * s, 1.24 * s)
            p.drawRoundedRect(r, s * 0.4, s * 0.4)
            a = s * 0.32
            p.drawLine(QPointF(cx + s * 0.15, cy - s * 0.62), QPointF(cx + s * 0.15 - a, cy - s * 0.62 - a))
            p.drawLine(QPointF(cx + s * 0.15, cy - s * 0.62), QPointF(cx + s * 0.15 - a, cy - s * 0.62 + a))
            if self.badge:
                f = QFont(self.font())
                f.setPixelSize(max(8, int(s * 0.95)))
                f.setBold(True)
                p.setFont(f)
                p.drawText(r, Qt.AlignCenter, self.badge)

    def enterEvent(self, e):
        self._hover = True; self.update()

    def leaveEvent(self, e):
        self._hover = False; self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        enabled = self.isEnabled()
        p.setPen(Qt.NoPen)
        if self.kind in ("heart", "shuffle", "repeat"):
            if self._hover and enabled:
                p.setBrush(QColor(255, 255, 255, 45 if self.isDown() else 28))
                p.drawEllipse(QRectF(1, 1, w - 2, h - 2))
            self._paint_line_icon(p, w, h)
            return
        if self.big:
            c = QColor(ACCENT) if enabled else QColor(120, 120, 120)
            if self._hover and enabled:
                c = c.lighter(112)
            if self.isDown():
                c = c.darker(110)
            p.setBrush(c)
            p.drawEllipse(QRectF(1, 1, w - 2, h - 2))
            fg = QColor(20, 20, 20)
        else:
            if self._hover and enabled:
                p.setBrush(QColor(255, 255, 255, 45 if self.isDown() else 28))
                p.drawEllipse(QRectF(1, 1, w - 2, h - 2))
            fg = QColor(240, 240, 240) if enabled else QColor(255, 255, 255, 70)
        p.setBrush(fg)
        cx, cy, s = w / 2, h / 2, w * 0.2

        def tri(pts):
            p.drawPolygon(QPolygonF([QPointF(x, y) for x, y in pts]))

        if self.kind == "play":
            tri([(cx - s * 0.7, cy - s * 1.05), (cx - s * 0.7, cy + s * 1.05), (cx + s * 1.05, cy)])
        elif self.kind == "pause":
            bw = s * 0.62
            p.drawRoundedRect(QRectF(cx - s * 0.85, cy - s, bw, s * 2), 1.5, 1.5)
            p.drawRoundedRect(QRectF(cx + s * 0.85 - bw, cy - s, bw, s * 2), 1.5, 1.5)
        elif self.kind == "next":
            tri([(cx - s, cy - s), (cx - s, cy + s), (cx + s * 0.55, cy)])
            p.drawRoundedRect(QRectF(cx + s * 0.55, cy - s, s * 0.42, s * 2), 1, 1)
        elif self.kind == "prev":
            tri([(cx + s, cy - s), (cx + s, cy + s), (cx - s * 0.55, cy)])
            p.drawRoundedRect(QRectF(cx - s * 0.97, cy - s, s * 0.42, s * 2), 1, 1)
        elif self.kind == "menu":
            p.setBrush(QColor(255, 255, 255, 230 if self._hover else 140))
            r = max(1.4, w * 0.07)
            for dx in (-w * 0.22, 0, w * 0.22):
                p.drawEllipse(QPointF(cx + dx, cy), r, r)


class SpeakerButton(QAbstractButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.level, self.muted, self._hover = 0.5, False, False
        self.setFixedSize(24, 24)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setToolTip("Без звука")

    def setState(self, level, muted):
        self.level, self.muted = level, muted
        self.update()

    def enterEvent(self, e):
        self._hover = True; self.update()

    def leaveEvent(self, e):
        self._hover = False; self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        col = QColor(255, 255, 255, 235 if self._hover else 170)
        if not self.isEnabled():
            col = QColor(255, 255, 255, 60)
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawPolygon(QPolygonF([QPointF(4, 9.5), QPointF(7.5, 9.5), QPointF(12, 5.5),
                                 QPointF(12, 18.5), QPointF(7.5, 14.5), QPointF(4, 14.5)]))
        pen = QPen(col, 1.6)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        if self.muted or self.level <= 0.001:
            p.drawLine(QPointF(15, 9), QPointF(21, 15))
            p.drawLine(QPointF(21, 9), QPointF(15, 15))
        else:
            p.drawArc(QRectF(10, 8.5, 7, 7), -55 * 16, 110 * 16)
            if self.level > 0.5:
                p.drawArc(QRectF(8, 5, 13, 14), -55 * 16, 110 * 16)


class FlatSlider(QSlider):
    """Тонкий ползунок: клик в любое место сразу ставит значение, колесо — окну."""
    HANDLE = 12

    def __init__(self, color, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(f"""
            QSlider {{ background: transparent; height: 18px; }}
            QSlider::groove:horizontal {{ height: 4px; background: rgba(255,255,255,45); border-radius: 2px; }}
            QSlider::sub-page:horizontal {{ background: {color}; border-radius: 2px; }}
            QSlider::handle:horizontal {{ width: {self.HANDLE}px; height: {self.HANDLE}px; margin: -4px 0;
                                          border-radius: {self.HANDLE // 2}px; background: white; }}
            QSlider::sub-page:horizontal:disabled {{ background: rgba(255,255,255,60); }}
            QSlider::handle:horizontal:disabled {{ background: rgba(255,255,255,0); }}
        """)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            v = QStyle.sliderValueFromPosition(self.minimum(), self.maximum(),
                                               e.x() - self.HANDLE // 2, self.width() - self.HANDLE)
            self.setValue(v)
        super().mousePressEvent(e)

    def wheelEvent(self, e):
        e.ignore()        # колесо обрабатывает окно (громкость)


class ElidedLabel(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.setMinimumWidth(10)

    def setFullText(self, t):
        self._full = t or ""
        self.setToolTip(self._full)
        self._refresh()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._refresh()

    def _refresh(self):
        self.setText(self.fontMetrics().elidedText(self._full, Qt.ElideRight, self.width()))


class Cover(QWidget):
    def __init__(self, size, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._pm = None

    def setImage(self, data):
        pm = QPixmap()
        self._pm = pm if data and pm.loadFromData(data) else None
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(r, 8, 8)
        p.setClipPath(path)
        if self._pm:
            pw, ph = self._pm.width(), self._pm.height()
            side = min(pw, ph)
            src = QRectF((pw - side) / 2, (ph - side) / 2, side, side)
            p.drawPixmap(r, self._pm, src)
        else:
            g = QLinearGradient(0, 0, r.width(), r.height())
            g.setColorAt(0, QColor(70, 70, 76))
            g.setColorAt(1, QColor(40, 40, 44))
            p.fillRect(r, g)
            p.setPen(QColor(255, 255, 255, 110))
            f = QFont(self.font())
            f.setPixelSize(int(r.height() * 0.42))
            p.setFont(f)
            p.drawText(r, Qt.AlignCenter, "♪")


def fmt_time(sec):
    sec = int(max(0, sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# ---------------------------------------------------------------- окно мини-плеера

def make_noactivate(widget):
    """WS_EX_NOACTIVATE: клики по окну не отнимают фокус у активного окна."""
    if not IS_WIN:
        return
    u = ctypes.windll.user32
    get = getattr(u, "GetWindowLongPtrW", u.GetWindowLongW)
    set_ = getattr(u, "SetWindowLongPtrW", u.SetWindowLongW)
    get.argtypes = [wintypes.HWND, ctypes.c_int]
    get.restype = ctypes.c_ssize_t
    set_.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    set_.restype = ctypes.c_ssize_t
    hwnd = int(widget.winId())
    GWL_EXSTYLE, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, WS_EX_TOPMOST = -20, 0x08000000, 0x80, 0x8
    style = get(hwnd, GWL_EXSTYLE)
    set_(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST)


class MiniPlayer(QWidget):
    action = pyqtSignal(str)
    seek_requested = pyqtSignal(float)
    menu_requested = pyqtSignal(QPoint)
    ext_action = pyqtSignal(str)
    W, H = 360, 162

    def __init__(self, cfg, volume):
        flags = (Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                 | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        super().__init__(None, flags)
        self.cfg, self.volume = cfg, volume
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedSize(self.W, self.H)
        self.setWindowTitle(APP_NAME)

        self.cover = Cover(76)
        self.title = ElidedLabel()
        self.artist = ElidedLabel()
        tf = QFont("Segoe UI", 10); tf.setBold(True)
        self.title.setFont(tf)
        self.artist.setFont(QFont("Segoe UI", 9))
        self.title.setStyleSheet("color: #ffffff; background: transparent;")
        self.artist.setStyleSheet("color: rgba(255,255,255,150); background: transparent;")

        self.prev_btn = IconButton("prev", 32)
        self.play_btn = IconButton("play", 38, big=True)
        self.next_btn = IconButton("next", 32)
        self.prev_btn.setToolTip("Предыдущий трек")
        self.next_btn.setToolTip("Следующий трек")
        self.play_btn.clicked.connect(self._on_play)
        self.prev_btn.clicked.connect(lambda: self.action.emit("prev"))
        self.next_btn.clicked.connect(lambda: self.action.emit("next"))

        self.shuffle_btn = IconButton("shuffle", 28)
        self.repeat_btn = IconButton("repeat", 28)
        self.like_btn = IconButton("heart", 24)
        self.shuffle_btn.clicked.connect(lambda: self._ext_click("shuffle"))
        self.repeat_btn.clicked.connect(lambda: self._ext_click("repeat"))
        self.like_btn.clicked.connect(lambda: self._ext_click("like"))
        for b in (self.shuffle_btn, self.repeat_btn, self.like_btn):
            b.setEnabled(False)          # заработают, когда подключимся к приложению
            b.setToolTip("Подключаюсь к Яндекс Музыке…")

        controls = QHBoxLayout()
        controls.setSpacing(8)
        controls.addStretch(1)
        controls.addWidget(self.shuffle_btn)
        controls.addWidget(self.prev_btn)
        controls.addWidget(self.play_btn)
        controls.addWidget(self.next_btn)
        controls.addWidget(self.repeat_btn)
        controls.addStretch(1)

        right = QVBoxLayout()
        right.setSpacing(1)
        right.setContentsMargins(0, 0, 0, 0)
        self.menu_btn = IconButton("menu", 22)
        self.menu_btn.setToolTip("Меню: настройки, выход")
        self.menu_btn.clicked.connect(lambda: self.menu_requested.emit(
            self.menu_btn.mapToGlobal(self.menu_btn.rect().bottomLeft())))
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(4)
        title_row.addWidget(self.title, 1)
        title_row.addWidget(self.like_btn, 0, Qt.AlignTop)
        title_row.addWidget(self.menu_btn, 0, Qt.AlignTop)
        right.addLayout(title_row)
        right.addWidget(self.artist)
        right.addStretch(1)
        right.addLayout(controls)

        top = QHBoxLayout()
        top.setSpacing(12)
        top.addWidget(self.cover, 0, Qt.AlignTop)
        top.addLayout(right, 1)

        small = QFont("Segoe UI", 8)
        dim = "color: rgba(255,255,255,160); background: transparent;"

        # --- перемотка
        self.seek = FlatSlider(ACCENT.name())
        self.seek.setRange(0, 0)
        self.seek.valueChanged.connect(self._on_seek_preview)
        self.seek.sliderReleased.connect(self._on_seek_commit)
        self.t_cur, self.t_dur = QLabel("0:00"), QLabel("0:00")
        for lb, al in ((self.t_cur, Qt.AlignLeft), (self.t_dur, Qt.AlignRight)):
            lb.setFont(small)
            lb.setStyleSheet(dim)
            lb.setFixedWidth(40)
            lb.setAlignment(al | Qt.AlignVCenter)
        seek_row = QHBoxLayout()
        seek_row.setSpacing(6)
        seek_row.addWidget(self.t_cur)
        seek_row.addWidget(self.seek, 1)
        seek_row.addWidget(self.t_dur)

        # --- громкость
        self.speaker = SpeakerButton()
        self.speaker.clicked.connect(self._toggle_mute)
        self.slider = FlatSlider("rgba(255,255,255,210)")
        self.slider.setRange(0, 100)
        self.slider.valueChanged.connect(self._on_slider)
        self.vol_label = QLabel("—")
        self.vol_label.setFixedWidth(40)
        self.vol_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.vol_label.setStyleSheet(dim)
        self.vol_label.setFont(small)
        vol = QHBoxLayout()
        vol.setSpacing(6)
        vol.addWidget(self.speaker)
        vol.addSpacing(10)
        vol.addWidget(self.slider, 1)
        vol.addWidget(self.vol_label)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 8)
        root.setSpacing(4)
        root.addLayout(top)
        root.addSpacing(4)
        root.addLayout(seek_row)
        root.addLayout(vol)

        self._playing = False
        self._muted = False
        self._ext = None
        self._cover_smtc = b""
        self._cover_app = None
        self._cover_shown = None
        self.ym_open = False
        self._drag = None
        self._hiding = False
        self._tl = (0.0, 0.0, False, False, time.monotonic())   # pos, dur, playing, can_seek, когда
        self._anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.finished.connect(self._anim_done)
        self._hide_timer = QTimer(self, singleShot=True, timeout=self._auto_hide)
        self._vol_timer = QTimer(self, interval=1000, timeout=self._sync_volume)
        self._tl_timer = QTimer(self, interval=250, timeout=self._tick_timeline)

        self.set_state({"ok": False})
        self.set_ext(None)
        self._tick_timeline()

    # --- состояние
    def set_state(self, st):
        ok = bool(st.get("ok"))
        for b in (self.prev_btn, self.play_btn, self.next_btn):
            b.setEnabled(ok)
        if not ok:
            if self.ym_open and not st.get("title"):
                self.title.setFullText("Яндекс Музыка открыта")
                self.artist.setFullText("Включите трек")
            else:
                self.title.setFullText(st.get("title") or "Яндекс Музыка не найдена")
                self.artist.setFullText(st.get("artist") or "Запустите приложение и включите трек")
            self._cover_smtc = b""
            self._show_cover()
            self.set_playing(False)
            return
        self.title.setFullText(st.get("title") or "Без названия")
        self.artist.setFullText(st.get("artist") or "")
        self.set_playing(bool(st.get("playing")))

    def set_cover(self, data):
        """Обложка, которую Яндекс Музыка передала в Windows (запасной источник)."""
        self._cover_smtc = data
        self._show_cover()

    def set_cover_app(self, data):
        """Обложка прямо из приложения (через порт управления) — точнее и крупнее."""
        self._cover_app = data or None
        self._show_cover()

    def _show_cover(self):
        data = self._cover_app if (self._cover_app and self._ext is not None) else self._cover_smtc
        if data != self._cover_shown:
            self._cover_shown = data
            self.cover.setImage(data)

    def set_playing(self, playing):
        self._playing = playing
        self.play_btn.setKind("pause" if playing else "play")
        self.play_btn.setToolTip("Пауза" if playing else "Воспроизвести")

    def _on_play(self):
        self.set_playing(not self._playing)      # мгновенный отклик, потом уточнится
        self.action.emit("play_pause")

    # --- кнопки самой Яндекс Музыки
    def set_ext(self, st):
        self._ext = st
        if st is None:
            self._cover_app = None
        self._show_cover()
        show = bool(self.cfg.get("ym_control", True))
        for b in (self.shuffle_btn, self.repeat_btn, self.like_btn):
            b.setVisible(show)
        if not st:
            for b in (self.shuffle_btn, self.repeat_btn, self.like_btn):
                b.setEnabled(False)
                b.setActive(False)
                b.setToolTip("Подключаюсь к Яндекс Музыке…" if self.ym_open or not show
                             else "Откройте Яндекс Музыку")
            return
        for name, btn, tips in (
                ("like", self.like_btn, ("Нравится", "Не нравится (убрать из «Мне нравится»)")),
                ("shuffle", self.shuffle_btn, ("Перемешать", "Выключить перемешивание")),
                ("repeat", self.repeat_btn, ("Повтор", "Повтор"))):
            info = st.get(name) or {}
            ok = bool(info.get("found")) and not info.get("disabled")
            btn.setEnabled(ok)
            on = _btn_on(info)
            badge = ""
            if name == "repeat" and on and any(w in (info.get("label") or "").lower()
                                               for w in ("трек", "композиц", "песн", "one")):
                badge = "1"
            btn.setActive(on, badge)
            if not info.get("found"):
                btn.setToolTip("Недоступно для этого источника (например, в «Моей волне»)")
            elif name == "repeat":
                btn.setToolTip(info.get("label") or "Повтор")
            else:
                btn.setToolTip(tips[1] if on else tips[0])

    def _ext_click(self, name):
        if name == "like" and self.like_btn.isEnabled():
            self.like_btn.setActive(not self.like_btn.active)     # мгновенный отклик
        self.ext_action.emit(name)

    # --- перемотка
    def set_timeline(self, pos, dur, playing, can_seek):
        self._tl = (pos, dur, playing, can_seek, time.monotonic())
        if not self.seek.isSliderDown():
            self._tick_timeline()

    def current_pos(self):
        pos, dur, playing, _, at = self._tl
        if playing:
            pos += time.monotonic() - at
        return min(pos, dur) if dur else pos

    def _tick_timeline(self):
        _, dur, _, can_seek, _ = self._tl
        if dur <= 0:
            self.seek.setEnabled(False)
            self.seek.blockSignals(True)
            self.seek.setRange(0, 0)
            self.seek.blockSignals(False)
            self.t_cur.setText("--:--")
            self.t_dur.setText("--:--")
            return
        self.seek.setEnabled(can_seek)
        self.seek.setToolTip("" if can_seek else "Яндекс Музыка не разрешает перематывать этот трек")
        if self.seek.isSliderDown():
            return
        self.seek.blockSignals(True)
        self.seek.setRange(0, int(dur * 1000))
        self.seek.setValue(int(self.current_pos() * 1000))
        self.seek.blockSignals(False)
        self.t_cur.setText(fmt_time(self.current_pos()))
        self.t_dur.setText(fmt_time(dur))

    def _on_seek_preview(self, val):
        self.t_cur.setText(fmt_time(val / 1000))   # время под пальцем, пока тянем ползунок

    def _on_seek_commit(self):
        self.seek_to(self.seek.value() / 1000)

    def seek_to(self, target):
        pos, dur, playing, can_seek, _ = self._tl
        if not can_seek or dur <= 0:
            return
        target = max(0.0, min(target, dur - 0.5))
        self._tl = (target, dur, playing, can_seek, time.monotonic())   # сразу показываем новую позицию
        self.seek_requested.emit(target)
        self._tick_timeline()

    def seek_relative(self, delta):
        self.seek_to(self.current_pos() + delta)

    # --- громкость
    def _sync_volume(self):
        v, muted = self.volume.get()
        has = v is not None
        self.slider.setEnabled(has)
        self.speaker.setEnabled(has)
        if not has:
            self.vol_label.setText("—")
            self.slider.setToolTip("Громкость появится, когда Яндекс Музыка начнёт воспроизведение")
            return
        self.slider.setToolTip("")
        self._muted = muted
        if not self.slider.isSliderDown():
            self.slider.blockSignals(True)
            self.slider.setValue(round(v * 100))
            self.slider.blockSignals(False)
        self._update_vol_ui()

    def _update_vol_ui(self):
        val = self.slider.value()
        self.vol_label.setText("0%" if self._muted else f"{val}%")
        self.speaker.setState(val / 100, self._muted)

    def _on_slider(self, val):
        self.volume.set(val / 100)
        if val > 0:
            self._muted = False
        self._update_vol_ui()

    def change_volume(self, delta):
        self._sync_volume()
        if self.slider.isEnabled():
            self.slider.setValue(max(0, min(100, self.slider.value() + delta)))

    def _toggle_mute(self):
        self._muted = not self._muted
        self.volume.set_mute(self._muted)
        self._update_vol_ui()

    def wheelEvent(self, e):
        step = int(self.cfg.get("volume_step", 5))
        self.change_volume(step if e.angleDelta().y() > 0 else -step)

    # --- показ / скрытие
    def _place(self):
        pos = self.cfg.get("pos")
        if pos:
            pt = QPoint(*pos)
            for sc in QApplication.screens():
                if sc.availableGeometry().adjusted(0, 0, -40, -40).contains(pt):
                    self.move(pt)
                    return
        sc = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        g = sc.availableGeometry()
        self.move(g.right() - self.W - 16, g.bottom() - self.H - 16)

    def show_popup(self):
        self._hiding = False
        if not self.isVisible():
            self._place()
            self.setWindowOpacity(0.0)
            self.show()
        self._anim.stop()
        self._anim.setStartValue(self.windowOpacity())
        self._anim.setEndValue(1.0)
        self._anim.start()
        self._sync_volume()
        self._tick_timeline()
        self._vol_timer.start()
        self._tl_timer.start()
        self._restart_hide_timer()

    def hide_popup(self):
        if not self.isVisible():
            return
        self._hiding = True
        self._hide_timer.stop()
        self._anim.stop()
        self._anim.setStartValue(self.windowOpacity())
        self._anim.setEndValue(0.0)
        self._anim.start()

    def toggle(self):
        if self.isVisible() and not self._hiding:
            self.hide_popup()
        else:
            self.show_popup()

    def _anim_done(self):
        if self._hiding:
            self._hiding = False
            self._vol_timer.stop()
            self._tl_timer.stop()
            self.hide()

    def _restart_hide_timer(self):
        sec = int(self.cfg.get("autohide_sec", 0))
        if sec > 0:
            self._hide_timer.start(sec * 1000)
        else:
            self._hide_timer.stop()

    def _auto_hide(self):
        if (self.underMouse() or self.slider.isSliderDown() or self.seek.isSliderDown()
                or self._drag is not None):
            self._restart_hide_timer()
        else:
            self.hide_popup()

    def showEvent(self, e):
        super().showEvent(e)
        make_noactivate(self)

    def enterEvent(self, e):
        self._hide_timer.stop()

    def leaveEvent(self, e):
        if self.isVisible() and not self._hiding:
            self._restart_hide_timer()

    def contextMenuEvent(self, e):
        self.menu_requested.emit(e.globalPos())

    # --- перетаскивание за фон
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPos() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPos() - self._drag)

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            self._drag = None
            self.cfg["pos"] = [self.x(), self.y()]
            save_config(self.cfg)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(255, 255, 255, 28), 1))
        p.setBrush(QColor(24, 24, 27, 242))
        p.drawRoundedRect(r, 14, 14)


# ---------------------------------------------------------------- настройки

MOD_QT_KEYS = {Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta, Qt.Key_AltGr}
QT_MOUSE = {Qt.LeftButton: "MouseLeft", Qt.RightButton: "MouseRight",
            Qt.MiddleButton: "MouseMiddle", Qt.XButton1: "Mouse4", Qt.XButton2: "Mouse5"}


def qt_mods_to_win(qm):
    qm = int(qm)
    m = 0
    if qm & int(Qt.ControlModifier): m |= MOD_CONTROL
    if qm & int(Qt.AltModifier):     m |= MOD_ALT
    if qm & int(Qt.ShiftModifier):   m |= MOD_SHIFT
    if qm & int(Qt.MetaModifier):    m |= MOD_WIN
    return m


class BindingEdit(QPushButton):
    """Поле записи сочетания: клавиши, кнопки мыши, колесо и их комбинации."""
    PROMPT = "Нажмите клавиши / кнопку мыши…  (Esc — отмена)"

    def __init__(self, value="", parent=None):
        super().__init__(parent)
        self.value = value or ""
        self.recording = False
        self._key = 0
        self.setMinimumWidth(240)
        self.clicked.connect(self.start)
        self._refresh()

    def _refresh(self, text=None):
        if text is None:
            text = display_binding(self.value) if self.value else "— не назначено —"
        self.setText(text)

    def clear(self):
        self.value = ""
        self._refresh()

    def start(self):
        if self.recording:
            return
        self.recording = True
        self._key = 0
        self._refresh(self.PROMPT)
        self.grabKeyboard()
        self.grabMouse()

    def _finish(self, value=None):
        self.recording = False
        self.releaseKeyboard()
        self.releaseMouse()
        if value is not None:
            self.value = value
        self._refresh()

    def _preview(self, mods):
        parts = [n for n, f in MOD_NAMES[:4] if mods & f]
        if self._key:
            parts.append(vk_name(self._key))
        self._refresh(" + ".join(parts) + " + …" if parts else self.PROMPT)

    def event(self, e):
        if self.recording:
            t = e.type()
            if t == QEvent.KeyPress:
                self._key_press(e); return True
            if t == QEvent.KeyRelease:
                self._key_release(e); return True
            if t == QEvent.MouseButtonPress:
                self._mouse_press(e); return True
            if t in (QEvent.MouseButtonRelease, QEvent.MouseButtonDblClick, QEvent.MouseMove):
                return True
            if t == QEvent.Wheel:
                tok = "WheelUp" if e.angleDelta().y() > 0 else "WheelDown"
                mods = qt_mods_to_win(e.modifiers())
                if not mods and not self._key:
                    self._refresh("Колесо — только вместе с клавишей (Ctrl, Alt…)")
                else:
                    self._finish(format_binding(mods, self._key, tok))
                return True
            if t == QEvent.FocusOut:
                self._finish()
        return super().event(e)

    def _key_press(self, e):
        if e.isAutoRepeat():
            return
        mods = qt_mods_to_win(e.modifiers())
        if e.key() == Qt.Key_Escape and not mods:
            self._finish()
            return
        if e.key() in MOD_QT_KEYS:
            self._preview(mods)
            return
        vk = e.nativeVirtualKey() if IS_WIN else 0
        if not vk or vk in MODIFIER_VKS:
            vk = name_vk(e.text()) if e.text() else 0
        if vk:
            self._key = vk
            self._preview(mods)

    def _key_release(self, e):
        if e.isAutoRepeat():
            return
        if e.key() in MOD_QT_KEYS:
            self._preview(qt_mods_to_win(QApplication.queryKeyboardModifiers()))
            return
        if self._key:
            mods = qt_mods_to_win(QApplication.queryKeyboardModifiers())
            self._finish(format_binding(mods, self._key, None))

    def _mouse_press(self, e):
        tok = QT_MOUSE.get(e.button())
        if not tok:
            return
        mods = qt_mods_to_win(e.modifiers())
        if tok in NEEDS_KEY and not mods and not self._key:
            self._refresh("ЛКМ/ПКМ — только вместе с клавишей (Ctrl, Alt…)")
            return
        self._finish(format_binding(mods, self._key, tok))


class SettingsDialog(QDialog):
    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.reset_pos = False
        self.setWindowTitle(f"{APP_NAME} — настройки")
        self.setMinimumWidth(520)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)

        self.edits = {}
        for act, label in ACTION_NAMES.items():
            e = BindingEdit(cfg["hotkeys"].get(act, ""))
            clr = QPushButton("✕")
            clr.setFixedWidth(30)
            clr.setToolTip("Убрать сочетание")
            clr.clicked.connect(e.clear)
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(e, 1)
            row.addWidget(clr)
            w = QWidget()
            w.setLayout(row)
            form.addRow(label + ":", w)
            self.edits[act] = e

        self.autohide = QSpinBox()
        self.autohide.setRange(0, 60)
        self.autohide.setSuffix(" с")
        self.autohide.setSpecialValueText("не скрывать")
        self.autohide.setValue(int(cfg.get("autohide_sec", 4)))
        form.addRow("Скрывать окно через:", self.autohide)

        self.step = QSpinBox()
        self.step.setRange(1, 25)
        self.step.setSuffix(" %")
        self.step.setValue(int(cfg.get("volume_step", 5)))
        form.addRow("Шаг громкости:", self.step)

        self.seek_step = QSpinBox()
        self.seek_step.setRange(1, 120)
        self.seek_step.setSuffix(" с")
        self.seek_step.setValue(int(cfg.get("seek_step", 10)))
        form.addRow("Шаг перемотки:", self.seek_step)

        self.with_ym = QCheckBox("Запускаться вместе с Яндекс Музыкой (ждать в фоне, пока она закрыта)")
        self.with_ym.setChecked(bool(cfg.get("launch_with_ym", True)))
        self.popup_ym = QCheckBox("Показывать окно, когда Яндекс Музыка запускается")
        self.popup_ym.setChecked(bool(cfg.get("popup_on_ym_start", False)))
        self.show_on_hk = QCheckBox("Показывать окно при управлении с клавиатуры / мыши")
        self.show_on_hk.setChecked(bool(cfg.get("show_on_hotkey", True)))
        self.tray_cb = QCheckBox("Показывать значок в трее")
        self.tray_cb.setChecked(bool(cfg.get("show_tray", False)))
        self.ctrl_cb = QCheckBox("Кнопки «Нравится», «Перемешать», «Повтор» "
                                 "(Яндекс Музыка запускается с портом управления)")
        self.ctrl_cb.setChecked(bool(cfg.get("ym_control", True)))
        self.fallback = QCheckBox("Если Яндекс Музыка не найдена — управлять текущим плеером Windows")
        self.fallback.setChecked(bool(cfg.get("any_player_fallback", False)))
        reset = QPushButton("Вернуть окно в правый нижний угол")
        reset.clicked.connect(self._reset_pos)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Сохранить")
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        for b in buttons.buttons():
            b.setAutoDefault(False)
            b.setDefault(False)

        root = QVBoxLayout(self)
        hint = QLabel("Кликните по полю и нажмите сочетание: клавиши, кнопку мыши, колесо "
                      "или всё вместе — например Ctrl + Мышь 4 или Alt + Колесо ↑.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        root.addWidget(hint)
        root.addLayout(form)
        root.addWidget(self.with_ym)
        root.addWidget(self.popup_ym)
        root.addWidget(self.show_on_hk)
        root.addWidget(self.tray_cb)
        root.addWidget(self.ctrl_cb)
        root.addWidget(self.fallback)
        root.addWidget(reset, 0, Qt.AlignLeft)
        root.addWidget(buttons)

    def _reset_pos(self):
        self.reset_pos = True

    def _accept(self):
        hk = {a: e.value for a, e in self.edits.items()}
        norm = {}
        for a, v in hk.items():
            if not v:
                continue
            b = parse_binding(v)
            if not b:
                QMessageBox.warning(self, APP_NAME, f"Сочетание «{v}» не поддерживается.")
                return
            key = (b["mods"], b["vk"], b["mouse"])
            if key in norm:
                QMessageBox.warning(self, APP_NAME, f"«{display_binding(v)}» назначено на несколько действий.")
                return
            norm[key] = a
        self.cfg["hotkeys"] = hk
        self.cfg["autohide_sec"] = self.autohide.value()
        self.cfg["volume_step"] = self.step.value()
        self.cfg["seek_step"] = self.seek_step.value()
        self.cfg["launch_with_ym"] = self.with_ym.isChecked()
        self.cfg["popup_on_ym_start"] = self.popup_ym.isChecked()
        self.cfg["show_on_hotkey"] = self.show_on_hk.isChecked()
        self.cfg["show_tray"] = self.tray_cb.isChecked()
        self.cfg["ym_control"] = self.ctrl_cb.isChecked()
        self.cfg["any_player_fallback"] = self.fallback.isChecked()
        if self.reset_pos:
            self.cfg["pos"] = None
        self.accept()


# ---------------------------------------------------------------- приложение

def tray_icon():
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(ACCENT)
    p.drawEllipse(2, 2, 60, 60)
    p.setBrush(QColor(20, 20, 20))
    p.drawPolygon(QPolygonF([QPointF(25, 18), QPointF(25, 46), QPointF(47, 32)]))
    p.end()
    return QIcon(pm)


class Controller(QObject):
    def __init__(self, app, server):
        super().__init__()
        self.app = app
        self.cfg = load_config()
        self.volume = VolumeController()
        self.player = MiniPlayer(self.cfg, self.volume)
        self.media = MediaWorker(self.cfg)
        self.player.action.connect(self.media.command)
        self.player.seek_requested.connect(self.media.seek)
        self.media.state_changed.connect(self._on_state)
        self.media.cover_changed.connect(self.player.set_cover)
        self.media.timeline_changed.connect(self.player.set_timeline)

        self.bridge = YMBridge()
        self.bridge.state_changed.connect(self.player.set_ext)
        self.bridge.cover_ready.connect(self.player.set_cover_app)
        self.player.ext_action.connect(self.bridge.click)
        self._last_restart = 0.0
        self._restart_fails = 0
        self._resume = None
        self._patch_timer = QTimer(self, interval=10 * 60 * 1000, timeout=self.patch_launchers)
        self._patch_timer.start()
        QTimer.singleShot(2000, self.patch_launchers)

        self.hotkeys = HotkeyManager()
        self.hotkeys.triggered.connect(self.on_hotkey)
        self.hotkeys.failed.connect(self.on_hotkeys_failed)

        self.tray = QSystemTrayIcon(tray_icon(), self)
        menu = QMenu()
        menu.addAction("Показать мини-плеер", self.player.show_popup)
        menu.addAction("Настройки…", self.open_settings)
        self.restart_act = menu.addAction("Перезапустить Яндекс Музыку, чтобы включить ♥ ⇄ ↻",
                                          lambda: self.restart_ym(manual=True))
        self.autorun_act = QAction("Запускать вместе с Windows", menu, checkable=True)
        self.autorun_act.setChecked(is_autostart())
        self.autorun_act.toggled.connect(self._autostart)
        menu.addAction(self.autorun_act)
        menu.addSeparator()
        menu.addAction("Выход", self.quit)
        self._menu = menu
        self.tray.setContextMenu(menu)
        self.player.menu_requested.connect(self._popup_menu)
        self.tray.activated.connect(self._tray_click)
        self._update_tooltip()

        # второй запуск программы просто показывает уже работающую
        self.server = server
        server.newConnection.connect(self._on_second_instance)

        # режим «вместе с Яндекс Музыкой»
        self.manual = not BACKGROUND      # запустили руками — показываемся сразу
        self.ym_running = False
        self._media_ym = False
        self._active = None
        self.watch = QTimer(self, interval=1000, timeout=self._check_ym)
        self.watch.start()
        if self.cfg.get("launch_with_ym") or is_autostart():
            try:
                set_autostart(True)       # ждать Яндекс Музыку с момента входа в Windows
            except Exception:
                traceback.print_exc()
        self.autorun_act.blockSignals(True)
        self.autorun_act.setChecked(is_autostart())
        self.autorun_act.blockSignals(False)

        self._apply_active()
        self.media.start()
        if not BACKGROUND:
            # запустили ярлыком из «Пуска» — показать окно (там же кнопка ⋯ с настройками)
            QTimer.singleShot(400, self.player.show_popup)


    # --- работа только при запущенной Яндекс Музыке
    def _is_active(self):
        return (not self.cfg.get("launch_with_ym")) or self.ym_running

    def _apply_active(self):
        active = self._is_active()
        if active == self._active:
            return
        self._active = active
        if active:
            self.tray.setVisible(bool(self.cfg.get("show_tray")))
            self.hotkeys.set_hotkeys(self.cfg["hotkeys"])
        else:
            self.hotkeys.set_hotkeys({})
            self.player.hide_popup()
            self.tray.hide()

    def _ensure_port(self):
        """Яндекс Музыка запущена без порта управления (кнопок ♥ ⇄ ↻ нет) — перезапускаем её с портом.
        Если что-то играло, после перезапуска продолжим с того же места."""
        if not self.cfg.get("ym_control"):
            return
        if self.bridge.connected:
            self._restart_fails = 0
            return
        if self._restart_fails >= 2:
            return                                # приложение не открывает порт — не мучаем его
        if time.time() - self._last_restart < 45:
            return
        procs = ym_proc_objects()
        if not procs:
            return
        for p in procs:
            if p.info.get("exe"):
                if p.info["exe"] != self.cfg.get("ym_exe"):
                    self.cfg["ym_exe"] = p.info["exe"]
                    save_config(self.cfg)
                try:
                    if any(CDP_FLAG in a for a in p.cmdline()):
                        return                    # уже с портом — просто ждём подключения
                except Exception:
                    pass
        if not cdp_port_open():
            self._restart_fails += 1
            self.restart_ym()

    def patch_launchers(self):
        enable = bool(self.cfg.get("ym_control", True))
        known = self.cfg.get("ym_exe", "")

        def work():
            try:
                patch_ym_launchers(ym_exe_guess(known), enable)
            except Exception:
                log_error("patch shortcuts", traceback.format_exc())
        threading.Thread(target=work, daemon=True).start()

    def restart_ym(self, manual=False):
        if manual:
            self._restart_fails = 0
        self._last_restart = time.time()
        st = self.media._last_state or {}
        if st.get("ok") and st.get("playing"):
            # запомнить, что играло, чтобы продолжить после перезапуска
            self._resume = {"title": st.get("title"), "artist": st.get("artist"),
                            "pos": self.player.current_pos(), "until": time.time() + 40}
        else:
            self._resume = None
        threading.Thread(target=restart_ym_with_port, args=(self.cfg.get("ym_exe"),),
                         daemon=True).start()

    def _check_ym(self):
        running = self._media_ym or ym_process_running()
        if running:
            self._ensure_port()
        if not (self.cfg.get("launch_with_ym") or self.cfg.get("popup_on_ym_start")):
            self.ym_running = running
            return
        if running != self.ym_running:
            self.ym_running = running
            self.player.ym_open = running
            if running:
                self._apply_active()
                if self.cfg.get("popup_on_ym_start", False):
                    self.player.set_state(self.media._last_state or {"ok": False})
                    QTimer.singleShot(400, self.player.show_popup)
            else:
                self.manual = False
        self._apply_active()

    def _on_state(self, st):
        self._media_ym = bool(st.get("ok") and st.get("ym"))
        self.player.set_state(st)
        r = self._resume
        if r and time.time() > r["until"]:
            self._resume = r = None
        if (r and st.get("ok") and time.time() - self._last_restart > 3
                and st.get("title") == r["title"] and st.get("artist") == r["artist"]):
            self._resume = None
            if not st.get("playing"):
                self.media.command("play")
            pos = r["pos"]
            if pos > 2:
                QTimer.singleShot(900, lambda: self.media.seek(pos))

    def _on_second_instance(self):
        sock = self.server.nextPendingConnection()
        if sock is None:
            return
        sock.waitForReadyRead(300)
        data = bytes(sock.readAll())
        sock.disconnectFromServer()
        if data.startswith(b"show"):
            self.player.show_popup()          # повторный запуск — показать окно (для настроек)

    # --- трей и настройки
    def _update_tooltip(self):
        hk = self.cfg["hotkeys"].get("toggle")
        self.tray.setToolTip(f"{APP_NAME} — {display_binding(hk) if hk else 'сочетание не назначено'}")

    def _tray_click(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self.player.toggle()

    def _autostart(self, on):
        try:
            set_autostart(on)
        except Exception as e:
            QMessageBox.warning(None, APP_NAME, f"Не удалось изменить автозапуск: {e}")
            return
        if not on and self.cfg.get("launch_with_ym"):
            self.cfg["launch_with_ym"] = False
            save_config(self.cfg)
            self._active = None
            self._apply_active()

    def on_hotkey(self, act):
        if act == "toggle":
            self.player.toggle()
            return
        if act == "play_pause":
            self.player._on_play()
        elif act in ("next", "prev"):
            self.media.command(act)
        elif act in ("seek_fwd", "seek_back"):
            step = int(self.cfg.get("seek_step", 10))
            self.player.seek_relative(step if act == "seek_fwd" else -step)
        elif act in ("vol_up", "vol_down"):
            step = int(self.cfg.get("volume_step", 5))
            self.player.change_volume(step if act == "vol_up" else -step)
        elif act in ("like", "shuffle", "repeat"):
            self.player._ext_click(act)
        if self.cfg.get("show_on_hotkey", True):
            self.player.show_popup()

    def on_hotkeys_failed(self, keys):
        text = ("Не удалось назначить: " + ", ".join(display_binding(k) for k in keys) +
                ".\nСочетание занято другой программой — выберите другое в настройках.")
        if self.tray.isVisible():
            self.tray.showMessage(APP_NAME, text, QSystemTrayIcon.Warning, 6000)
        else:
            box = QMessageBox(QMessageBox.Warning, APP_NAME, text)
            box.setAttribute(Qt.WA_DeleteOnClose)
            box.setWindowFlags(box.windowFlags() | Qt.WindowStaysOnTopHint)
            box.show()
            self._warn_box = box

    def _popup_menu(self, pos):
        self.player._hide_timer.stop()
        self.restart_act.setVisible(bool(self.cfg.get("ym_control")) and not self.bridge.connected
                                    and self.ym_running)
        self.autorun_act.blockSignals(True)
        self.autorun_act.setChecked(is_autostart())
        self.autorun_act.blockSignals(False)
        self._menu.exec_(pos)
        if self.player.isVisible():
            self.player._restart_hide_timer()

    def open_settings(self):
        self.hotkeys.set_hotkeys({})          # чтобы поле записи могло «поймать» сочетания
        dlg = SettingsDialog(self.cfg)
        if dlg.exec_() == QDialog.Accepted:
            save_config(self.cfg)
            self.patch_launchers()            # включили/выключили кнопки — поправить ярлыки
            self.player.set_ext(self.player._ext)
            self.media.fallback = bool(self.cfg["any_player_fallback"])
            self._update_tooltip()
            if self.cfg["launch_with_ym"]:
                self._autostart(True)
                self.autorun_act.blockSignals(True)
                self.autorun_act.setChecked(True)
                self.autorun_act.blockSignals(False)
            if dlg.reset_pos and self.player.isVisible():
                self.player._place()
        self._active = None                   # переприменить сочетания / видимость
        self._apply_active()

    def diagnostics_text(self):
        procs = ym_processes()
        lines = [
            f"{APP_NAME}, Python {sys.version.split()[0]}, {sys.executable}",
            f"Запуск из автозагрузки: {BACKGROUND}; режим «вместе с ЯМ»: {self.cfg.get('launch_with_ym')}; "
            f"активен: {self._active}",
            "",
            "Процессы Яндекс Музыки: " + ("; ".join(f"{n} ({e})" for n, e in procs[:3]) if procs else "НЕ НАЙДЕНЫ"),
            "Медиа-сессии Windows: " + (", ".join(self.media.aumids) if self.media.aumids else "нет"),
            "Аудиосессии: " + (", ".join(sorted(set(self.volume.audio_names))) or "нет")
            + (f"  [pycaw: {self.volume.error}]" if self.volume.error else ""),
            "Горячие клавиши: " + self.hotkeys.status,
            "Сочетания: " + ", ".join(f"{k}={v}" for k, v in self.cfg["hotkeys"].items() if v),
        ]
        if self.media.error:
            lines += ["", "Последняя ошибка медиа:", self.media.error.strip()[-1500:]]
        return "\n".join(lines)

    def show_diagnostics(self):
        self.volume._ts = 0
        self.volume.get()
        text = self.diagnostics_text()
        box = QMessageBox()
        box.setWindowTitle(f"{APP_NAME} — диагностика")
        box.setText("Если что-то не работает — нажмите «Копировать» и пришлите текст.")
        box.setDetailedText(text)
        box.setInformativeText(text[:900])
        copy = box.addButton("Копировать", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Close)
        box.exec_()
        if box.clickedButton() is copy:
            QApplication.clipboard().setText(text)

    def quit(self):
        self.hotkeys.stop()
        self.tray.hide()
        self.app.quit()


SERVER_NAME = "YMMini-" + (os.environ.get("USERNAME") or os.environ.get("USER") or "user")


def main():
    if "--unpatch" in sys.argv:              # вызывается деинсталлятором
        try:
            patch_ym_launchers(ym_exe_guess(load_config().get("ym_exe", "")), False)
        except Exception:
            log_error("unpatch", traceback.format_exc())
        return
    if IS_WIN:
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("YMMini")
        except Exception:
            pass
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(tray_icon())

    # один экземпляр: если уже запущен — попросить его показаться и выйти
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if sock.waitForConnected(300):
        if not BACKGROUND:
            sock.write(b"show")
            sock.flush()
            sock.waitForBytesWritten(300)
        sock.disconnectFromServer()
        return
    QLocalServer.removeServer(SERVER_NAME)
    server = QLocalServer()
    server.listen(SERVER_NAME)

    ctl = Controller(app, server)  # noqa: F841
    sys.exit(app.exec_())


def _fatal(exc_type, exc, tb):
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    log_error("crash", text)
    sys.__stderr__ and sys.__stderr__.write(text)
    if IS_WIN:
        ctypes.windll.user32.MessageBoxW(None, "YM Mini упал с ошибкой:\n\n" + text[-1800:] +
                                         "\n\nПолный текст: " + log_path(), APP_NAME, 0x10)


if __name__ == "__main__":
    sys.excepthook = _fatal          # ошибки в обработчиках Qt тоже попадут в лог
    try:
        main()
    except SystemExit:
        raise
    except BaseException:
        _fatal(*sys.exc_info())
