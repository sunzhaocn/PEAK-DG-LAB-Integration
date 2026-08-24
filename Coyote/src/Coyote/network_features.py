"""Network / relay extension for Coyote.

Adds five phone/controller network modes without rewriting backend.py/ui_qt.py:
- Local LAN (default / preferred)
- Virtual LAN (Tailscale / ZeroTier / similar)
- Official WSS relay (explicit opt-in)
- Custom WS/WSS relay
- Public direct (user-managed port forwarding / firewall)

The official relay identity comes from official_relay.json, not the user's normal
settings file, so "Restore official relay" always restores the shipped channel.
"""
from __future__ import annotations

import ipaddress
import json
import socket
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import websocket

import backend as B
from relay_config import official_relay

_BACKEND_INSTALLED = False
_UI_INSTALLED = False
_ORIGINAL_LOAD_CONFIG = None
_ORIGINAL_SAVE_CONFIG = None

MODE_OFFICIAL = "official_relay"
MODE_CUSTOM = "custom_relay"
MODE_LAN = "lan"
MODE_VIRTUAL = "virtual_lan"
MODE_PUBLIC = "public_direct"

MODE_LABELS = {
    MODE_LAN: "局域网直连（默认）",
    MODE_VIRTUAL: "虚拟组网",
    MODE_OFFICIAL: "官方 WSS 中继",
    MODE_CUSTOM: "自定义 WS/WSS 中继",
    MODE_PUBLIC: "公网直连",
}
LOCAL_SERVER_MODES = {MODE_LAN, MODE_VIRTUAL, MODE_PUBLIC}

NETWORK_DEFAULTS = {
    # Privacy / traffic default: stay on the user's LAN unless they explicitly
    # select an Internet relay mode. The official relay address is shipped
    # separately, but is never auto-selected as a fallback.
    "connection_mode": MODE_LAN,
    "custom_relay_url": "",
    "manual_host": "",
    "direct_host_source": "auto",
}

_DETECT_LOCK = threading.RLock()
_DETECT = {
    "local_ipv4": "",
    "local_ipv6": "",
    "public_ipv4": "",
    "public_ipv6": "",
    "updated_at": 0.0,
    "running": False,
    "error": "",
}


def _s(value) -> str:
    return str(value or "").strip()


def _json_read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _json_write(path: Path, data: dict) -> tuple[bool, str]:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True, "配置已保存"
    except Exception as exc:
        return False, str(exc)


def official_name_url() -> tuple[str, str]:
    name, raw = official_relay()
    ok, url_or_error = normalize_relay_url(raw)
    if ok:
        return name, url_or_error
    return name, _s(raw)


def normalize_relay_url(value) -> tuple[bool, str]:
    raw = _s(value)
    if not raw:
        return False, "中继地址不能为空"
    if "://" not in raw:
        raw = "wss://" + raw
    try:
        parts = urlsplit(raw)
    except Exception as exc:
        return False, f"地址格式错误：{exc}"
    if parts.scheme.lower() not in {"ws", "wss"}:
        return False, "中继地址只允许 ws:// 或 wss://"
    if not parts.hostname:
        return False, "中继地址缺少主机名/IP"
    if parts.username or parts.password:
        return False, "中继地址不允许包含用户名或密码"
    if parts.query or parts.fragment:
        return False, "中继基础地址不要包含 ?query 或 #fragment"
    scheme = parts.scheme.lower()
    host = parts.hostname
    try:
        port = parts.port
    except ValueError:
        return False, "端口格式无效"
    if port is not None and not 1 <= int(port) <= 65535:
        return False, "端口必须在 1~65535"
    try:
        ip_obj = ipaddress.ip_address(host)
        display_host = f"[{host}]" if ip_obj.version == 6 else host
    except ValueError:
        display_host = host.lower()
    netloc = display_host + (f":{port}" if port is not None else "")
    path = parts.path or ""
    if path == "/":
        path = ""
    else:
        path = path.rstrip("/")
    return True, urlunsplit((scheme, netloc, path, "", ""))


def _clean_host_input(value: str) -> str:
    raw = _s(value)
    if not raw:
        return ""
    if "://" in raw:
        try:
            return urlsplit(raw).hostname or ""
        except Exception:
            return ""
    if raw.startswith("["):
        end = raw.find("]")
        if end > 0:
            return raw[1:end]
    # host:port (but never split an unbracketed IPv6 literal)
    if raw.count(":") == 1:
        host, maybe_port = raw.rsplit(":", 1)
        if maybe_port.isdigit():
            return host
    return raw


def _host_for_url(host: str) -> str:
    host = _clean_host_input(host)
    if not host:
        return ""
    try:
        return f"[{host}]" if ipaddress.ip_address(host).version == 6 else host
    except ValueError:
        return host


def _mode() -> str:
    mode = _s(B.network_settings.get("connection_mode", MODE_LAN))
    return mode if mode in MODE_LABELS else MODE_LAN


def active_controller_url() -> str:
    """URL used by Coyote itself (controller role)."""
    mode = _mode()
    if mode == MODE_OFFICIAL:
        _, url = official_name_url()
        return url
    if mode == MODE_CUSTOM:
        ok, value = normalize_relay_url(B.network_settings.get("custom_relay_url", ""))
        if ok:
            return value
        _, url = official_name_url()
        return url
    # Direct modes keep controller -> local relay on loopback.
    return f"ws://127.0.0.1:{int(B.DG_PORT)}"


def _best_direct_host(mode: str) -> str:
    manual = _s(B.network_settings.get("manual_host", ""))
    source = _s(B.network_settings.get("direct_host_source", "auto")) or "auto"
    snap = network_snapshot()

    if source == "manual" and manual:
        return manual
    if source == "ipv6" and snap.get("local_ipv6"):
        return snap["local_ipv6"]
    if source == "public_ipv4" and snap.get("public_ipv4"):
        return snap["public_ipv4"]
    if source == "public_ipv6" and snap.get("public_ipv6"):
        return snap["public_ipv6"]

    if mode == MODE_PUBLIC:
        return manual or snap.get("public_ipv6") or snap.get("public_ipv4") or snap.get("local_ipv4") or B.LAN_IP
    if mode == MODE_VIRTUAL:
        return manual or snap.get("local_ipv4") or B.LAN_IP
    return snap.get("local_ipv4") or B.LAN_IP or "127.0.0.1"


def phone_ws_url(controller_id=None):
    if controller_id is None:
        with B.dg_lock:
            controller_id = B.dg.get("controller_id")
    if not controller_id:
        return None

    mode = _mode()
    if mode in {MODE_OFFICIAL, MODE_CUSTOM}:
        base = active_controller_url().rstrip("/")
        return f"{base}?tid={quote(str(controller_id), safe='')}"

    host = _host_for_url(_best_direct_host(mode))
    if not host:
        return None
    return f"ws://{host}:{int(B.DG_PORT)}/?tid={quote(str(controller_id), safe='')}"


def pairing_url(controller_id=None):
    ws_url = phone_ws_url(controller_id)
    if not ws_url:
        return None
    return "https://dungeon-lab.cn/s/?v=1&action=socket&url=" + quote(ws_url, safe="")


def _local_ipv4() -> str:
    for target in (("8.8.8.8", 80), ("1.1.1.1", 80)):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(target)
            value = sock.getsockname()[0]
            if value and not value.startswith(("127.", "169.254.")):
                return value
        except OSError:
            pass
        finally:
            sock.close()
    try:
        values = socket.gethostbyname_ex(socket.gethostname())[2]
        for value in values:
            if value and not value.startswith(("127.", "169.254.")):
                return value
    except OSError:
        pass
    return ""


def _local_ipv6() -> str:
    # First use route selection; this filters out link-local addresses naturally.
    sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
    try:
        sock.connect(("2001:4860:4860::8888", 80, 0, 0))
        value = sock.getsockname()[0].split("%", 1)[0]
        obj = ipaddress.ip_address(value)
        if not (obj.is_loopback or obj.is_link_local or obj.is_unspecified):
            return value
    except OSError:
        pass
    finally:
        sock.close()
    try:
        for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET6):
            value = item[4][0].split("%", 1)[0]
            obj = ipaddress.ip_address(value)
            if not (obj.is_loopback or obj.is_link_local or obj.is_unspecified):
                return value
    except Exception:
        pass
    return ""


def _fetch_ip(url: str, version: int) -> str:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Coyote-Network/1"})
        with urllib.request.urlopen(req, timeout=3.5) as resp:
            value = resp.read(128).decode("ascii", errors="ignore").strip()
        obj = ipaddress.ip_address(value)
        return value if obj.version == version else ""
    except Exception:
        return ""


def detect_network_async(force=False) -> None:
    now = time.time()
    with _DETECT_LOCK:
        if _DETECT["running"]:
            return
        if not force and _DETECT["updated_at"] and now - _DETECT["updated_at"] < 60:
            return
        _DETECT["running"] = True
        _DETECT["error"] = ""

    def worker():
        local4 = _local_ipv4()
        local6 = _local_ipv6()
        public4 = _fetch_ip("https://api4.ipify.org", 4)
        public6 = _fetch_ip("https://api6.ipify.org", 6)
        with _DETECT_LOCK:
            _DETECT.update({
                "local_ipv4": local4,
                "local_ipv6": local6,
                "public_ipv4": public4,
                "public_ipv6": public6,
                "updated_at": time.time(),
                "running": False,
                "error": "" if (local4 or local6 or public4 or public6) else "未检测到可用地址",
            })

    threading.Thread(target=worker, name="CoyoteNetworkDetect", daemon=True).start()


def network_snapshot() -> dict:
    with _DETECT_LOCK:
        return dict(_DETECT)


def public_direct_summary() -> str:
    snap = network_snapshot()
    p4 = snap.get("public_ipv4") or "无"
    p6 = snap.get("public_ipv6") or "无"
    if p4 == "无" and p6 == "无":
        return "未知/不可用"
    notes = []
    if p6 != "无":
        notes.append("IPv6 可作为直连候选（仍需防火墙放行）")
    if p4 != "无":
        notes.append("IPv4 已检测到公网出口；是否可入站仍取决于 NAT/端口映射")
    return "；".join(notes)


def _persist_network_only() -> tuple[bool, str]:
    data = _json_read(B.CONFIG_FILE) if B.CONFIG_FILE.exists() else {}
    network = data.get("network")
    if not isinstance(network, dict):
        network = {}
    network.update({
        "peak_port": int(B.network_settings.get("peak_port", B.DEFAULT_PEAK_PORT)),
        "dg_port": int(B.network_settings.get("dg_port", B.DEFAULT_DG_PORT)),
        "peak_game_dir": _s(B.network_settings.get("peak_game_dir", "")),
        "connection_mode": _mode(),
        "custom_relay_url": _s(B.network_settings.get("custom_relay_url", "")),
        "manual_host": _s(B.network_settings.get("manual_host", "")),
        "direct_host_source": _s(B.network_settings.get("direct_host_source", "auto")) or "auto",
    })
    data["network"] = network
    return _json_write(B.CONFIG_FILE, data)


def _load_network_extension() -> None:
    for key, value in NETWORK_DEFAULTS.items():
        B.network_settings.setdefault(key, value)
    data = _json_read(B.CONFIG_FILE) if B.CONFIG_FILE.exists() else {}
    network = data.get("network") or {}
    if not isinstance(network, dict):
        return
    mode = _s(network.get("connection_mode", MODE_LAN))
    B.network_settings["connection_mode"] = mode if mode in MODE_LABELS else MODE_LAN
    B.network_settings["custom_relay_url"] = _s(network.get("custom_relay_url", ""))
    B.network_settings["manual_host"] = _s(network.get("manual_host", ""))
    source = _s(network.get("direct_host_source", "auto")) or "auto"
    B.network_settings["direct_host_source"] = source


def request_reconnect(reason="网络设置已更改") -> None:
    try:
        ws = B.dg_ws
        if ws is not None:
            ws.close()
    except Exception:
        pass
    with B.dg_lock:
        B.dg["server"] = "正在重连"
        B.dg["error"] = ""
    try:
        B.add_log("连接", "切换连接通道", f"{reason}; {active_controller_url()}")
    except Exception:
        pass


def _network_websocket_loop() -> None:
    """Drop-in replacement for backend.websocket_loop."""
    while not B.stop_event.is_set():
        mode = _mode()
        url = active_controller_url()
        B.DG_URL = url

        if mode in LOCAL_SERVER_MODES:
            if not B.server_running():
                B.start_server()
            if not B.server_running():
                time.sleep(1)
                continue
        else:
            with B.dg_lock:
                if not B.dg.get("controller_id"):
                    label = MODE_LABELS.get(mode, mode)
                    B.dg["server"] = f"正在连接{label}"

        try:
            B.dg_ws = websocket.WebSocketApp(
                url,
                on_open=B.on_open,
                on_message=B.on_message,
                on_error=B.on_error,
                on_close=B.on_close,
            )
            B.dg_ws.run_forever(ping_interval=20, ping_timeout=10)
        except Exception as exc:
            with B.dg_lock:
                B.dg["server"] = "线程异常"
                B.dg["error"] = str(exc)
            B.add_log("错误", "WebSocket 线程异常", str(exc))
        finally:
            B.dg_ws = None
        if not B.stop_event.is_set():
            time.sleep(1)


def install_backend() -> None:
    global _BACKEND_INSTALLED, _ORIGINAL_LOAD_CONFIG, _ORIGINAL_SAVE_CONFIG
    if _BACKEND_INSTALLED:
        return
    _BACKEND_INSTALLED = True

    for key, value in NETWORK_DEFAULTS.items():
        B.network_settings.setdefault(key, value)

    _ORIGINAL_LOAD_CONFIG = B.load_config
    _ORIGINAL_SAVE_CONFIG = getattr(B, "save_config", None)

    def load_config_wrapper():
        result = _ORIGINAL_LOAD_CONFIG()
        _load_network_extension()
        return result

    B.load_config = load_config_wrapper

    if callable(_ORIGINAL_SAVE_CONFIG):
        def save_config_wrapper(*args, **kwargs):
            result = _ORIGINAL_SAVE_CONFIG(*args, **kwargs)
            _persist_network_only()
            return result
        B.save_config = save_config_wrapper

    B.phone_ws_url = phone_ws_url
    B.pairing_url = pairing_url
    B.websocket_loop = _network_websocket_loop
    B.active_controller_url = active_controller_url
    B.network_snapshot = network_snapshot
    B.detect_network_async = detect_network_async
    B.request_network_reconnect = request_reconnect
    B.public_direct_summary = public_direct_summary

    _load_network_extension()
    detect_network_async(force=True)


def install_ui(UI) -> None:
    global _UI_INSTALLED
    if _UI_INSTALLED:
        return
    _UI_INSTALLED = True

    # Preserve all previously installed Window extensions (extended/multiplayer).
    BaseWindow = UI.Window
    original_save_full_config = UI.save_full_config

    def save_full_config_wrapper():
        ok, message = original_save_full_config()
        if not ok:
            return ok, message
        net_ok, net_message = _persist_network_only()
        return (True, message) if net_ok else (False, net_message)

    UI.save_full_config = save_full_config_wrapper

    class NetworkWindow(BaseWindow):
        relay_test_finished = UI.Signal(bool, str, str)

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.relay_test_finished.connect(self._network_test_done)

        def build_pair(self):
            outer = UI.QVBoxLayout(self.pair)
            outer.setContentsMargins(4, 4, 4, 4)
            outer.setSpacing(10)

            top = UI.QHBoxLayout()
            a, al = self.panel("DG-LAB 配对")
            self.qr = UI.QLabel("等待控制方 ID...")
            self.qr.setAlignment(UI.Qt.AlignmentFlag.AlignCenter)
            self.qr.setMinimumSize(340, 340)
            al.addWidget(self.qr, 1)
            self.url = UI.QLineEdit()
            self.url.setReadOnly(True)
            al.addWidget(self.url)
            cp = UI.QPushButton("复制配对地址")
            cp.clicked.connect(self.copy_pair_url)
            al.addWidget(cp, alignment=UI.Qt.AlignmentFlag.AlignRight)

            b, bl = self.panel("连接详细信息")
            f = UI.QFormLayout()
            self.detail = {}
            for key, name in (
                ("server", "DG Server / Relay"),
                ("controller", "控制方 ID"),
                ("app", "APP ID"),
                ("device", "郊狼设备"),
                ("slot", "slotId"),
                ("error", "错误"),
            ):
                value = UI.QLabel("-")
                value.setWordWrap(True)
                self.detail[key] = value
                f.addRow(name, value)
            bl.addLayout(f)
            bl.addStretch(1)
            top.addWidget(a, 1)
            top.addWidget(b, 1)
            outer.addLayout(top)

            panel, pl = self.panel("网络 / 中继通道")
            note = UI.QLabel(
                "首次启动默认使用局域网直连，不会自动把连接切到公网。只有用户明确选择“官方 WSS 中继”"
                "或“自定义 WS/WSS 中继”时才走公网中继。局域网/虚拟组网/公网直连会在本机启动 v4-server；"
                "公网 IPv4 即使能检测到，也不代表路由器已允许入站，公网直连需自行配置端口映射和防火墙。"
            )
            note.setObjectName("muted")
            note.setWordWrap(True)
            pl.addWidget(note)

            # V2.3: 网络设置改为左右并排，而不是一个 QFormLayout 从上到下串行。
            self.net_mode = UI.QComboBox()
            for mode, label in MODE_LABELS.items():
                self.net_mode.addItem(label, mode)
            current_mode = _mode()
            idx = self.net_mode.findData(current_mode)
            self.net_mode.setCurrentIndex(max(0, idx))

            official_name, official_url = official_name_url()
            self.net_official = UI.QLabel(f"{official_name} · {official_url}")
            self.net_official.setTextInteractionFlags(UI.Qt.TextInteractionFlag.TextSelectableByMouse)
            self.net_official.setWordWrap(True)

            self.net_custom = UI.QLineEdit(_s(B.network_settings.get("custom_relay_url", "")))
            self.net_custom.setPlaceholderText("wss://relay.example.com")
            self.net_manual_host = UI.QLineEdit(_s(B.network_settings.get("manual_host", "")))
            self.net_manual_host.setPlaceholderText("Tailscale IP / 公网 IP / IPv6 / 域名")

            self.net_source = UI.QComboBox()
            for label, value in (
                ("自动选择", "auto"),
                ("手动地址", "manual"),
                ("本机 IPv6", "ipv6"),
                ("公网 IPv4", "public_ipv4"),
                ("公网 IPv6", "public_ipv6"),
            ):
                self.net_source.addItem(label, value)
            source = _s(B.network_settings.get("direct_host_source", "auto")) or "auto"
            sidx = self.net_source.findData(source)
            self.net_source.setCurrentIndex(max(0, sidx))

            self.net_local4 = UI.QLabel("检测中…")
            self.net_local6 = UI.QLabel("检测中…")
            self.net_public4 = UI.QLabel("检测中…")
            self.net_public6 = UI.QLabel("检测中…")
            self.net_public_state = UI.QLabel("检测中…")
            self.net_current = UI.QLabel(active_controller_url())
            self.net_current.setTextInteractionFlags(UI.Qt.TextInteractionFlag.TextSelectableByMouse)
            self.net_current.setWordWrap(True)

            # 左栏：只放“选择/配置”；右栏：只放“检测/状态”。
            columns = UI.QHBoxLayout()
            columns.setSpacing(12)

            config_box = UI.QGroupBox("连接方式")
            config_form = UI.QFormLayout(config_box)
            config_form.setFieldGrowthPolicy(UI.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            config_form.setLabelAlignment(UI.Qt.AlignmentFlag.AlignRight | UI.Qt.AlignmentFlag.AlignVCenter)
            config_form.addRow("连接模式", self.net_mode)

            self.net_official_label = UI.QLabel("官方通道")
            self.net_custom_label = UI.QLabel("自定义 Relay")
            self.net_source_label = UI.QLabel("直连地址来源")
            self.net_manual_label = UI.QLabel("手动主机/IP")

            config_form.addRow(self.net_official_label, self.net_official)
            config_form.addRow(self.net_custom_label, self.net_custom)
            config_form.addRow(self.net_source_label, self.net_source)
            config_form.addRow(self.net_manual_label, self.net_manual_host)

            status_box = UI.QGroupBox("网络检测 / 当前状态")
            status_form = UI.QFormLayout(status_box)
            status_form.setFieldGrowthPolicy(UI.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            status_form.setLabelAlignment(UI.Qt.AlignmentFlag.AlignRight | UI.Qt.AlignmentFlag.AlignVCenter)
            status_form.addRow("本机 IPv4", self.net_local4)
            status_form.addRow("本机 IPv6", self.net_local6)
            status_form.addRow("公网 IPv4", self.net_public4)
            status_form.addRow("公网 IPv6", self.net_public6)
            status_form.addRow("公网直连判断", self.net_public_state)
            status_form.addRow("当前控制端地址", self.net_current)

            columns.addWidget(config_box, 1)
            columns.addWidget(status_box, 1)
            pl.addLayout(columns)

            # 选择模式时即时隐藏无关输入，避免“所有模式控件一起串着显示”。
            self.net_mode.currentIndexChanged.connect(self._network_update_mode_fields)
            self.net_source.currentIndexChanged.connect(self._network_update_mode_fields)
            self._network_update_mode_fields()

            buttons = UI.QHBoxLayout()
            detect = UI.QPushButton("重新自动检测")
            detect.clicked.connect(self.network_redetect)
            test = UI.QPushButton("测试中继")
            test.clicked.connect(self.network_test_relay)
            apply_btn = UI.QPushButton("应用并重连")
            apply_btn.setObjectName("primary")
            apply_btn.clicked.connect(self.network_apply)
            restore = UI.QPushButton("切换 / 恢复官方中继")
            restore.clicked.connect(self.network_restore_official)
            buttons.addWidget(detect)
            buttons.addWidget(test)
            buttons.addStretch(1)
            buttons.addWidget(restore)
            buttons.addWidget(apply_btn)
            pl.addLayout(buttons)
            outer.addWidget(panel)
            self._network_last_ui_refresh = 0.0
            detect_network_async(force=False)

        def _network_update_mode_fields(self, *args):
            mode = self.net_mode.currentData() or MODE_LAN
            source = self.net_source.currentData() or "auto"

            is_official = mode == MODE_OFFICIAL
            is_custom = mode == MODE_CUSTOM
            is_direct = mode in (MODE_LAN, MODE_VIRTUAL, MODE_PUBLIC)

            for widget in (self.net_official_label, self.net_official):
                widget.setVisible(is_official)
            for widget in (self.net_custom_label, self.net_custom):
                widget.setVisible(is_custom)
            for widget in (self.net_source_label, self.net_source):
                widget.setVisible(is_direct)

            show_manual = is_direct and source == "manual"
            for widget in (self.net_manual_label, self.net_manual_host):
                widget.setVisible(show_manual)

        def _network_collect(self):
            mode = self.net_mode.currentData() or MODE_LAN
            custom = self.net_custom.text().strip()
            manual = self.net_manual_host.text().strip()
            source = self.net_source.currentData() or "auto"
            if mode == MODE_CUSTOM:
                ok, value = normalize_relay_url(custom)
                if not ok:
                    return False, value
                custom = value
            B.network_settings["connection_mode"] = mode
            B.network_settings["custom_relay_url"] = custom
            B.network_settings["manual_host"] = manual
            B.network_settings["direct_host_source"] = source
            return True, ""

        def network_apply(self):
            ok, message = self._network_collect()
            if not ok:
                self.msg_warning("网络设置无效", message, "请输入有效的 ws:// 或 wss:// 中继地址。")
                return
            ok, message = _persist_network_only()
            if not ok:
                self.msg_error("保存失败", "网络设置没有写入配置。", message)
                return
            request_reconnect("用户应用网络设置")
            self.feedback(f"已应用：{MODE_LABELS.get(_mode(), _mode())}", 3500)
            self._refresh_network_panel(force=True)

        def network_restore_official(self):
            name, url = official_name_url()
            B.network_settings["connection_mode"] = MODE_OFFICIAL
            B.network_settings["custom_relay_url"] = ""
            self.net_mode.setCurrentIndex(max(0, self.net_mode.findData(MODE_OFFICIAL)))
            self.net_custom.clear()
            _persist_network_only()
            request_reconnect("恢复官方中继")
            self.feedback(f"已恢复{name}：{url}", 4500)
            self._refresh_network_panel(force=True)

        def network_redetect(self):
            detect_network_async(force=True)
            self.feedback("正在重新检测 IPv4 / IPv6 / 公网出口…", 3000)
            self._refresh_network_panel(force=True)

        def network_test_relay(self):
            # Test what is currently selected in controls without changing persisted settings.
            mode = self.net_mode.currentData() or MODE_LAN
            custom = self.net_custom.text().strip()
            if mode == MODE_OFFICIAL:
                _, test_url = official_name_url()
            elif mode == MODE_CUSTOM:
                ok, test_url = normalize_relay_url(custom)
                if not ok:
                    self.msg_warning("测试失败", test_url, "")
                    return
            else:
                test_url = f"ws://127.0.0.1:{int(B.DG_PORT)}"

            self.feedback(f"正在测试：{test_url}", 2500)

            def worker():
                started = time.perf_counter()
                error = ""
                try:
                    ws = websocket.create_connection(test_url, timeout=5)
                    try:
                        ws.recv()
                    except Exception:
                        pass
                    ws.close()
                except Exception as exc:
                    error = str(exc)
                elapsed = (time.perf_counter() - started) * 1000.0
                detail = error if error else f"WebSocket 握手约 {elapsed:.0f} ms"
                self.relay_test_finished.emit(not bool(error), test_url, detail)

            threading.Thread(target=worker, name="CoyoteRelayTest", daemon=True).start()

        def _network_test_done(self, ok, url, detail):
            if ok:
                self.msg_info("中继测试成功", url, detail)
            else:
                self.msg_warning("中继测试失败", url, detail)

        def _refresh_network_panel(self, force=False):
            if not hasattr(self, "net_current"):
                return
            now = time.time()
            if not force and now - getattr(self, "_network_last_ui_refresh", 0.0) < 1.0:
                return
            self._network_last_ui_refresh = now
            snap = network_snapshot()
            suffix = "（检测中）" if snap.get("running") else ""
            self.net_local4.setText((snap.get("local_ipv4") or "无") + suffix)
            self.net_local6.setText((snap.get("local_ipv6") or "无") + suffix)
            self.net_public4.setText((snap.get("public_ipv4") or "无") + suffix)
            self.net_public6.setText((snap.get("public_ipv6") or "无") + suffix)
            self.net_public_state.setText(public_direct_summary())
            self.net_current.setText(active_controller_url())
            name, url = official_name_url()
            self.net_official.setText(f"{name} · {url}")

        def refresh_ui(self):
            super().refresh_ui()
            self._refresh_network_panel(force=False)

        def build_settings_export_payload(self):
            payload = super().build_settings_export_payload()
            network = payload.setdefault("network", {})
            network.update({
                "connection_mode": _mode(),
                "custom_relay_url": _s(B.network_settings.get("custom_relay_url", "")),
                "manual_host": _s(B.network_settings.get("manual_host", "")),
                "direct_host_source": _s(B.network_settings.get("direct_host_source", "auto")),
            })
            # official URL intentionally isn't exported; it is release identity.
            return payload

    UI.Window = NetworkWindow
