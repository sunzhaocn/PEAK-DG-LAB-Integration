"""GitHub Release update checker and portable in-place updater for Coyote.

Update source: sunzhaocn/PEAK-DG-LAB-Integration GitHub Releases.
A compatible release must contain a .zip asset whose contents are the files
that should be copied into the current Coyote installation directory.

The running Windows executable is never overwritten in-place.  The update is
downloaded and extracted to a temporary staging directory first; a temporary
PowerShell helper waits for this process to exit, copies the staged files over
the current installation, and then restarts Coyote.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QMessageBox, QHBoxLayout, QLabel, QPushButton

import backend as B
from app_version import APP_VERSION


GITHUB_REPOSITORY = "sunzhaocn/PEAK-DG-LAB-Integration"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
RELEASES_URL = f"https://github.com/{GITHUB_REPOSITORY}/releases"
CHECK_DELAY_MS = 1800
REQUEST_TIMEOUT_SECONDS = 8
MAX_DOWNLOAD_BYTES = 1024 * 1024 * 1024  # 1 GiB safety ceiling
MAX_EXTRACTED_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB safety ceiling

_UI_INSTALLED = False
_VERSION_RE = re.compile(r"^[vV]?(\d+(?:\.\d+)*)")


def _version_tuple(value: str):
    text = str(value or "").strip()
    match = _VERSION_RE.match(text)
    if not match:
        return None
    try:
        parts = tuple(int(part) for part in match.group(1).split("."))
    except (TypeError, ValueError):
        return None
    parts = parts + (0,) * max(0, 3 - len(parts))
    while len(parts) > 3 and parts[-1] == 0:
        parts = parts[:-1]
    return parts


def compare_versions(left: str, right: str) -> int:
    """Return 1 if left>right, 0 if equal, -1 if left<right."""
    a = _version_tuple(left)
    b = _version_tuple(right)
    if a is None or b is None:
        raise ValueError(f"Unsupported version: {left!r} / {right!r}")
    width = max(len(a), len(b))
    a += (0,) * (width - len(a))
    b += (0,) * (width - len(b))
    return (a > b) - (a < b)


def is_newer_version(latest: str, current: str = APP_VERSION) -> bool:
    try:
        return compare_versions(latest, current) > 0
    except ValueError:
        return False


def _release_request(url: str):
    return urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"Coyote/{APP_VERSION}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="GET",
    )


def _fetch_latest_release():
    """Return (release, error_text). release is present even when not newer."""
    try:
        with urllib.request.urlopen(
            _release_request(LATEST_RELEASE_API),
            timeout=REQUEST_TIMEOUT_SECONDS,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None, "GitHub 仓库暂时没有可用的 Release。"
        return None, f"GitHub 返回 HTTP {exc.code}。"
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return None, f"无法连接 GitHub：{exc}"

    if not isinstance(payload, dict):
        return None, "GitHub 返回了无法识别的版本信息。"

    tag = str(payload.get("tag_name") or "").strip()
    if not tag or _version_tuple(tag) is None:
        return None, "最新 Release 没有可识别的版本号。"

    return {
        "tag": tag,
        "name": str(payload.get("name") or tag).strip() or tag,
        "url": str(payload.get("html_url") or RELEASES_URL).strip() or RELEASES_URL,
        "body": str(payload.get("body") or ""),
        "assets": payload.get("assets") if isinstance(payload.get("assets"), list) else [],
    }, ""


def _asset_score(asset):
    name = str(asset.get("name") or "").lower()
    if not name.endswith(".zip"):
        return -1
    score = 10
    if "coyote" in name:
        score += 20
    if "windows" in name or "win" in name:
        score += 20
    if "x64" in name or "amd64" in name:
        score += 10
    if "source" in name or "src" in name:
        score -= 30
    return score


def _select_update_asset(release):
    candidates = []
    for asset in release.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        url = str(asset.get("browser_download_url") or "").strip()
        score = _asset_score(asset)
        if score < 0 or not url:
            continue
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in {"github.com", "www.github.com"}:
            continue
        candidates.append((score, int(asset.get("size") or 0), asset))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return candidates[0][2]


def _install_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # src/Coyote/update_checker.py -> Coyote project root
    return Path(__file__).resolve().parents[2]


def _assert_install_root_writable(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    probe = root / f".coyote_update_write_test_{os.getpid()}"
    try:
        probe.write_text("ok", encoding="utf-8")
    finally:
        try:
            probe.unlink()
        except OSError:
            pass


def _safe_extract(zip_path: Path, output_dir: Path):
    total = 0
    with zipfile.ZipFile(zip_path, "r") as archive:
        infos = archive.infolist()
        if not infos:
            raise ValueError("更新 ZIP 是空文件。")
        for info in infos:
            name = info.filename.replace("\\", "/")
            pure = Path(name)
            if name.startswith("/") or pure.is_absolute() or ".." in pure.parts:
                raise ValueError(f"更新 ZIP 包含不安全路径：{info.filename}")
            total += max(0, int(info.file_size))
            if total > MAX_EXTRACTED_BYTES:
                raise ValueError("更新 ZIP 解压后的总大小超过安全限制。")
        archive.extractall(output_dir)


def _payload_root(extracted: Path) -> Path:
    entries = [entry for entry in extracted.iterdir() if entry.name != "__MACOSX"]
    files = [entry for entry in entries if entry.is_file()]
    dirs = [entry for entry in entries if entry.is_dir()]
    # Common GitHub/release packaging style: one wrapper directory.
    if not files and len(dirs) == 1:
        return dirs[0]
    return extracted


def _download_and_stage(release):
    asset = _select_update_asset(release)
    if asset is None:
        raise RuntimeError(
            "这个 Release 没有可用于自动安装的 ZIP 附件。\n"
            "发布新版时请在 Release Assets 上传 Windows ZIP，例如："
            "Coyote-Windows-x64-v0.0.2.zip。"
        )

    install_root = _install_root()
    _assert_install_root_writable(install_root)

    temp_root = Path(tempfile.mkdtemp(prefix="coyote-update-"))
    zip_path = temp_root / "update.zip"
    extracted = temp_root / "payload"
    extracted.mkdir(parents=True, exist_ok=True)

    download_url = str(asset.get("browser_download_url") or "").strip()
    expected_size = int(asset.get("size") or 0)
    if expected_size > MAX_DOWNLOAD_BYTES:
        raise RuntimeError("更新包超过 1 GiB 安全限制。")

    request = urllib.request.Request(
        download_url,
        headers={"User-Agent": f"Coyote/{APP_VERSION}", "Accept": "application/octet-stream"},
        method="GET",
    )
    downloaded = 0
    try:
        with urllib.request.urlopen(request, timeout=30) as response, zip_path.open("wb") as out:
            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    if int(content_length) > MAX_DOWNLOAD_BYTES:
                        raise RuntimeError("更新包超过 1 GiB 安全限制。")
                except ValueError:
                    pass
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                downloaded += len(chunk)
                if downloaded > MAX_DOWNLOAD_BYTES:
                    raise RuntimeError("更新下载超过 1 GiB 安全限制。")
                out.write(chunk)
        if expected_size and downloaded != expected_size:
            raise RuntimeError(f"更新包下载不完整：应为 {expected_size} 字节，实际 {downloaded} 字节。")
        _safe_extract(zip_path, extracted)
        payload = _payload_root(extracted)
        if not any(payload.iterdir()):
            raise RuntimeError("更新包解压后没有可安装文件。")
        return {
            "temp_root": str(temp_root),
            "payload_root": str(payload),
            "install_root": str(install_root),
            "asset_name": str(asset.get("name") or zip_path.name),
            "downloaded": downloaded,
        }
    except Exception:
        shutil.rmtree(temp_root, ignore_errors=True)
        raise


def _write_update_helper(stage):
    temp_root = Path(stage["temp_root"])
    payload_root = Path(stage["payload_root"])
    install_root = Path(stage["install_root"])
    script = temp_root / "apply-coyote-update.ps1"

    restart_exe = Path(sys.executable).resolve()
    restart_arg = ""
    if not getattr(sys, "frozen", False):
        restart_arg = str(Path(__file__).resolve().with_name("main.py"))

    ps = r'''param(
    [int]$PidToWait,
    [string]$SourceDir,
    [string]$TargetDir,
    [string]$RestartExe,
    [string]$RestartArg
)
$ErrorActionPreference = "Stop"
try { Wait-Process -Id $PidToWait -ErrorAction SilentlyContinue } catch {}
Start-Sleep -Milliseconds 700
New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null
Get-ChildItem -LiteralPath $SourceDir -Force | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $TargetDir -Recurse -Force
}
if (Test-Path -LiteralPath $RestartExe) {
    if ([string]::IsNullOrWhiteSpace($RestartArg)) {
        Start-Process -FilePath $RestartExe -WorkingDirectory $TargetDir
    } else {
        Start-Process -FilePath $RestartExe -ArgumentList @($RestartArg) -WorkingDirectory $TargetDir
    }
}
'''
    script.write_text(ps, encoding="utf-8-sig")
    return script, restart_exe, restart_arg, payload_root, install_root


def _launch_installer(stage):
    script, restart_exe, restart_arg, payload_root, install_root = _write_update_helper(stage)
    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-PidToWait",
        str(os.getpid()),
        "-SourceDir",
        str(payload_root),
        "-TargetDir",
        str(install_root),
        "-RestartExe",
        str(restart_exe),
        "-RestartArg",
        str(restart_arg),
    ]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(command, close_fds=True, creationflags=creationflags)


class _UpdateBridge(QObject):
    check_finished = Signal(object, str, bool)  # release, error, manual
    stage_finished = Signal(object, str)  # stage, error


def install_ui(UI):
    """Add startup checking plus a manual update panel to the existing UI."""
    global _UI_INSTALLED
    if _UI_INSTALLED:
        return
    _UI_INSTALLED = True

    BaseWindow = UI.Window

    class UpdateAwareWindow(BaseWindow):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._update_check_running = False
            self._update_install_running = False
            self._update_bridge = _UpdateBridge(self)
            self._update_bridge.check_finished.connect(self._on_update_check_finished)
            self._update_bridge.stage_finished.connect(self._on_update_stage_finished)
            self._build_update_panel(UI)
            QTimer.singleShot(CHECK_DELAY_MS, lambda: self._check_for_updates(manual=False))

        def _build_update_panel(self, UI):
            try:
                scroll = self.look.findChild(UI.QScrollArea)
                body = scroll.widget() if scroll is not None else None
                layout = body.layout() if body is not None else None
                if layout is None:
                    return

                title = UI.QLabel("软件更新")
                title.setStyleSheet("font-size:18px;font-weight:700")
                panel, panel_layout = self.panel("版本与更新")
                self.update_version_label = UI.QLabel(f"当前版本：v{APP_VERSION}")
                self.update_version_label.setStyleSheet("font-size:15px;font-weight:700")
                self.update_status_label = UI.QLabel("可手动检查 GitHub Release；启动时也会静默检查一次。")
                self.update_status_label.setObjectName("muted")
                self.update_status_label.setWordWrap(True)

                row = QHBoxLayout()
                self.check_update_button = QPushButton("检查更新")
                self.check_update_button.setObjectName("primary")
                self.check_update_button.clicked.connect(lambda: self._check_for_updates(manual=True))
                row.addWidget(self.check_update_button)
                row.addStretch(1)

                panel_layout.addWidget(self.update_version_label)
                panel_layout.addWidget(self.update_status_label)
                panel_layout.addLayout(row)

                # Put the updater at the top of Settings so it is easy to find.
                layout.insertWidget(0, panel)
                layout.insertWidget(0, title)
            except Exception as exc:
                try:
                    B.add_log("错误", "更新面板创建失败", str(exc))
                except Exception:
                    pass

        def _set_update_busy(self, busy, text=""):
            self._update_check_running = bool(busy)
            if hasattr(self, "check_update_button"):
                self.check_update_button.setEnabled(not busy and not self._update_install_running)
                self.check_update_button.setText("正在检查…" if busy else "检查更新")
            if text and hasattr(self, "update_status_label"):
                self.update_status_label.setText(text)

        def _check_for_updates(self, manual=False):
            if self._update_check_running or self._update_install_running:
                if manual:
                    self.feedback("版本检查正在进行中。", 2500)
                return
            self._set_update_busy(True, "正在连接 GitHub 检查最新版本……")

            def worker():
                release, error = _fetch_latest_release()
                self._update_bridge.check_finished.emit(release, error, bool(manual))

            threading.Thread(target=worker, name="CoyoteUpdateChecker", daemon=True).start()

        def _on_update_check_finished(self, release, error, manual):
            self._set_update_busy(False)
            if error:
                if hasattr(self, "update_status_label"):
                    self.update_status_label.setText(error)
                if manual:
                    self.msg_warning("检查更新失败", "无法完成版本检查。", error)
                return
            if not isinstance(release, dict):
                return

            latest = str(release.get("tag") or "").strip()
            if not is_newer_version(latest):
                text = f"当前已是最新版本：v{APP_VERSION}"
                if hasattr(self, "update_status_label"):
                    self.update_status_label.setText(text)
                if manual:
                    self.msg_info("没有新版本", text, f"GitHub 最新版本：{latest}")
                return

            if hasattr(self, "update_status_label"):
                self.update_status_label.setText(f"发现新版本：{latest}（当前 v{APP_VERSION}）")
            self._show_update_prompt(release)

        def _show_update_prompt(self, release):
            latest = str(release.get("tag") or "").strip()
            asset = _select_update_asset(release)
            asset_text = str(asset.get("name") or "") if asset else "未找到自动安装 ZIP"

            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Information)
            box.setWindowTitle("发现新版本")
            box.setText(f"发现 Coyote 新版本 {latest}")
            box.setInformativeText(
                f"当前版本：v{APP_VERSION}\n"
                f"最新版本：{latest}\n"
                f"更新包：{asset_text}\n\n"
                "点击“立即更新”后，程序会下载并解压更新包；随后自动退出，"
                "覆盖当前安装文件夹并重新启动。"
            )
            update_button = box.addButton("立即更新", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("稍后", QMessageBox.ButtonRole.RejectRole)
            if asset is None:
                update_button.setEnabled(False)
            box.exec()
            if box.clickedButton() is update_button and asset is not None:
                self._start_download_and_install(release)

        def _start_download_and_install(self, release):
            if self._update_install_running:
                return
            self._update_install_running = True
            self._set_update_busy(False, "正在下载并解压更新包，请勿关闭程序……")
            if hasattr(self, "check_update_button"):
                self.check_update_button.setEnabled(False)
                self.check_update_button.setText("正在准备更新…")
            self.feedback("正在下载并准备更新……", 5000)

            def worker():
                try:
                    stage = _download_and_stage(release)
                    self._update_bridge.stage_finished.emit(stage, "")
                except Exception as exc:
                    self._update_bridge.stage_finished.emit(None, f"{type(exc).__name__}: {exc}")

            threading.Thread(target=worker, name="CoyoteUpdateDownloader", daemon=True).start()

        def _on_update_stage_finished(self, stage, error):
            if error or not isinstance(stage, dict):
                self._update_install_running = False
                if hasattr(self, "check_update_button"):
                    self.check_update_button.setEnabled(True)
                    self.check_update_button.setText("检查更新")
                if hasattr(self, "update_status_label"):
                    self.update_status_label.setText("更新失败，可稍后重新检查。")
                self.msg_error("更新失败", "更新包下载或解压失败。", error or "未知错误")
                return

            try:
                _launch_installer(stage)
            except Exception as exc:
                self._update_install_running = False
                self.msg_error("启动更新程序失败", "更新已经下载，但无法启动覆盖安装。", str(exc))
                return

            if hasattr(self, "update_status_label"):
                self.update_status_label.setText("更新已准备完成，正在退出并自动安装……")
            self.feedback("更新已下载完成。程序将退出、覆盖安装并自动重启。", 4000)
            try:
                B.add_log("系统", "准备安装更新", f"当前 v{APP_VERSION}")
            except Exception:
                pass
            QTimer.singleShot(350, UI.QApplication.instance().quit)

    UI.Window = UpdateAwareWindow
    UI.APP_VERSION = APP_VERSION
