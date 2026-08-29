"""Encrypted remote diagnostics/reporting extension for Coyote V2.6.1.

This module intentionally reuses the already-authenticated/selected Coyote
controller WebSocket.  It does NOT open a separate raw UDP telemetry channel.
Remote reporting is enabled only when the active controller URL is ``wss://``;
plain ``ws://`` relay traffic can still work, but logs/game/device diagnostics
are never uploaded over that plaintext transport.

User privacy always wins over server policy:
- ``disable_log_upload``: never upload Coyote event logs.
- ``disable_state_upload``: never upload PEAK/game state or DG-LAB device info.

The server can choose log policy (off / realtime / interval) and reporting
intervals, but it cannot override those two local privacy switches.
"""
from __future__ import annotations

import json
import platform
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

import backend as B

try:
    import multiplayer_features as MP
except Exception:
    MP = None

_BACKEND_INSTALLED = False
_UI_INSTALLED = False
_ORIGINAL_ON_MESSAGE = None
_ORIGINAL_ON_OPEN = None
_ORIGINAL_ON_ERROR = None
_ORIGINAL_ON_CLOSE = None
_ORIGINAL_LOAD_CONFIG = None
_ORIGINAL_SAVE_CONFIG = None

REPORTING_DEFAULTS = {
    "disable_log_upload": False,
    "disable_state_upload": False,
    "client_instance_id": "",
}

_POLICY_LOCK = threading.RLock()
_POLICY = {
    "logMode": "interval",          # off | realtime | interval
    "logIntervalSeconds": 15,
    "stateIntervalSeconds": 2,
    "blockRecheckSeconds": 60,
}

_STATE_LOCK = threading.RLock()
_STATE = {
    "last_notice": "",
    "last_notice_type": "",
    "last_report_at": 0.0,
    "last_log_at": 0.0,
    "connected": False,
    "encrypted": False,
    "blocked": False,
    "blocked_reason": "",
    # A ban is scoped to the relay origin that reported it.  A ban from the
    # official relay must never poison Direct mode or a different custom relay.
    "blocked_origin": "",
    "block_revision": 0,
    "blocked_checked_at": 0.0,
    "block_watch_running": False,
    "last_error": "",
}

_REPORT_THREAD = None
_LAST_LOG_REVISION = 0
_LAST_STATE_SENT = 0.0
_LAST_LOG_SENT = 0.0


def _s(value) -> str:
    return str(value or "").strip()


def _json_read(path: Path) -> dict:
    try:
        obj = json.loads(path.read_text(encoding="utf-8-sig"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _json_write(path: Path, data: dict) -> tuple[bool, str]:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True, "配置已保存"
    except Exception as exc:
        return False, str(exc)


def _ensure_defaults() -> None:
    for key, value in REPORTING_DEFAULTS.items():
        B.network_settings.setdefault(key, value)
    if not _s(B.network_settings.get("client_instance_id")):
        B.network_settings["client_instance_id"] = uuid.uuid4().hex


def _load_config() -> None:
    _ensure_defaults()
    data = _json_read(B.CONFIG_FILE) if B.CONFIG_FILE.exists() else {}
    section = data.get("reporting") or {}
    if not isinstance(section, dict):
        return
    B.network_settings["disable_log_upload"] = bool(section.get("disable_log_upload", False))
    B.network_settings["disable_state_upload"] = bool(section.get("disable_state_upload", False))
    instance = _s(section.get("client_instance_id"))
    if instance:
        B.network_settings["client_instance_id"] = instance[:80]


def _save_config() -> tuple[bool, str]:
    _ensure_defaults()
    data = _json_read(B.CONFIG_FILE) if B.CONFIG_FILE.exists() else {}
    data["reporting"] = {
        "disable_log_upload": bool(B.network_settings.get("disable_log_upload", False)),
        "disable_state_upload": bool(B.network_settings.get("disable_state_upload", False)),
        "client_instance_id": _s(B.network_settings.get("client_instance_id")) or uuid.uuid4().hex,
    }
    return _json_write(B.CONFIG_FILE, data)


def _active_url() -> str:
    try:
        return _s(B.active_controller_url())
    except Exception:
        return _s(getattr(B, "DG_URL", ""))


def _is_encrypted_remote() -> bool:
    url = _active_url()
    try:
        return urlsplit(url).scheme.lower() == "wss"
    except Exception:
        return False


def _is_remote_mode() -> bool:
    url = _active_url()
    try:
        parts = urlsplit(url)
        if parts.scheme.lower() not in {"ws", "wss"}:
            return False
        return (parts.hostname or "") not in {"127.0.0.1", "localhost", "::1"}
    except Exception:
        return False


def _relay_origin(url: str | None = None) -> str:
    """Return a stable origin key for ban scoping (scheme + host + port)."""
    raw = _s(url if url is not None else _active_url())
    try:
        parts = urlsplit(raw)
        scheme = parts.scheme.lower()
        host = (parts.hostname or "").lower()
        if scheme not in {"ws", "wss"} or not host:
            return ""
        port = parts.port or (443 if scheme == "wss" else 80)
        display_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
        return f"{scheme}://{display_host}:{port}"
    except Exception:
        return ""


def current_target_blocked() -> bool:
    """Whether the *currently applied remote relay* is the banned origin.

    Direct mode is always false even if an official/custom relay ban is still
    remembered in the background.
    """
    if not _is_remote_mode():
        return False
    origin = _relay_origin()
    if not origin:
        return False
    with _STATE_LOCK:
        return bool(_STATE.get("blocked")) and _s(_STATE.get("blocked_origin")) == origin


def _send_json(payload: dict) -> bool:
    if not _is_encrypted_remote():
        return False
    ws = getattr(B, "dg_ws", None)
    if ws is None:
        return False
    try:
        message = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        # Keep private diagnostics bounded independently from game RPC frames.
        if len(message.encode("utf-8")) > 220_000:
            return False
        with B.ws_send_lock:
            ws.send(message)
        return True
    except Exception as exc:
        with _STATE_LOCK:
            _STATE["last_error"] = str(exc)
        return False


def _privacy() -> dict:
    return {
        "logUploadDisabled": bool(B.network_settings.get("disable_log_upload", False)),
        "stateUploadDisabled": bool(B.network_settings.get("disable_state_upload", False)),
    }


def _client_hello() -> dict:
    with B.dg_lock:
        controller_id = _s(B.dg.get("controller_id"))
    return {
        "type": "coyote.control",
        "op": "hello",
        "protocol": 1,
        "clientInstanceId": _s(B.network_settings.get("client_instance_id")),
        "controllerId": controller_id or None,
        "client": {
            "name": "Coyote PEAK Controller",
            "platform": platform.system(),
            "python": platform.python_version(),
            # Deliberately do not upload OS username, hostname, home paths or MAC addresses.
        },
        "privacy": _privacy(),
        "time": time.time(),
    }


def send_hello() -> bool:
    if not _is_encrypted_remote():
        return False
    ok = _send_json(_client_hello())
    if ok:
        with _STATE_LOCK:
            _STATE["connected"] = True
            _STATE["encrypted"] = True
    return ok


def _safe_deepcopy(value, fallback):
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except Exception:
        return fallback


def _multiplayer_snapshot() -> dict:
    if MP is None:
        return {"apps": {}, "players": {}, "bindings": {}, "defaultRoute": {}}

    result = {"apps": {}, "players": {}, "bindings": {}, "defaultRoute": {}}
    try:
        lock = getattr(MP, "_DEVICE_LOCK", None)
        if lock is not None:
            with lock:
                result["apps"] = _safe_deepcopy(getattr(MP, "_MULTI_APPS", {}), {})
        else:
            result["apps"] = _safe_deepcopy(getattr(MP, "_MULTI_APPS", {}), {})
    except Exception:
        pass
    try:
        lock = getattr(MP, "_PLAYER_LOCK", None)
        state = getattr(MP, "_PLAYER_STATE", {})
        if lock is not None:
            with lock:
                result["players"] = _safe_deepcopy(state, {})
        else:
            result["players"] = _safe_deepcopy(state, {})
    except Exception:
        pass
    try:
        lock = getattr(MP, "_BIND_LOCK", None)
        bindings = getattr(MP, "_PLAYER_BINDINGS", {})
        if lock is not None:
            with lock:
                result["bindings"] = _safe_deepcopy(bindings, {})
        else:
            result["bindings"] = _safe_deepcopy(bindings, {})
    except Exception:
        pass
    try:
        fn = getattr(MP, "_default_route_snapshot", None)
        if callable(fn):
            result["defaultRoute"] = _safe_deepcopy(fn(), {})
    except Exception:
        pass
    return result


def _state_payload() -> dict:
    with B.peak_lock:
        peak = _safe_deepcopy(B.latest_peak if isinstance(B.latest_peak, dict) else {}, {})
        peak_time = float(getattr(B, "last_peak_time", 0.0) or 0.0)
    with B.dg_lock:
        dg = _safe_deepcopy(B.dg if isinstance(B.dg, dict) else {}, {})

    return {
        "type": "coyote.report",
        "kind": "state",
        "clientInstanceId": _s(B.network_settings.get("client_instance_id")),
        "time": time.time(),
        "peakTimestamp": peak_time,
        "peak": peak,
        "dg": dg,
        "multiplayer": _multiplayer_snapshot(),
        "privacy": _privacy(),
    }


def send_state(force=False) -> bool:
    global _LAST_STATE_SENT
    if bool(B.network_settings.get("disable_state_upload", False)):
        return False
    if not _is_encrypted_remote():
        return False
    now = time.time()
    with _POLICY_LOCK:
        interval = max(1.0, min(60.0, float(_POLICY.get("stateIntervalSeconds", 2) or 2)))
    if not force and now - _LAST_STATE_SENT < interval:
        return False
    ok = _send_json(_state_payload())
    if ok:
        _LAST_STATE_SENT = now
        with _STATE_LOCK:
            _STATE["last_report_at"] = now
    return ok


def _sanitize_log(item) -> dict:
    item = item if isinstance(item, dict) else {}
    return {
        "time": _s(item.get("time"))[:32],
        "timestamp": float(item.get("timestamp", 0.0) or 0.0),
        "category": _s(item.get("category"))[:80],
        "event": _s(item.get("event"))[:160],
        "detail": _s(item.get("detail"))[:3000],
        "output": _safe_deepcopy(item.get("output") if isinstance(item.get("output"), dict) else {}, {}),
    }


def _log_snapshot(max_items=100) -> tuple[int, list[dict]]:
    with B.log_lock:
        revision = int(getattr(B, "log_revision", 0) or 0)
        items = list(getattr(B, "event_logs", []))[-max_items:]
    return revision, [_sanitize_log(x) for x in items]


def send_logs(force=False, final=False, initial=False) -> bool:
    global _LAST_LOG_REVISION, _LAST_LOG_SENT
    if bool(B.network_settings.get("disable_log_upload", False)):
        return False
    if not _is_encrypted_remote():
        return False

    now = time.time()
    with _POLICY_LOCK:
        mode = _s(_POLICY.get("logMode", "interval")).lower()
        interval = max(3.0, min(3600.0, float(_POLICY.get("logIntervalSeconds", 15) or 15)))
    if mode == "off":
        return False
    if mode == "realtime":
        interval = 1.0
    if not force and not initial and now - _LAST_LOG_SENT < interval:
        return False

    revision, all_items = _log_snapshot(120 if initial else 80)
    if not initial and not final and revision == _LAST_LOG_REVISION:
        _LAST_LOG_SENT = now
        return False

    if initial and _LAST_LOG_REVISION > 0:
        initial = False

    if initial:
        items = all_items
    else:
        delta = max(0, revision - _LAST_LOG_REVISION)
        items = all_items[-min(max(delta, 1), 80):] if all_items else []

    payload = {
        "type": "coyote.report",
        "kind": "logs",
        "clientInstanceId": _s(B.network_settings.get("client_instance_id")),
        "time": now,
        "revision": revision,
        "initial": bool(initial),
        "final": bool(final),
        "logs": items,
        "privacy": _privacy(),
    }
    ok = _send_json(payload)
    if ok:
        _LAST_LOG_REVISION = revision
        _LAST_LOG_SENT = now
        with _STATE_LOCK:
            _STATE["last_log_at"] = now
    return ok


def send_privacy_update() -> bool:
    return _send_json({
        "type": "coyote.control",
        "op": "privacy",
        "clientInstanceId": _s(B.network_settings.get("client_instance_id")),
        "privacy": _privacy(),
        "time": time.time(),
    })


def _set_blocked(reason: str, *, origin: str | None = None) -> None:
    reason = _s(reason) or "服务器已拒绝此 IP 的中继连接"
    scope = _s(origin) or _relay_origin()
    # Never create a public-relay ban state while the active transport is Direct.
    if not scope or not scope.startswith(("wss://", "ws://")):
        return
    try:
        scope_host = (urlsplit(scope).hostname or "").lower()
    except Exception:
        scope_host = ""
    if scope_host in {"127.0.0.1", "localhost", "::1"}:
        return
    with _STATE_LOCK:
        _STATE["blocked"] = True
        _STATE["blocked_reason"] = reason[:300]
        _STATE["blocked_origin"] = scope
        _STATE["last_notice"] = reason[:300]
        _STATE["last_notice_type"] = "blocked"
        _STATE["connected"] = False
    B.remote_relay_blocked = True
    B.remote_relay_block_reason = reason[:300]
    B.remote_relay_block_origin = scope
    # Only overwrite the visible DG state when this ban belongs to the active
    # remote target.  Direct mode must remain completely usable.
    if current_target_blocked():
        with B.dg_lock:
            B.dg["server"] = "已被中继封禁"
            B.dg["error"] = reason[:300]
    try:
        B.add_log("连接", "中继拒绝连接", f"{reason[:300]} · {scope}")
    except Exception:
        pass


def clear_blocked_state(*, origin: str | None = None, force: bool = False) -> bool:
    """Clear remembered ban state.

    When ``origin`` is provided, only a ban for that relay is cleared.  This is
    important when a connection to another relay succeeds while an older relay
    remains banned.
    """
    expected = _s(origin)
    with _STATE_LOCK:
        stored = _s(_STATE.get("blocked_origin"))
        if not force and expected and stored and stored != expected:
            return False
        had_block = bool(_STATE.get("blocked"))
        _STATE["blocked"] = False
        _STATE["blocked_reason"] = ""
        _STATE["blocked_origin"] = ""
        _STATE["block_revision"] = 0
    B.remote_relay_blocked = False
    B.remote_relay_block_reason = ""
    B.remote_relay_block_origin = ""
    return had_block


def _relay_status_url() -> str:
    raw = _active_url()
    parts = urlsplit(raw)
    if parts.scheme.lower() != "wss" or not parts.hostname:
        return ""
    host = parts.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = parts.port
    netloc = host + (f":{port}" if port and port != 443 else "")
    return f"https://{netloc}/relay-status"


def check_block_status_async(force=False, *, wait_for_change=False) -> None:
    """Check the current relay ban state over HTTPS.

    While banned, ``wait_for_change`` uses a TLS long-poll.  The server releases
    the request as soon as its block-list revision changes, so an administrator
    unblocking the IP reconnects the client immediately instead of waiting for
    a countdown interval.
    """
    url = _relay_status_url()
    origin = _relay_origin()
    if not url or not origin or not _is_remote_mode():
        return
    now = time.time()
    with _STATE_LOCK:
        if wait_for_change and _STATE.get("block_watch_running"):
            return
        if not wait_for_change and not force and now - float(_STATE.get("blocked_checked_at", 0.0) or 0.0) < 2:
            return
        _STATE["blocked_checked_at"] = now
        revision = int(_STATE.get("block_revision", 0) or 0)
        if wait_for_change:
            _STATE["block_watch_running"] = True

    def worker():
        try:
            query = {}
            timeout = 5.0
            if wait_for_change:
                query = {"since": str(max(0, revision)), "wait": "25"}
                timeout = 32.0
            request_url = url + (("?" + urlencode(query)) if query else "")
            req = Request(
                request_url,
                headers={
                    "User-Agent": "Coyote-Relay-Status/2.6.1",
                    "Accept": "application/json",
                    "Cache-Control": "no-cache",
                },
            )
            with urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read(32_768).decode("utf-8", errors="replace"))

            # The user may have switched to Direct or another relay while this
            # HTTPS request was pending.  Never apply a stale result to it.
            if origin != _relay_origin() or not _is_remote_mode():
                return

            if isinstance(data, dict):
                try:
                    server_revision = max(0, int(data.get("revision", revision) or 0))
                except Exception:
                    server_revision = revision
                with _STATE_LOCK:
                    _STATE["block_revision"] = server_revision
                    _STATE["blocked_checked_at"] = time.time()

                if data.get("blocked"):
                    _set_blocked(_s(data.get("reason")) or "服务器已封禁当前公网 IP", origin=origin)
                elif data.get("ok"):
                    with _STATE_LOCK:
                        was_blocked = bool(_STATE.get("blocked")) and _s(_STATE.get("blocked_origin")) == origin
                    clear_blocked_state(origin=origin)
                    if was_blocked:
                        try:
                            B.add_log("连接", "服务器解除封禁", f"{origin} · 立即恢复连接")
                        except Exception:
                            pass
                        try:
                            B.request_network_reconnect("服务器已解除 IP 封禁")
                        except Exception:
                            pass
        except Exception as exc:
            # A long-poll timeout/network interruption is not itself a ban-state
            # change.  Keep the remembered reason and let the watcher reconnect.
            with _STATE_LOCK:
                _STATE["last_error"] = str(exc)[:500]
        finally:
            if wait_for_change:
                with _STATE_LOCK:
                    _STATE["block_watch_running"] = False

    threading.Thread(target=worker, name="CoyoteRelayBlockWatch" if wait_for_change else "CoyoteRelayBlockCheck", daemon=True).start()


def _apply_policy(payload: dict) -> None:
    policy = payload.get("policy") if isinstance(payload, dict) else None
    if not isinstance(policy, dict):
        return
    with _POLICY_LOCK:
        mode = _s(policy.get("logMode", _POLICY["logMode"])).lower()
        if mode in {"off", "realtime", "interval"}:
            _POLICY["logMode"] = mode
        for key, lo, hi in (
            ("logIntervalSeconds", 3, 3600),
            ("stateIntervalSeconds", 1, 60),
            ("blockRecheckSeconds", 15, 3600),
        ):
            try:
                _POLICY[key] = max(lo, min(hi, int(policy.get(key, _POLICY[key]))))
            except Exception:
                pass


def policy_snapshot() -> dict:
    with _POLICY_LOCK:
        return dict(_POLICY)


def reporting_snapshot() -> dict:
    with _STATE_LOCK:
        data = dict(_STATE)
    data["privacy"] = _privacy()
    data["policy"] = policy_snapshot()
    data["transport"] = _active_url()
    data["blocked_current"] = current_target_blocked()
    return data


def _report_loop() -> None:
    while not B.stop_event.is_set():
        try:
            if current_target_blocked():
                # Keep one encrypted long-poll open.  The server wakes it as soon
                # as block/unblock state changes, so there is no 15/60s countdown.
                check_block_status_async(force=True, wait_for_change=True)
                time.sleep(0.5)
                continue

            if _is_encrypted_remote() and getattr(B, "dg_ws", None) is not None:
                send_state(force=False)
                send_logs(force=False)
            time.sleep(0.75)
        except Exception:
            time.sleep(1.0)


def _start_report_thread() -> None:
    global _REPORT_THREAD
    if _REPORT_THREAD is not None and _REPORT_THREAD.is_alive():
        return
    _REPORT_THREAD = threading.Thread(target=_report_loop, name="CoyoteRemoteReporting", daemon=True)
    _REPORT_THREAD.start()


def _handle_private_message(message) -> bool:
    try:
        data = json.loads(message) if isinstance(message, str) else json.loads(message.decode("utf-8"))
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    msg_type = data.get("type")
    if msg_type == "coyote.policy":
        _apply_policy(data)
        return True
    if msg_type == "coyote.notice":
        event = _s(data.get("event"))
        reason = _s(data.get("reason")) or event
        with _STATE_LOCK:
            _STATE["last_notice"] = reason[:300]
            _STATE["last_notice_type"] = event[:80]
        if event == "blocked":
            _set_blocked(reason)
        elif event == "kicked":
            with B.dg_lock:
                B.dg["error"] = f"服务器踢下线：{reason}"
            try:
                B.add_log("连接", "服务器踢下线", reason)
            except Exception:
                pass
        elif event == "diagnostic_rejected":
            try:
                B.add_log("连接", "远程诊断未上传", reason)
            except Exception:
                pass
        return True
    return False


def install_backend() -> None:
    global _BACKEND_INSTALLED, _ORIGINAL_ON_MESSAGE, _ORIGINAL_ON_OPEN
    global _ORIGINAL_ON_ERROR, _ORIGINAL_ON_CLOSE, _ORIGINAL_LOAD_CONFIG, _ORIGINAL_SAVE_CONFIG
    if _BACKEND_INSTALLED:
        return
    _BACKEND_INSTALLED = True

    _ensure_defaults()
    _load_config()
    B.remote_relay_blocked = False
    B.remote_relay_block_reason = ""
    B.remote_relay_block_origin = ""
    B.clear_remote_relay_block = clear_blocked_state
    B.remote_relay_is_current_blocked = current_target_blocked
    B.check_remote_relay_block_status = check_block_status_async
    B.remote_reporting_snapshot = reporting_snapshot
    B.save_remote_reporting_config = _save_config

    _ORIGINAL_LOAD_CONFIG = B.load_config
    def load_config_wrapper():
        result = _ORIGINAL_LOAD_CONFIG()
        _load_config()
        return result
    B.load_config = load_config_wrapper

    _ORIGINAL_SAVE_CONFIG = getattr(B, "save_config", None)
    if callable(_ORIGINAL_SAVE_CONFIG):
        def save_config_wrapper(*args, **kwargs):
            result = _ORIGINAL_SAVE_CONFIG(*args, **kwargs)
            _save_config()
            return result
        B.save_config = save_config_wrapper

    _ORIGINAL_ON_MESSAGE = B.on_message
    _ORIGINAL_ON_OPEN = B.on_open
    _ORIGINAL_ON_ERROR = B.on_error
    _ORIGINAL_ON_CLOSE = B.on_close

    def on_open_wrapper(ws):
        clear_blocked_state(origin=_relay_origin())
        with _STATE_LOCK:
            _STATE["connected"] = True
            _STATE["encrypted"] = _is_encrypted_remote()
            _STATE["last_error"] = ""
        _ORIGINAL_ON_OPEN(ws)

    def on_message_wrapper(ws, message):
        # Server-private diagnostics/control frames must never reach DG-LAB logic.
        if _handle_private_message(message):
            return
        _ORIGINAL_ON_MESSAGE(ws, message)
        try:
            parsed = json.loads(message)
        except Exception:
            parsed = None
        if isinstance(parsed, dict) and parsed.get("type") == "hello":
            # Server has assigned the actual controller id at this point.
            send_hello()
            send_state(force=True)
            send_logs(force=True, initial=True)

    def on_error_wrapper(ws, error):
        text = str(error)
        with _STATE_LOCK:
            _STATE["last_error"] = text[:500]
        _ORIGINAL_ON_ERROR(ws, error)
        if "403" in text or "Forbidden" in text:
            _set_blocked("服务器拒绝连接，正在读取封禁原因…")
            check_block_status_async(force=True)

    def on_close_wrapper(ws, code, reason):
        with _STATE_LOCK:
            _STATE["connected"] = False
        if int(code or 0) == 4009:
            _set_blocked(_s(reason) or "服务器已封禁当前连接")
        _ORIGINAL_ON_CLOSE(ws, code, reason)

    B.on_open = on_open_wrapper
    B.on_message = on_message_wrapper
    B.on_error = on_error_wrapper
    B.on_close = on_close_wrapper

    _start_report_thread()


def install_ui(UI) -> None:
    global _UI_INSTALLED
    if _UI_INSTALLED:
        return
    _UI_INSTALLED = True

    original_save_full_config = UI.save_full_config

    def save_full_config_wrapper():
        ok, message = original_save_full_config()
        if not ok:
            return ok, message
        privacy_ok, privacy_message = _save_config()
        return (True, message) if privacy_ok else (False, privacy_message)

    UI.save_full_config = save_full_config_wrapper
    BaseWindow = UI.Window

    class ReportingWindow(BaseWindow):
        def build_logs(self):
            super().build_logs()
            box = UI.QGroupBox("远程诊断与隐私")
            layout = UI.QVBoxLayout(box)
            note = UI.QLabel(
                "仅在官方/自定义 WSS 中继下上传诊断数据；直连或 ws:// 不上传。"
                "这两个本机隐私开关优先级高于服务器策略，不影响 DG-LAB 中转本身。"
            )
            note.setObjectName("muted")
            note.setWordWrap(True)
            layout.addWidget(note)

            self.disable_log_upload = UI.QCheckBox("禁止上传日志")
            self.disable_log_upload.setChecked(bool(B.network_settings.get("disable_log_upload", False)))
            self.disable_state_upload = UI.QCheckBox("禁止上传设备信息和游戏信息")
            self.disable_state_upload.setChecked(bool(B.network_settings.get("disable_state_upload", False)))
            layout.addWidget(self.disable_log_upload)
            layout.addWidget(self.disable_state_upload)

            info = UI.QFormLayout()
            self.remote_policy_label = UI.QLabel("-")
            self.remote_policy_label.setWordWrap(True)
            self.remote_status_label = UI.QLabel("-")
            self.remote_status_label.setWordWrap(True)
            info.addRow("服务器日志策略", self.remote_policy_label)
            info.addRow("远程诊断状态", self.remote_status_label)
            layout.addLayout(info)

            self.disable_log_upload.toggled.connect(self._reporting_privacy_changed)
            self.disable_state_upload.toggled.connect(self._reporting_privacy_changed)

            root = self.logs.layout()
            if root is not None:
                root.insertWidget(0, box)

        def _reporting_privacy_changed(self, *_args):
            B.network_settings["disable_log_upload"] = bool(self.disable_log_upload.isChecked())
            B.network_settings["disable_state_upload"] = bool(self.disable_state_upload.isChecked())
            _save_config()
            send_privacy_update()
            if not B.network_settings["disable_state_upload"]:
                send_state(force=True)
            if not B.network_settings["disable_log_upload"]:
                send_logs(force=True, initial=True)

        def refresh_ui(self):
            super().refresh_ui()
            snap = reporting_snapshot()
            if hasattr(self, "remote_policy_label"):
                p = snap.get("policy") or {}
                mode = {"off": "关闭", "realtime": "实时", "interval": "间隔"}.get(p.get("logMode"), p.get("logMode", "-"))
                self.remote_policy_label.setText(
                    f"{mode} · 日志 {p.get('logIntervalSeconds', '-')}s · 状态 {p.get('stateIntervalSeconds', '-')}s"
                )
                if snap.get("blocked_current"):
                    status = f"已被中继封禁：{snap.get('blocked_reason') or '-'}"
                elif not _is_remote_mode():
                    status = "直连模式：不向公网服务器上传诊断数据"
                elif not snap.get("encrypted") and not _is_encrypted_remote():
                    status = "当前不是 WSS：诊断上传已禁用"
                elif snap.get("connected"):
                    status = "WSS 加密通道已连接"
                    if snap.get("last_notice_type") == "diagnostic_rejected":
                        status += " · " + (_s(snap.get("last_notice")) or "远程诊断被服务器限制")
                else:
                    status = "等待 WSS 中继连接"
                self.remote_status_label.setText(status)

            # Do not paint an official/custom relay ban onto a Direct preview.
            # Also keep the network page's "未应用" message when the user has
            # only switched tabs but has not applied that selection yet.
            if current_target_blocked() and hasattr(self, "net_info"):
                try:
                    selected = self._selected_mode() if hasattr(self, "_selected_mode") else None
                    applied = _s(B.network_settings.get("connection_mode"))
                    if not selected or selected == applied:
                        self.net_info["state"].setText(
                            "已封禁 · " + (_s(getattr(B, "remote_relay_block_reason", "")) or "服务器拒绝连接")
                        )
                except Exception:
                    pass

        def closeEvent(self, event):
            # Best-effort final delta before the controller socket is closed.
            try:
                send_logs(force=True, final=True)
                send_state(force=True)
            except Exception:
                pass
            return super().closeEvent(event)

    UI.Window = ReportingWindow
