"""Set the Coyote runtime/window icon without changing application behaviour."""
from __future__ import annotations

import ctypes
import sys
from pathlib import Path

import backend as B

_INSTALLED = False
_APP_USER_MODEL_ID = "Coyote.PEAK.DGLAB"


def _set_windows_app_id() -> None:
    """Give Windows a stable identity for taskbar grouping and icon lookup."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            _APP_USER_MODEL_ID
        )
    except Exception:
        pass


def _file_icon(QIcon):
    """Load the project icon when running from source or an unpacked build."""
    candidates = []
    try:
        candidates.append(Path(B.ROOT) / "icon.ico")
    except Exception:
        pass
    try:
        candidates.append(Path(__file__).resolve().parents[4] / "icon.ico")
    except Exception:
        pass
    try:
        candidates.append(Path(sys.executable).resolve().parent / "icon.ico")
    except Exception:
        pass

    seen = set()
    for path in candidates:
        try:
            path = path.resolve()
        except Exception:
            continue
        if path in seen:
            continue
        seen.add(path)
        if not path.is_file():
            continue
        icon = QIcon(str(path))
        if not icon.isNull():
            return icon
    return QIcon()


def _windows_executable_icon(QIcon, QImage, QPixmap):
    """Read the icon embedded in Coyote.exe through the Windows shell API."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return QIcon()

    large = (ctypes.c_void_p * 1)()
    small = (ctypes.c_void_p * 1)()
    handles = []
    try:
        extract = ctypes.windll.shell32.ExtractIconExW
        extract.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_uint,
        ]
        extract.restype = ctypes.c_uint
        count = extract(str(Path(sys.executable).resolve()), 0, large, small, 1)
        if count <= 0:
            return QIcon()

        for handle in (large[0], small[0]):
            if handle and handle not in handles:
                handles.append(handle)

        for handle in handles:
            image = QImage.fromHICON(int(handle))
            if image.isNull():
                continue
            pixmap = QPixmap.fromImage(image)
            if not pixmap.isNull():
                return QIcon(pixmap)
    except Exception:
        return QIcon()
    finally:
        try:
            destroy = ctypes.windll.user32.DestroyIcon
            destroy.argtypes = [ctypes.c_void_p]
            for handle in handles:
                destroy(ctypes.c_void_p(handle))
        except Exception:
            pass
    return QIcon()


def install_ui(UI) -> None:
    """Install after all other Window wrappers and before UI.main() runs."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    _set_windows_app_id()
    BaseWindow = UI.Window

    class IconWindow(BaseWindow):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            try:
                from PySide6.QtGui import QIcon, QImage, QPixmap
                from PySide6.QtWidgets import QApplication

                icon = _file_icon(QIcon)
                if icon.isNull():
                    icon = _windows_executable_icon(QIcon, QImage, QPixmap)
                if icon.isNull():
                    return

                app = QApplication.instance()
                if app is not None:
                    app.setWindowIcon(icon)
                self.setWindowIcon(icon)
            except Exception as exc:
                try:
                    B.add_log("系统", "窗口图标加载失败", repr(exc))
                except Exception:
                    pass

    UI.Window = IconWindow
