"""Coyote network / relay extension - V2.6.

The UI and networking model intentionally expose only three top-level modes:

1. Direct (default)
   - Coyote controller -> local Bun relay (127.0.0.1)
   - phone -> a phone-reachable address of this PC
   - ordinary LAN, Tailscale/ZeroTier/WireGuard/VPN, public IPv4/IPv6 and a
     manually routable hostname are all just different *address sources* of the
     same direct topology.
2. Official WSS relay
   - fixed release endpoint from official_relay.json / relay_config.py.
3. Custom relay
   - user supplied ws:// or wss:// endpoint.

There is no automatic fallback from Direct to the official relay. Public relay
traffic is entered only after the user explicitly selects and applies a relay
mode.

V2.4 provides the inline live-latency panel. V2.6 adds encrypted relay observability, stable client identity, client-log upload, and server kick/block reason handling. Latency checks use TCP/TLS
connection setup in a worker thread instead of creating temporary DG-LAB
controller WebSocket sessions, so periodic monitoring does not pollute the
relay's controller count.
"""
from __future__ import annotations

import ipaddress
import json
import os
import platform
import socket
import ssl
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import websocket

import backend as B
from relay_config import official_relay

_BACKEND_INSTALLED = False
_UI_INSTALLED = False
_ORIGINAL_LOAD_CONFIG = None
_ORIGINAL_SAVE_CONFIG = None
_ORIGINAL_ON_MESSAGE = None
_ORIGINAL_ON_OPEN = None
_ORIGINAL_ON_CLOSE = None
_ORIGINAL_ON_ERROR = None
_ORIGINAL_ADD_LOG = None
_OBSERVABILITY_STARTED = False
_LOG_QUEUE = deque(maxlen=2000)
_CONTROL_LOCK = threading.RLock()
_SERVER_CONTROL = {"action":"", "reason":"", "retry_at":0.0, "updated_at":0.0}

MODE_DIRECT = "direct"
MODE_OFFICIAL = "official_relay"
MODE_CUSTOM = "custom_relay"

LEGACY_MODE_MAP = {
    "lan": MODE_DIRECT,
    "manual_direct": MODE_DIRECT,
    "virtual_lan": MODE_DIRECT,
    "public_direct": MODE_DIRECT,
}

MODE_LABELS = {
    MODE_DIRECT: "直连",
    MODE_OFFICIAL: "官方 WSS 中继",
    MODE_CUSTOM: "自定义中继",
}
LOCAL_SERVER_MODES = {MODE_DIRECT}

HOST_AUTO = "auto"
HOST_IPV4 = "ipv4"
HOST_IPV6 = "ipv6"
HOST_MANUAL = "manual"
HOST_SOURCE_LABELS = {
    HOST_AUTO: "自动选择（IPv4 优先）",
    HOST_IPV4: "本机 IPv4",
    HOST_IPV6: "本机 IPv6",
    HOST_MANUAL: "手动 IP / IPv6 / 域名",
}

NETWORK_DEFAULTS = {
    "connection_mode": MODE_DIRECT,
    "custom_relay_url": "",
    "manual_host": "",
    "direct_port": 9998,
    "direct_host_source": HOST_AUTO,
    "client_instance_id": "",
    "client_label": "",
    "server_observability_enabled": True,
    "telemetry_interval": 1.0,
}

_DETECT_LOCK = threading.RLock()
_DETECT = {
    "local_ipv4": "",
    "local_ipv6": "",
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
    if parts.scheme.lower() != "wss":
        return False, "远程中继必须使用 wss:// TLS 加密，禁止明文 ws://"
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


def official_name_url() -> tuple[str, str]:
    name, raw = official_relay()
    ok, url_or_error = normalize_relay_url(raw)
    if ok:
        return name, url_or_error
    return name, _s(raw)


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
    # host:port, but do not split an unbracketed IPv6 literal.
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
    raw = _s(B.network_settings.get("connection_mode", MODE_DIRECT))
    mode = LEGACY_MODE_MAP.get(raw, raw)
    return mode if mode in MODE_LABELS else MODE_DIRECT


def _host_source() -> str:
    source = _s(B.network_settings.get("direct_host_source", HOST_AUTO))
    return source if source in HOST_SOURCE_LABELS else HOST_AUTO


def _direct_port() -> int:
    try:
        value = int(B.network_settings.get("direct_port", B.DG_PORT))
    except Exception:
        value = int(B.DG_PORT)
    return value if 1 <= value <= 65535 else int(B.DG_PORT)


def network_snapshot() -> dict:
    with _DETECT_LOCK:
        return dict(_DETECT)


def _best_direct_host(source: str | None = None) -> str:
    source = source or _host_source()
    snap = network_snapshot()
    local4 = _s(snap.get("local_ipv4"))
    local6 = _s(snap.get("local_ipv6"))
    manual = _clean_host_input(B.network_settings.get("manual_host", ""))

    if source == HOST_IPV4:
        return local4
    if source == HOST_IPV6:
        return local6
    if source == HOST_MANUAL:
        return manual
    return local4 or local6 or _s(getattr(B, "LAN_IP", "")) or "127.0.0.1"


def active_controller_url() -> str:
    """Address used by the Coyote controller role itself."""
    mode = _mode()
    if mode == MODE_OFFICIAL:
        _, url = official_name_url()
        return url
    if mode == MODE_CUSTOM:
        raw = _s(B.network_settings.get("custom_relay_url", ""))
        ok, value = normalize_relay_url(raw)
        # Do not silently fall back to the official endpoint.
        return value if ok else (raw or "ws://127.0.0.1:0")
    return f"ws://127.0.0.1:{int(B.DG_PORT)}"


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

    host = _host_for_url(_best_direct_host())
    if not host:
        return None
    return f"ws://{host}:{_direct_port()}/?tid={quote(str(controller_id), safe='')}"


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
        with _DETECT_LOCK:
            _DETECT.update({
                "local_ipv4": local4,
                "local_ipv6": local6,
                "updated_at": time.time(),
                "running": False,
                "error": "" if (local4 or local6) else "未检测到可用直连地址",
            })

    threading.Thread(target=worker, name="CoyoteNetworkDetect", daemon=True).start()


def relay_endpoint_parts(url: str) -> tuple[str, str, int]:
    ok, normalized = normalize_relay_url(url)
    if not ok:
        raise ValueError(normalized)
    parts = urlsplit(normalized)
    scheme = parts.scheme.lower()
    host = parts.hostname or ""
    port = parts.port or (443 if scheme == "wss" else 80)
    return scheme, host, int(port)


def measure_socket_latency(url: str, timeout: float = 3.5) -> tuple[bool, float, str]:
    """Measure DNS + TCP (+ TLS for WSS) setup without opening WebSocket session."""
    try:
        scheme, host, port = relay_endpoint_parts(url)
    except Exception as exc:
        return False, 0.0, str(exc)

    started = time.perf_counter()
    raw_sock = None
    tls_sock = None
    try:
        raw_sock = socket.create_connection((host, port), timeout=timeout)
        raw_sock.settimeout(timeout)
        if scheme == "wss":
            context = ssl.create_default_context()
            tls_sock = context.wrap_socket(raw_sock, server_hostname=host)
            raw_sock = None
        elapsed = (time.perf_counter() - started) * 1000.0
        return True, elapsed, f"{scheme.upper()} {host}:{port}"
    except Exception as exc:
        elapsed = (time.perf_counter() - started) * 1000.0
        return False, elapsed, f"{type(exc).__name__}: {exc}"
    finally:
        try:
            if tls_sock is not None:
                tls_sock.close()
        except Exception:
            pass
        try:
            if raw_sock is not None:
                raw_sock.close()
        except Exception:
            pass


def measure_direct_latency(timeout: float = 2.0) -> tuple[bool, float, str]:
    started = time.perf_counter()
    sock = None
    try:
        sock = socket.create_connection(("127.0.0.1", int(B.DG_PORT)), timeout=timeout)
        elapsed = (time.perf_counter() - started) * 1000.0
        return True, elapsed, f"本机 Relay 127.0.0.1:{int(B.DG_PORT)}"
    except Exception as exc:
        elapsed = (time.perf_counter() - started) * 1000.0
        return False, elapsed, f"{type(exc).__name__}: {exc}"
    finally:
        try:
            if sock is not None:
                sock.close()
        except Exception:
            pass



def _client_instance_id() -> str:
    value = _s(B.network_settings.get("client_instance_id", ""))
    if not value:
        value = uuid.uuid4().hex
        B.network_settings["client_instance_id"] = value
    return value


def _client_label() -> str:
    label = _s(B.network_settings.get("client_label", ""))
    return label or f"Coyote-{_client_instance_id()[:8]}"


def _telemetry_interval() -> float:
    try:
        return max(0.5, min(10.0, float(B.network_settings.get("telemetry_interval", 1.0))))
    except Exception:
        return 1.0


def server_control_snapshot() -> dict:
    with _CONTROL_LOCK:
        return dict(_SERVER_CONTROL)


def _send_server_frame(payload: dict) -> bool:
    if _mode() not in {MODE_OFFICIAL, MODE_CUSTOM}:
        return False
    with B.dg_lock:
        ws = B.dg_ws
    if ws is None:
        return False
    try:
        packet = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with B.ws_send_lock:
            ws.send(packet)
        return True
    except Exception:
        return False


def _send_profile_frame() -> None:
    if _mode() not in {MODE_OFFICIAL, MODE_CUSTOM}:
        return
    _send_server_frame({
        "type": "coyote.profile",
        "instanceId": _client_instance_id(),
        "label": _client_label(),
        "appVersion": _s(getattr(B, "APP_VERSION", getattr(B, "VERSION", ""))),
        "platform": platform.platform()[:120],
    })


def _safe_snapshot() -> tuple[dict, dict, dict]:
    telemetry = {}
    try:
        with B.peak_lock:
            telemetry = json.loads(json.dumps(B.latest_peak, ensure_ascii=False)) if isinstance(B.latest_peak, dict) else {}
    except Exception:
        telemetry = {}
    try:
        with B.dg_lock:
            dg = json.loads(json.dumps(B.dg, ensure_ascii=False)) if isinstance(B.dg, dict) else {}
    except Exception:
        dg = {}
    multiplayer = {}
    try:
        import multiplayer_features as MP
        if hasattr(MP, "get_multiplayer_snapshot"):
            multiplayer["players"] = MP.get_multiplayer_snapshot()
        if hasattr(MP, "get_multidevice_snapshot"):
            multiplayer["devices"] = MP.get_multidevice_snapshot()
    except Exception:
        pass
    return telemetry, dg, multiplayer


def _flush_client_logs() -> None:
    if not bool(B.network_settings.get("server_observability_enabled", True)):
        return
    batch = []
    while _LOG_QUEUE and len(batch) < 100:
        try:
            batch.append(_LOG_QUEUE.popleft())
        except IndexError:
            break
    if batch:
        if not _send_server_frame({"type":"coyote.logs", "instanceId":_client_instance_id(), "entries":batch}):
            for item in reversed(batch):
                if len(_LOG_QUEUE) < _LOG_QUEUE.maxlen:
                    _LOG_QUEUE.appendleft(item)


def _observability_loop() -> None:
    next_status = 0.0
    while not B.stop_event.is_set():
        if _mode() in {MODE_OFFICIAL, MODE_CUSTOM} and bool(B.network_settings.get("server_observability_enabled", True)):
            now = time.time()
            if now >= next_status:
                telemetry, dg, multiplayer = _safe_snapshot()
                _send_server_frame({
                    "type":"coyote.status",
                    "instanceId":_client_instance_id(),
                    "label":_client_label(),
                    "appVersion":_s(getattr(B, "APP_VERSION", getattr(B, "VERSION", ""))),
                    "platform":platform.platform()[:120],
                    "telemetry":telemetry,
                    "dg":dg,
                    "multiplayer":multiplayer,
                    "network":{"mode":_mode(), "relay":active_controller_url()},
                })
                next_status = now + _telemetry_interval()
            _flush_client_logs()
        time.sleep(0.25)


def _handle_server_control(data: dict) -> None:
    action = _s(data.get("action", ""))
    reason = _s(data.get("reason", "")) or ("服务器已封禁此客户端" if action == "blocked" else "管理员已踢下线")
    try:
        retry_after = max(5, min(86400, int(data.get("retryAfter", 60))))
    except Exception:
        retry_after = 60
    if action not in {"blocked", "kicked", "notice"}:
        return
    with _CONTROL_LOCK:
        _SERVER_CONTROL.update({"action":action, "reason":reason, "retry_at":time.time()+retry_after if action in {"blocked","kicked"} else 0.0, "updated_at":time.time()})
    with B.dg_lock:
        B.dg["server"] = "已被服务器封禁" if action == "blocked" else ("管理员已踢下线" if action == "kicked" else "服务器通知")
        B.dg["error"] = reason
    try:
        if B.dg_ws is not None and action in {"blocked","kicked"}:
            B.dg_ws.close()
    except Exception:
        pass


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
        "direct_port": _direct_port(),
        "direct_host_source": _host_source(),
        "client_instance_id": _client_instance_id(),
        "client_label": _client_label(),
        "server_observability_enabled": bool(B.network_settings.get("server_observability_enabled", True)),
        "telemetry_interval": _telemetry_interval(),
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

    raw_mode = _s(network.get("connection_mode", MODE_DIRECT))
    mode = LEGACY_MODE_MAP.get(raw_mode, raw_mode)
    B.network_settings["connection_mode"] = mode if mode in MODE_LABELS else MODE_DIRECT
    B.network_settings["custom_relay_url"] = _s(network.get("custom_relay_url", ""))
    B.network_settings["manual_host"] = _s(network.get("manual_host", ""))

    try:
        direct_port = int(network.get("direct_port", network.get("dg_port", B.DEFAULT_DG_PORT)))
    except Exception:
        direct_port = int(B.DEFAULT_DG_PORT)
    B.network_settings["direct_port"] = (
        direct_port if 1 <= direct_port <= 65535 else int(B.DEFAULT_DG_PORT)
    )

    source = _s(network.get("direct_host_source", ""))
    if source not in HOST_SOURCE_LABELS:
        # Legacy manual-direct modes should preserve their explicit host. Legacy
        # LAN remains automatic.
        source = HOST_MANUAL if raw_mode in {"manual_direct", "virtual_lan", "public_direct"} else HOST_AUTO
    B.network_settings["direct_host_source"] = source
    instance_id = _s(network.get("client_instance_id", ""))
    if not instance_id:
        instance_id = uuid.uuid4().hex
    B.network_settings["client_instance_id"] = instance_id
    B.network_settings["client_label"] = _s(network.get("client_label", ""))
    B.network_settings["server_observability_enabled"] = bool(network.get("server_observability_enabled", True))
    try:
        interval = float(network.get("telemetry_interval", 1.0))
    except Exception:
        interval = 1.0
    B.network_settings["telemetry_interval"] = max(0.5, min(10.0, interval))


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
    while not B.stop_event.is_set():
        with _CONTROL_LOCK:
            retry_at = float(_SERVER_CONTROL.get("retry_at", 0.0) or 0.0)
            reason = _s(_SERVER_CONTROL.get("reason", ""))
            action = _s(_SERVER_CONTROL.get("action", ""))
        now = time.time()
        if retry_at > now:
            remaining = max(1, int(retry_at - now))
            with B.dg_lock:
                B.dg["server"] = ("已被服务器封禁" if action == "blocked" else "管理员已踢下线") + f" · {remaining}s 后重试"
                B.dg["error"] = reason
            time.sleep(min(1.0, retry_at - now))
            continue
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
                    B.dg["server"] = f"正在连接{MODE_LABELS.get(mode, mode)}"

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
    global _BACKEND_INSTALLED, _ORIGINAL_LOAD_CONFIG, _ORIGINAL_SAVE_CONFIG, _ORIGINAL_ON_MESSAGE, _ORIGINAL_ON_OPEN, _ORIGINAL_ON_CLOSE, _ORIGINAL_ON_ERROR, _ORIGINAL_ADD_LOG, _OBSERVABILITY_STARTED
    if _BACKEND_INSTALLED:
        return
    _BACKEND_INSTALLED = True

    for key, value in NETWORK_DEFAULTS.items():
        B.network_settings.setdefault(key, value)

    _ORIGINAL_LOAD_CONFIG = B.load_config
    _ORIGINAL_SAVE_CONFIG = getattr(B, "save_config", None)
    _ORIGINAL_ON_MESSAGE = B.on_message
    _ORIGINAL_ON_OPEN = B.on_open
    _ORIGINAL_ON_CLOSE = B.on_close
    _ORIGINAL_ON_ERROR = B.on_error
    _ORIGINAL_ADD_LOG = B.add_log

    def add_log_wrapper(category, event, detail="", output=None):
        result = _ORIGINAL_ADD_LOG(category, event, detail, output)
        try:
            _LOG_QUEUE.append({"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "category": str(category)[:80], "event": str(event)[:160], "detail": str(detail)[:1000]})
        except Exception:
            pass
        return result
    B.add_log = add_log_wrapper

    def on_open_wrapper(ws):
        _ORIGINAL_ON_OPEN(ws)
        _send_profile_frame()

    def on_message_wrapper(ws, message):
        try:
            data = json.loads(message)
        except Exception:
            data = None
        if isinstance(data, dict) and data.get("type") == "coyote.control":
            _handle_server_control(data)
            return
        _ORIGINAL_ON_MESSAGE(ws, message)

    def on_close_wrapper(ws, code, reason):
        _ORIGINAL_ON_CLOSE(ws, code, reason)
        snap = server_control_snapshot()
        if snap.get("reason"):
            with B.dg_lock:
                B.dg["server"] = "已被服务器封禁" if snap.get("action") == "blocked" else "管理员已踢下线"
                B.dg["error"] = snap.get("reason")

    def on_error_wrapper(ws, error):
        _ORIGINAL_ON_ERROR(ws, error)
        snap = server_control_snapshot()
        if snap.get("reason"):
            with B.dg_lock:
                B.dg["error"] = snap.get("reason")

    B.on_open = on_open_wrapper
    B.on_message = on_message_wrapper
    B.on_close = on_close_wrapper
    B.on_error = on_error_wrapper

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

    _load_network_extension()
    if not _OBSERVABILITY_STARTED:
        _OBSERVABILITY_STARTED = True
        threading.Thread(target=_observability_loop, name="CoyoteRelayObservability", daemon=True).start()
    detect_network_async(force=True)


def install_ui(UI) -> None:
    global _UI_INSTALLED
    if _UI_INSTALLED:
        return
    _UI_INSTALLED = True

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
        latency_finished = UI.Signal(str, bool, float, str)

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._latency_running = False
            self._latency_mode = None
            self._latency_manual = False
            self._network_last_ui_refresh = 0.0
            self.latency_finished.connect(self._latency_done)

            self._latency_timer = UI.QTimer(self)
            self._latency_timer.setInterval(5000)
            self._latency_timer.timeout.connect(self._latency_tick)
            self._latency_timer.start()

        @staticmethod
        def _readonly_value(text="-"):
            label = UI.QLabel(str(text or "-"))
            label.setTextInteractionFlags(UI.Qt.TextInteractionFlag.TextSelectableByMouse)
            label.setWordWrap(True)
            label.setMinimumHeight(26)
            return label

        @staticmethod
        def _note(text):
            label = UI.QLabel(text)
            label.setObjectName("muted")
            label.setWordWrap(True)
            return label

        def build_pair(self):
            outer = UI.QVBoxLayout(self.pair)
            outer.setContentsMargins(4, 4, 4, 4)
            outer.setSpacing(0)

            scroll = UI.QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(UI.QFrame.Shape.NoFrame)

            body = UI.QWidget()
            body.setMinimumWidth(820)
            layout = UI.QVBoxLayout(body)
            layout.setContentsMargins(4, 4, 4, 4)
            layout.setSpacing(12)

            # Existing pairing / detail area.
            top = UI.QHBoxLayout()
            top.setSpacing(12)

            pair_panel, pair_l = self.panel("DG-LAB 配对")
            self.qr = UI.QLabel("等待控制方 ID...")
            self.qr.setAlignment(UI.Qt.AlignmentFlag.AlignCenter)
            self.qr.setMinimumSize(300, 300)
            self.qr.setSizePolicy(UI.QSizePolicy.Policy.Expanding, UI.QSizePolicy.Policy.Expanding)
            pair_l.addWidget(self.qr, 1)
            self.url = UI.QLineEdit()
            self.url.setReadOnly(True)
            self.url.setMinimumHeight(34)
            pair_l.addWidget(self.url)
            copy_btn = UI.QPushButton("复制配对地址")
            copy_btn.setMinimumHeight(34)
            copy_btn.clicked.connect(self.copy_pair_url)
            pair_l.addWidget(copy_btn, alignment=UI.Qt.AlignmentFlag.AlignRight)

            detail_panel, detail_l = self.panel("连接详细信息")
            detail_form = UI.QFormLayout()
            detail_form.setHorizontalSpacing(16)
            detail_form.setVerticalSpacing(9)
            detail_form.setFieldGrowthPolicy(UI.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
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
                value.setMinimumWidth(220)
                self.detail[key] = value
                detail_form.addRow(name, value)
            detail_l.addLayout(detail_form)
            detail_l.addStretch(1)
            top.addWidget(pair_panel, 1)
            top.addWidget(detail_panel, 1)
            layout.addLayout(top)

            # Network area: tabs on the left, selected-tab information on the right.
            net_panel, net_l = self.panel("网络连接")
            net_l.setSpacing(10)

            columns = UI.QHBoxLayout()
            columns.setSpacing(12)

            self.net_tabs = UI.QTabWidget()
            self.net_tabs.setMinimumWidth(470)
            self.net_tabs.setDocumentMode(True)
            self._mode_by_tab = []

            # Direct tab.
            direct_tab = UI.QWidget()
            direct_l = UI.QVBoxLayout(direct_tab)
            direct_l.setContentsMargins(12, 12, 12, 12)
            direct_l.setSpacing(10)
            direct_form = UI.QFormLayout()
            direct_form.setHorizontalSpacing(14)
            direct_form.setVerticalSpacing(9)
            direct_form.setFieldGrowthPolicy(UI.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

            self.net_host_source = UI.QComboBox()
            self.net_host_source.setMinimumHeight(36)
            for source, label in HOST_SOURCE_LABELS.items():
                self.net_host_source.addItem(label, source)
            src_idx = self.net_host_source.findData(_host_source())
            self.net_host_source.setCurrentIndex(max(0, src_idx))

            self.net_manual_host = UI.QLineEdit(_s(B.network_settings.get("manual_host", "")))
            self.net_manual_host.setPlaceholderText("例如 100.x.x.x / IPv6 / 域名")
            self.net_manual_host.setMinimumHeight(36)

            self.net_direct_port = UI.QSpinBox()
            self.net_direct_port.setRange(1, 65535)
            self.net_direct_port.setValue(_direct_port())
            self.net_direct_port.setMinimumHeight(36)
            self.net_direct_port.setMaximumWidth(160)

            direct_form.addRow("手机地址来源", self.net_host_source)
            self._manual_host_label = UI.QLabel("手动地址")
            direct_form.addRow(self._manual_host_label, self.net_manual_host)
            direct_form.addRow("手机访问端口", self.net_direct_port)
            direct_l.addLayout(direct_form)
            direct_l.addWidget(self._note(
                "直连是默认模式。普通 Wi-Fi/LAN、Tailscale、ZeroTier、WireGuard、VPN、"
                "公网 IPv4/IPv6 都属于同一拓扑，只是手机访问本机 Relay 时使用的地址不同。"
            ))
            direct_l.addStretch(1)
            self.net_tabs.addTab(direct_tab, "直连")
            self._mode_by_tab.append(MODE_DIRECT)

            # Official relay tab.
            official_tab = UI.QWidget()
            official_l = UI.QVBoxLayout(official_tab)
            official_l.setContentsMargins(12, 12, 12, 12)
            official_l.setSpacing(10)
            official_name, official_url = official_name_url()
            official_form = UI.QFormLayout()
            official_form.setHorizontalSpacing(14)
            official_form.setVerticalSpacing(9)
            self.net_official_name = self._readonly_value(official_name)
            self.net_official_url = self._readonly_value(official_url)
            official_form.addRow("名称", self.net_official_name)
            official_form.addRow("WSS 地址", self.net_official_url)
            official_l.addLayout(official_form)
            official_l.addWidget(self._note(
                "只有你主动选择并点击“应用并重连”后才走北京官方中继。"
                "程序不会因为直连失败而自动切到公网。"
            ))
            official_l.addStretch(1)
            self.net_tabs.addTab(official_tab, "官方中继")
            self._mode_by_tab.append(MODE_OFFICIAL)

            # Custom relay tab.
            custom_tab = UI.QWidget()
            custom_l = UI.QVBoxLayout(custom_tab)
            custom_l.setContentsMargins(12, 12, 12, 12)
            custom_l.setSpacing(10)
            custom_form = UI.QFormLayout()
            custom_form.setHorizontalSpacing(14)
            custom_form.setVerticalSpacing(9)
            custom_form.setFieldGrowthPolicy(UI.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            self.net_custom = UI.QLineEdit(_s(B.network_settings.get("custom_relay_url", "")))
            self.net_custom.setPlaceholderText("wss://relay.example.com")
            self.net_custom.setMinimumHeight(36)
            custom_form.addRow("Relay 地址", self.net_custom)
            custom_l.addLayout(custom_form)
            custom_l.addWidget(self._note(
                "填写用户自己搭建的 wss:// Relay。远程中继强制 TLS 加密，不允许明文 ws://。保存自定义地址不会改变官方服务器地址。"
            ))
            custom_l.addStretch(1)
            self.net_tabs.addTab(custom_tab, "自定义中继")
            self._mode_by_tab.append(MODE_CUSTOM)

            # Right side follows the selected tab in real time.
            info_box = UI.QGroupBox("当前选项网络信息")
            info_l = UI.QFormLayout(info_box)
            info_l.setHorizontalSpacing(14)
            info_l.setVerticalSpacing(9)
            info_l.setFieldGrowthPolicy(UI.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            self.net_info = {}
            for key, title in (
                ("selected_mode", "所选通道"),
                ("applied_mode", "已应用通道"),
                ("protocol", "协议"),
                ("controller", "控制端目标"),
                ("phone", "手机目标"),
                ("ipv4", "本机 IPv4"),
                ("ipv6", "本机 IPv6"),
                ("state", "连接状态"),
                ("server_notice", "服务器通知"),
            ):
                value = self._readonly_value("-")
                self.net_info[key] = value
                info_l.addRow(title, value)

            columns.addWidget(self.net_tabs, 3)
            columns.addWidget(info_box, 2)
            net_l.addLayout(columns)

            self.net_observability = UI.QCheckBox("向远程中继上报 PEAK 游戏状态、全部郊狼设备信息和客户端日志（仅通过 WSS/TLS 加密）")
            self.net_observability.setChecked(bool(B.network_settings.get("server_observability_enabled", True)))
            self.net_observability.toggled.connect(lambda checked: B.network_settings.__setitem__("server_observability_enabled", bool(checked)))
            net_l.addWidget(self.net_observability)

            # Inline real-time latency. No QMessageBox / toast is used for tests.
            latency_box = UI.QGroupBox("实时延迟")
            latency_l = UI.QHBoxLayout(latency_box)
            latency_l.setContentsMargins(12, 10, 12, 10)
            latency_l.setSpacing(12)
            self.net_latency_value = UI.QLabel("等待检测")
            self.net_latency_value.setStyleSheet("font-size:18px;font-weight:700")
            self.net_latency_value.setMinimumWidth(150)
            self.net_latency_detail = UI.QLabel("每 5 秒自动刷新")
            self.net_latency_detail.setObjectName("muted")
            self.net_latency_detail.setWordWrap(True)
            self.net_latency_test = UI.QPushButton("立即测试")
            self.net_latency_test.setMinimumHeight(34)
            self.net_latency_test.clicked.connect(self.network_test_latency)
            latency_l.addWidget(self.net_latency_value)
            latency_l.addWidget(self.net_latency_detail, 1)
            latency_l.addWidget(self.net_latency_test)
            net_l.addWidget(latency_box)

            actions = UI.QHBoxLayout()
            actions.setSpacing(10)
            detect_btn = UI.QPushButton("重新检测本机网络")
            detect_btn.setMinimumHeight(36)
            detect_btn.clicked.connect(self.network_redetect)
            restore_btn = UI.QPushButton("切换 / 恢复官方中继")
            restore_btn.setMinimumHeight(36)
            restore_btn.clicked.connect(self.network_restore_official)
            apply_btn = UI.QPushButton("应用并重连")
            apply_btn.setObjectName("primary")
            apply_btn.setMinimumHeight(36)
            apply_btn.clicked.connect(self.network_apply)
            actions.addWidget(detect_btn)
            actions.addStretch(1)
            actions.addWidget(restore_btn)
            actions.addWidget(apply_btn)
            net_l.addLayout(actions)

            layout.addWidget(net_panel)
            layout.addStretch(1)
            scroll.setWidget(body)
            outer.addWidget(scroll)

            # Restore selected tab from persisted mode.
            applied = _mode()
            try:
                tab_index = self._mode_by_tab.index(applied)
            except ValueError:
                tab_index = 0
            self.net_tabs.setCurrentIndex(tab_index)

            self.net_tabs.currentChanged.connect(self._network_tab_changed)
            self.net_host_source.currentIndexChanged.connect(self._direct_input_changed)
            self.net_manual_host.textChanged.connect(self._direct_input_changed)
            self.net_direct_port.valueChanged.connect(self._direct_input_changed)
            self.net_custom.textChanged.connect(self._custom_input_changed)

            self._direct_input_changed()
            self._refresh_network_panel(force=True)
            detect_network_async(force=False)
            # Switching/building the page should show a result quickly.
            UI.QTimer.singleShot(250, lambda: self._start_latency_test(manual=False, show_checking=True))

        def _selected_mode(self) -> str:
            if not hasattr(self, "net_tabs"):
                return _mode()
            index = self.net_tabs.currentIndex()
            if 0 <= index < len(self._mode_by_tab):
                return self._mode_by_tab[index]
            return MODE_DIRECT

        def _set_tab_for_mode(self, mode: str):
            try:
                index = self._mode_by_tab.index(mode)
            except ValueError:
                index = 0
            self.net_tabs.setCurrentIndex(index)

        def _direct_selected_host(self) -> str:
            source = self.net_host_source.currentData() or HOST_AUTO
            snap = network_snapshot()
            local4 = _s(snap.get("local_ipv4"))
            local6 = _s(snap.get("local_ipv6"))
            manual = _clean_host_input(self.net_manual_host.text())
            if source == HOST_IPV4:
                return local4
            if source == HOST_IPV6:
                return local6
            if source == HOST_MANUAL:
                return manual
            return local4 or local6 or _s(getattr(B, "LAN_IP", "")) or "127.0.0.1"

        def _direct_preview_phone_url(self) -> str:
            host = _host_for_url(self._direct_selected_host())
            if not host:
                return "未检测到可用地址"
            return f"ws://{host}:{int(self.net_direct_port.value())}/?tid=<控制方ID>"

        def _selected_relay_url(self) -> tuple[bool, str]:
            mode = self._selected_mode()
            if mode == MODE_OFFICIAL:
                return True, official_name_url()[1]
            if mode == MODE_CUSTOM:
                return normalize_relay_url(self.net_custom.text().strip())
            return True, f"ws://127.0.0.1:{int(B.DG_PORT)}"

        def _network_tab_changed(self, *args):
            self._refresh_network_panel(force=True)
            self._start_latency_test(manual=False, show_checking=True)

        def _direct_input_changed(self, *args):
            if not hasattr(self, "net_host_source"):
                return
            manual = (self.net_host_source.currentData() or HOST_AUTO) == HOST_MANUAL
            self.net_manual_host.setVisible(manual)
            self._manual_host_label.setVisible(manual)
            self._refresh_network_panel(force=True)

        def _custom_input_changed(self, *args):
            self._refresh_network_panel(force=True)
            if self._selected_mode() == MODE_CUSTOM:
                # Do not fire on every keystroke; the timer/manual button will test.
                self.net_latency_value.setText("等待检测")
                self.net_latency_detail.setText("地址已修改，点击“立即测试”或等待自动刷新")

        def _network_collect(self):
            mode = self._selected_mode()
            custom = self.net_custom.text().strip()
            manual = self.net_manual_host.text().strip()
            source = self.net_host_source.currentData() or HOST_AUTO

            if mode == MODE_CUSTOM:
                ok, value = normalize_relay_url(custom)
                if not ok:
                    return False, value
                custom = value

            if mode == MODE_DIRECT:
                if source == HOST_MANUAL:
                    host = _clean_host_input(manual)
                    if not host:
                        return False, "手动地址模式必须填写手机能够访问到的本机 IP、IPv6 或域名。"
                    manual = host
                elif source == HOST_IPV4 and not network_snapshot().get("local_ipv4"):
                    return False, "当前没有检测到可用的本机 IPv4。"
                elif source == HOST_IPV6 and not network_snapshot().get("local_ipv6"):
                    return False, "当前没有检测到可用的本机 IPv6。"

            B.network_settings["connection_mode"] = mode
            B.network_settings["custom_relay_url"] = custom
            B.network_settings["manual_host"] = manual
            B.network_settings["direct_port"] = int(self.net_direct_port.value())
            B.network_settings["direct_host_source"] = source
            return True, ""

        def network_apply(self):
            ok, message = self._network_collect()
            if not ok:
                self.msg_warning("网络设置无效", message, "请检查当前选项卡中的地址设置。")
                return
            ok, message = _persist_network_only()
            if not ok:
                self.msg_error("保存失败", "网络设置没有写入配置。", message)
                return
            request_reconnect("用户应用网络设置")
            self.feedback(f"已应用：{MODE_LABELS.get(_mode(), _mode())}", 2600)
            self._refresh_network_panel(force=True)
            self._start_latency_test(manual=False, show_checking=True)

        def network_restore_official(self):
            name, url = official_name_url()
            B.network_settings["connection_mode"] = MODE_OFFICIAL
            self._set_tab_for_mode(MODE_OFFICIAL)
            _persist_network_only()
            request_reconnect("用户切换到官方中继")
            self.feedback(f"已切换{name}：{url}", 3000)
            self._refresh_network_panel(force=True)
            self._start_latency_test(manual=False, show_checking=True)

        def network_redetect(self):
            detect_network_async(force=True)
            self.net_info["ipv4"].setText("检测中…")
            self.net_info["ipv6"].setText("检测中…")
            # refresh_ui will pick up the worker result without a dialog.

        def _latency_tick(self):
            if not hasattr(self, "net_tabs"):
                return
            self._start_latency_test(manual=False, show_checking=False)

        def network_test_latency(self):
            self._start_latency_test(manual=True, show_checking=True)

        def _start_latency_test(self, manual=False, show_checking=False):
            if not hasattr(self, "net_latency_value"):
                return
            if self._latency_running:
                if manual:
                    self.net_latency_value.setText("检测中…")
                    self.net_latency_detail.setText("上一轮检测尚未完成")
                return

            mode = self._selected_mode()
            if mode == MODE_CUSTOM:
                ok, target = normalize_relay_url(self.net_custom.text().strip())
                if not ok:
                    self.net_latency_value.setText("地址无效")
                    self.net_latency_detail.setText(target)
                    return
            elif mode == MODE_OFFICIAL:
                target = official_name_url()[1]
            else:
                target = ""

            self._latency_running = True
            self._latency_mode = mode
            self._latency_manual = bool(manual)
            if show_checking or manual:
                self.net_latency_value.setText("检测中…")
                self.net_latency_detail.setText("正在测量当前选项通道")
            if manual:
                self.net_latency_test.setText("检测中…")
                self.net_latency_test.setEnabled(False)

            def worker():
                if mode == MODE_DIRECT:
                    ok, ms, detail = measure_direct_latency()
                else:
                    ok, ms, detail = measure_socket_latency(target)
                self.latency_finished.emit(mode, bool(ok), float(ms), str(detail))

            threading.Thread(target=worker, name="CoyoteLatencyProbe", daemon=True).start()

        def _latency_done(self, mode, ok, ms, detail):
            manual = self._latency_manual
            self._latency_running = False
            self._latency_mode = None
            self._latency_manual = False

            if manual:
                self.net_latency_test.setText("立即测试")
                self.net_latency_test.setEnabled(True)

            # Ignore a stale result from the tab the user has already left.
            if mode != self._selected_mode():
                return

            if ok:
                if ms < 1.0:
                    text = "< 1 ms"
                else:
                    text = f"{ms:.0f} ms"
                self.net_latency_value.setText(text)
                if mode == MODE_DIRECT:
                    self.net_latency_detail.setText(
                        "本机 Relay 响应 · 每 5 秒刷新；不代表手机到电脑的实际网络 RTT"
                    )
                else:
                    self.net_latency_detail.setText(f"{detail} · 每 5 秒自动刷新")
            else:
                self.net_latency_value.setText("不可达")
                self.net_latency_detail.setText(detail)

        def _refresh_network_panel(self, force=False):
            if not hasattr(self, "net_info"):
                return
            now = time.time()
            if not force and now - self._network_last_ui_refresh < 0.5:
                return
            self._network_last_ui_refresh = now

            snap = network_snapshot()
            local4 = _s(snap.get("local_ipv4")) or "无"
            local6 = _s(snap.get("local_ipv6")) or "无"
            selected = self._selected_mode()
            applied = _mode()

            self.net_info["selected_mode"].setText(MODE_LABELS.get(selected, selected))
            self.net_info["applied_mode"].setText(MODE_LABELS.get(applied, applied))
            self.net_info["ipv4"].setText("检测中…" if snap.get("running") else local4)
            self.net_info["ipv6"].setText("检测中…" if snap.get("running") else local6)

            if selected == MODE_DIRECT:
                self.net_info["protocol"].setText("WS · 本机 Relay 直连")
                self.net_info["controller"].setText(f"ws://127.0.0.1:{int(B.DG_PORT)}")
                self.net_info["phone"].setText(self._direct_preview_phone_url())
            elif selected == MODE_OFFICIAL:
                url = official_name_url()[1]
                self.net_info["protocol"].setText("WSS · TLS")
                self.net_info["controller"].setText(url)
                self.net_info["phone"].setText(url.rstrip("/") + "?tid=<控制方ID>")
            else:
                ok, url = normalize_relay_url(self.net_custom.text().strip())
                if ok:
                    scheme = urlsplit(url).scheme.upper()
                    self.net_info["protocol"].setText(f"{scheme}" + (" · TLS" if scheme == "WSS" else ""))
                    self.net_info["controller"].setText(url)
                    self.net_info["phone"].setText(url.rstrip("/") + "?tid=<控制方ID>")
                else:
                    self.net_info["protocol"].setText("-")
                    self.net_info["controller"].setText("地址无效")
                    self.net_info["phone"].setText("-")

            with B.dg_lock:
                server_state = _s(B.dg.get("server", "")) or "-"
                controller_id = _s(B.dg.get("controller_id", ""))
            if selected != applied:
                self.net_info["state"].setText(
                    f"未应用（当前实际：{MODE_LABELS.get(applied, applied)}）"
                )
            else:
                suffix = " · 已取得控制方 ID" if controller_id else ""
                self.net_info["state"].setText(server_state + suffix)

            notice = server_control_snapshot()
            if notice.get("reason"):
                retry = max(0, int(float(notice.get("retry_at",0) or 0) - time.time()))
                suffix = f" · {retry}s 后重试" if retry > 0 else ""
                self.net_info["server_notice"].setText(("封禁：" if notice.get("action") == "blocked" else "踢下线：") + str(notice.get("reason")) + suffix)
            else:
                self.net_info["server_notice"].setText("无")

            name, url = official_name_url()
            self.net_official_name.setText(name)
            self.net_official_url.setText(url)

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
                "direct_port": _direct_port(),
                "direct_host_source": _host_source(),
                "client_instance_id": _client_instance_id(),
                "client_label": _client_label(),
                "server_observability_enabled": bool(B.network_settings.get("server_observability_enabled", True)),
                "telemetry_interval": _telemetry_interval(),
            })
            return payload

    UI.Window = NetworkWindow
