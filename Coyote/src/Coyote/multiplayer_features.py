"""Multiplayer telemetry + multi DG-LAB device routing for Coyote.

Runtime-only extension. It keeps backend.py/ui_qt.py unchanged and adds:
- Remote-player roster/status telemetry supplied by MultiplayerTelemetry.cs.
- A dedicated Qt multiplayer page with live player detail panels.
- Multiple DG-LAB APP/client tracking on the existing v4 relay server.
- Session-only player -> DG-LAB client/slot binding.
- Optional remote-player HP-damage output routed only to the bound device.

Remote output is OFF by default and bindings are never persisted across sessions.
"""

from __future__ import annotations

import json
import math
import socket
import threading
import time
import uuid
from io import BytesIO

import backend as B


MULTIPLAYER_VERSION = 5
_BACKEND_INSTALLED = False
_UI_INSTALLED = False

_PLAYER_LOCK = threading.RLock()
_DEVICE_LOCK = threading.RLock()
_BIND_LOCK = threading.RLock()
_REMOTE_RAMP_LOCK = threading.RLock()

# Current PEAK roster. Replaced atomically per multiplayer telemetry packet.
_PLAYER_STATE = {
    "revision": 0,
    "updated_at": 0.0,
    "scene": "",
    "players": {},
}

# DG-LAB controlled clients attached to the existing v4 controller connection.
# client_id -> {connected_at,last_seen,devices:{slot_id: device}}
_MULTI_APPS = {}

# Stable local/default route for the original single-player rule path.
# Once a real device is selected, it is never silently reassigned to another
# APP/slot merely because the selected device disconnects.
_DEFAULT_ROUTE = {
    "selected": False,
    "client_id": "",
    "slot_id": "",
    "device_name": "",
    "selected_at": 0.0,
    "automatic": False,
}

# Session-only player bindings. Never persisted by design.
# player_id -> {client_id, slot_id, enabled}
_PLAYER_BINDINGS = {}
_REMOTE_LAST_TRIGGER = {}
_REMOTE_RAMP_GENERATION = {}

# A second explicit gate in addition to backend.master_output_enabled.
# Deliberately resets OFF every application start.
REMOTE_OUTPUT_ENABLED = False

# Avoid a stale roster lingering forever if PEAK stops emitting multiplayer packets.
PLAYER_STALE_SECONDS = 2.0


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _float(value, default=0.0):
    try:
        value = float(value)
        return value if math.isfinite(value) else float(default)
    except Exception:
        return float(default)


def _int(value, default=0):
    try:
        return int(value)
    except Exception:
        return int(default)


def _str(value, default=""):
    value = default if value is None else value
    try:
        return str(value)
    except Exception:
        return str(default)


def _status_percent(value):
    raw = _float(value, 0.0)
    if abs(raw) <= 1.5:
        raw *= 100.0
    return max(0.0, min(100.0, raw))


def _canonical_status_name(name):
    raw = _str(name).strip()
    normalized = "".join(ch.lower() for ch in raw if ch.isalnum())
    if normalized.startswith("petrif"):
        return "Petrify"
    return raw or "Unknown"


def _status_map(player):
    """Name-based status map; never relies on fixed numeric indexes.

    Duplicate aliases are merged using the largest observed value. This avoids
    the classic Petrify/Petrified/Petrification alias showing 0 while another
    synchronized alias carries the real progress.
    """
    names = player.get("statusNames") or []
    values = player.get("statuses") or []
    result = {}
    if not isinstance(values, list):
        return result
    for i, raw_value in enumerate(values):
        raw_name = names[i] if isinstance(names, list) and i < len(names) else f"Status{i}"
        key = _canonical_status_name(raw_name)
        pct = _status_percent(raw_value)
        previous = result.get(key)
        if previous is None or pct > previous:
            result[key] = pct
    return result


def _player_key(player):
    for field in ("playerId", "networkId", "stableId"):
        value = _str(player.get(field, "")).strip()
        if value:
            return value
    instance = _str(player.get("instanceId", "")).strip()
    return f"instance:{instance}" if instance else ""


def _normalize_player(raw, scene=""):
    if not isinstance(raw, dict):
        return None
    key = _player_key(raw)
    if not key:
        return None

    position = raw.get("position") or {}
    if not isinstance(position, dict):
        position = {}

    player = {
        "playerId": key,
        "networkId": _str(raw.get("networkId", "")),
        "instanceId": _str(raw.get("instanceId", "")),
        "name": _str(raw.get("name", "") or "未知玩家"),
        "isLocal": bool(raw.get("isLocal", False)),
        "scene": _str(raw.get("scene", scene) or scene),
        "hp": max(0.0, min(100.0, _float(raw.get("hp", 100.0), 100.0))),
        "hpMax": max(1.0, _float(raw.get("hpMax", 100.0), 100.0)),
        "staminaCurrent": _float(raw.get("staminaCurrent", 0.0), 0.0),
        "staminaMax": max(0.0, _float(raw.get("staminaMax", 0.0), 0.0)),
        "extraStamina": _float(raw.get("extraStamina", 0.0), 0.0),
        "dead": bool(raw.get("dead", False)),
        "passedOut": bool(raw.get("passedOut", False)),
        "fullyPassedOut": bool(raw.get("fullyPassedOut", False)),
        "climbing": bool(raw.get("climbing", False)),
        "grounded": bool(raw.get("grounded", False)),
        "crouching": bool(raw.get("crouching", False)),
        "position": {
            "x": _float(position.get("x", 0.0), 0.0),
            "y": _float(position.get("y", 0.0), 0.0),
            "z": _float(position.get("z", 0.0), 0.0),
        },
        "distanceToLocal": max(0.0, _float(raw.get("distanceToLocal", 0.0), 0.0)),
        "statusNames": list(raw.get("statusNames") or []),
        "statuses": list(raw.get("statuses") or []),
        "lastSeen": time.time(),
    }
    player["statusMap"] = _status_map(player)
    return player


def _binding_for(player_id):
    with _BIND_LOCK:
        value = _PLAYER_BINDINGS.get(player_id)
        return dict(value) if isinstance(value, dict) else None


def _device_key(client_id, slot_id):
    return f"{client_id}:{slot_id}"


def _device_online_from_record(device):
    if not isinstance(device, dict):
        return False
    if device.get("present") is False:
        return False
    if device.get("hasDevice") is False:
        return False
    state = _str(device.get("connectState", "")).strip().lower()
    if state in {"disconnected", "disconnect", "offline", "closed", "false", "0"}:
        return False
    # DG-LAB snapshots normally carry hasDevice. If it is temporarily unknown,
    # a present slot is accepted until an explicit offline state arrives.
    return True


def _device_state(client_id, slot_id):
    client_id = _str(client_id).strip()
    slot_id = _str(slot_id).strip()
    with _DEVICE_LOCK:
        app = _MULTI_APPS.get(client_id)
        if not isinstance(app, dict):
            return {"app_connected": False, "online": False, "device": None, "state": "APP已断开"}
        device = (app.get("devices") or {}).get(slot_id)
        if not isinstance(device, dict):
            return {"app_connected": True, "online": False, "device": None, "state": "Slot未检测到"}
        online = _device_online_from_record(device)
        if online:
            state = "在线"
        elif device.get("present") is False:
            state = "设备离线·等待重连"
        elif device.get("hasDevice") is False:
            state = "郊狼未连接·等待重连"
        else:
            state = "设备不可用"
        return {"app_connected": True, "online": online, "device": dict(device), "state": state}


def _device_exists(client_id, slot_id):
    return bool(_device_state(client_id, slot_id).get("online"))


def _default_route_snapshot():
    with _DEVICE_LOCK:
        route = dict(_DEFAULT_ROUTE)
    if not route.get("selected"):
        route.update({"online": False, "state": "未选择本地主设备", "app_connected": False})
        return route
    state = _device_state(route.get("client_id"), route.get("slot_id"))
    route.update({
        "online": bool(state.get("online")),
        "state": state.get("state", "离线"),
        "app_connected": bool(state.get("app_connected")),
    })
    device = state.get("device") or {}
    if device:
        route["device_name"] = _str(device.get("name") or route.get("device_name") or "郊狼设备")
    return route


def _sync_default_backend():
    route = _default_route_snapshot()
    if not route.get("selected"):
        with B.dg_lock:
            B.dg["app_id"] = None
            B.dg["slot_id"] = None
            B.dg["device_name"] = None
            B.dg["device_type"] = None
            B.dg["has_device"] = None
            B.dg["connect_state"] = None
        return

    client_id = _str(route.get("client_id", "")).strip()
    slot_id = _str(route.get("slot_id", "")).strip()
    state = _device_state(client_id, slot_id)
    device = state.get("device") or {}
    app_connected = bool(state.get("app_connected"))

    with B.dg_lock:
        # If the APP itself is gone, do not leave a sendable app_id behind.
        # The stable identity remains in _DEFAULT_ROUTE for the multiplayer UI.
        B.dg["app_id"] = client_id if app_connected else None
        B.dg["slot_id"] = slot_id if app_connected else None
        B.dg["device_name"] = device.get("name") or route.get("device_name") or None
        B.dg["device_type"] = device.get("type")
        B.dg["has_device"] = bool(state.get("online"))
        B.dg["connect_state"] = device.get("connectState") or state.get("state")


def set_default_device(client_id, slot_id, *, automatic=False):
    client_id = _str(client_id).strip()
    slot_id = _str(slot_id).strip()
    if not client_id or not slot_id:
        return False, "设备信息不完整"
    state = _device_state(client_id, slot_id)
    if not state.get("online"):
        return False, "目标郊狼当前不在线"
    device = state.get("device") or {}
    with _DEVICE_LOCK:
        _DEFAULT_ROUTE.update({
            "selected": True,
            "client_id": client_id,
            "slot_id": slot_id,
            "device_name": _str(device.get("name") or "郊狼设备"),
            "selected_at": time.time(),
            "automatic": bool(automatic),
        })
    _sync_default_backend()
    B.add_log(
        "多人",
        "本地主设备已设置",
        f"client={client_id}; slot={slot_id}; {'自动首次选择' if automatic else '用户手动选择'}",
    )
    return True, "已设为本地主设备"


def _auto_select_default_if_unset():
    with _DEVICE_LOCK:
        if _DEFAULT_ROUTE.get("selected"):
            return False
        candidates = []
        for client_id, app in _MULTI_APPS.items():
            for slot_id, device in (app.get("devices") or {}).items():
                if _device_online_from_record(device):
                    candidates.append((float(app.get("connected_at", 0.0) or 0.0), client_id, slot_id))
    if not candidates:
        return False
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    _, client_id, slot_id = candidates[0]
    ok, _ = set_default_device(client_id, slot_id, automatic=True)
    return ok


def _cancel_route_ramps(client_id, slot_id):
    client_id = _str(client_id).strip()
    slot_id = _str(slot_id).strip()
    with _BIND_LOCK:
        for pid, binding in _PLAYER_BINDINGS.items():
            if (
                _str(binding.get("client_id")) == client_id
                and _str(binding.get("slot_id")) == slot_id
            ):
                _REMOTE_RAMP_GENERATION[pid] = _REMOTE_RAMP_GENERATION.get(pid, 0) + 1


def _on_device_transition(client_id, slot_id, was_online, is_online):
    if was_online == is_online:
        return
    route = _default_route_snapshot()
    is_default = (
        route.get("selected")
        and _str(route.get("client_id")) == _str(client_id)
        and _str(route.get("slot_id")) == _str(slot_id)
    )

    if not is_online:
        _cancel_route_ramps(client_id, slot_id)
        if is_default:
            _sync_default_backend()
            B.add_log(
                "连接",
                "本地主设备暂时离线",
                f"client={client_id}; slot={slot_id}; 不切换到其他设备，等待原设备恢复",
            )
        with _BIND_LOCK:
            affected = [
                pid for pid, binding in _PLAYER_BINDINGS.items()
                if _str(binding.get("client_id")) == _str(client_id)
                and _str(binding.get("slot_id")) == _str(slot_id)
            ]
        if affected:
            B.add_log(
                "连接",
                "绑定郊狼暂时离线",
                f"client={client_id}; slot={slot_id}; 玩家={','.join(affected)}; 绑定保留，输出暂停",
            )
        return

    # Same APP + same Slot recovered. Clear any stale queued operation first.
    try:
        send_rpc_to_client(client_id, "device.op.clear", {"s": _str(slot_id)})
    except Exception:
        pass
    if is_default:
        _sync_default_backend()
    with _BIND_LOCK:
        affected = [
            pid for pid, binding in _PLAYER_BINDINGS.items()
            if _str(binding.get("client_id")) == _str(client_id)
            and _str(binding.get("slot_id")) == _str(slot_id)
        ]
    B.add_log(
        "连接",
        "郊狼已恢复",
        f"client={client_id}; slot={slot_id}; 清除旧任务后恢复；绑定玩家={','.join(affected) or '-'}",
    )


def _clear_bound_device(binding, reason="玩家离开/解绑"):
    if not isinstance(binding, dict):
        return
    client_id = _str(binding.get("client_id", "")).strip()
    slot_id = _str(binding.get("slot_id", "")).strip()
    if not client_id or not slot_id:
        return
    # If the route is already offline there is nothing reachable to clear.
    if not _device_state(client_id, slot_id).get("app_connected"):
        return
    try:
        send_rpc_to_client(client_id, "device.op.clear", {"s": slot_id})
        B.add_log("多人", "清除绑定设备输出", f"{reason}; client={client_id}; slot={slot_id}")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Multiplayer PEAK telemetry
# ---------------------------------------------------------------------------

def _handle_multiplayer_packet(packet):
    if not isinstance(packet, dict):
        return

    scene = _str(packet.get("scene", ""))
    raw_players = packet.get("players") or []
    if not isinstance(raw_players, list):
        return

    current = {}
    for raw in raw_players[:64]:
        player = _normalize_player(raw, scene)
        if player is not None:
            current[player["playerId"]] = player

    with _PLAYER_LOCK:
        previous = dict(_PLAYER_STATE["players"])
        _PLAYER_STATE["players"] = current
        _PLAYER_STATE["scene"] = scene
        _PLAYER_STATE["updated_at"] = time.time()
        _PLAYER_STATE["revision"] += 1

    joined = [pid for pid in current if pid not in previous]
    left = [pid for pid in previous if pid not in current]

    for pid in joined:
        player = current[pid]
        B.add_log(
            "多人",
            "玩家加入",
            f"{player.get('name','未知玩家')} | id={pid} | scene={player.get('scene','')}",
        )

    for pid in left:
        old = previous[pid]
        binding = _binding_for(pid)
        if binding:
            _clear_bound_device(binding, "玩家已离开")
        with _BIND_LOCK:
            _PLAYER_BINDINGS.pop(pid, None)
            _REMOTE_LAST_TRIGGER.pop(pid, None)
        with _REMOTE_RAMP_LOCK:
            _REMOTE_RAMP_GENERATION.pop(pid, None)
        B.add_log(
            "多人",
            "玩家离开",
            f"{old.get('name','未知玩家')} | id={pid}",
        )

    # Compare only stable IDs that existed in both packets.
    for pid, player in current.items():
        old = previous.get(pid)
        if not isinstance(old, dict):
            continue
        _handle_remote_player_damage(player, old)


def get_multiplayer_snapshot():
    now = time.time()
    with _PLAYER_LOCK:
        updated = float(_PLAYER_STATE.get("updated_at", 0.0) or 0.0)
        if updated and now - updated > PLAYER_STALE_SECONDS:
            players = {}
        else:
            players = {
                pid: dict(player)
                for pid, player in (_PLAYER_STATE.get("players") or {}).items()
            }
        return {
            "revision": int(_PLAYER_STATE.get("revision", 0) or 0),
            "updated_at": updated,
            "scene": _str(_PLAYER_STATE.get("scene", "")),
            "players": players,
        }


# ---------------------------------------------------------------------------
# Multiple DG-LAB controlled clients / slots
# ---------------------------------------------------------------------------

def _normalize_device(device, client_id):
    if not isinstance(device, dict):
        return None
    slot_id = _str(device.get("slotId", "")).strip()
    if not slot_id:
        return None
    props = device.get("props") or {}
    slot_state = device.get("slotState") or {}
    if not isinstance(props, dict):
        props = {}
    if not isinstance(slot_state, dict):
        slot_state = {}
    return {
        "client_id": client_id,
        "slot_id": slot_id,
        "name": _str(device.get("name", "") or "郊狼设备"),
        "type": _str(device.get("type", "")),
        "hasDevice": slot_state.get("hasDevice"),
        "connectState": props.get("connectState"),
        "present": True,
        "last_seen": time.time(),
    }


def _ensure_app(client_id):
    client_id = _str(client_id).strip()
    if not client_id:
        return None
    app = _MULTI_APPS.get(client_id)
    if app is None:
        app = {
            "client_id": client_id,
            "connected_at": time.time(),
            "last_seen": time.time(),
            "devices": {},
        }
        _MULTI_APPS[client_id] = app
    else:
        app["last_seen"] = time.time()
    return app


def _update_multi_device_data(client_id, payload):
    """Update one APP without allowing slot-list compaction to change identity.

    Missing slots are retained as offline records for this APP session. That
    keeps player bindings and the local/default route attached to the exact
    clientId+slotId instead of accidentally sliding to the next list entry.
    Returns a list of (slot_id, was_online, is_online) transitions.
    """
    if not client_id or not isinstance(payload, dict):
        return []
    event = payload.get("ev")
    transitions = []
    with _DEVICE_LOCK:
        app = _ensure_app(client_id)
        if app is None:
            return []
        old_devices = app.setdefault("devices", {})
        old_online = {sid: _device_online_from_record(dev) for sid, dev in old_devices.items()}

        if event == "devices.snapshot":
            seen = set()
            for raw in payload.get("devices") or []:
                device = _normalize_device(raw, client_id)
                if not device:
                    continue
                sid = device["slot_id"]
                seen.add(sid)
                previous = old_devices.get(sid)
                if isinstance(previous, dict):
                    # Preserve a useful name/type when a patch/snapshot omits it.
                    if not device.get("name"):
                        device["name"] = previous.get("name") or "郊狼设备"
                    if not device.get("type"):
                        device["type"] = previous.get("type") or ""
                old_devices[sid] = device

            # Do not delete unseen historical slots during the same APP session.
            # Mark them explicitly offline so a B disconnect cannot make C become B.
            for sid, device in list(old_devices.items()):
                if sid in seen:
                    continue
                if not isinstance(device, dict):
                    continue
                device["present"] = False
                device["hasDevice"] = False
                device["connectState"] = "disconnected"

        elif event == "slots.patch":
            for raw in payload.get("slots") or []:
                if not isinstance(raw, dict):
                    continue
                slot_id = _str(raw.get("slotId", "")).strip()
                if not slot_id:
                    continue
                state = raw.get("slotState") or {}
                device = old_devices.setdefault(
                    slot_id,
                    {
                        "client_id": client_id,
                        "slot_id": slot_id,
                        "name": "郊狼设备",
                        "type": "",
                        "hasDevice": None,
                        "connectState": None,
                        "present": True,
                        "last_seen": time.time(),
                    },
                )
                device["present"] = True
                if isinstance(state, dict) and "hasDevice" in state:
                    device["hasDevice"] = state.get("hasDevice")
                device["last_seen"] = time.time()

        else:
            return []

        app["last_seen"] = time.time()
        for sid, device in old_devices.items():
            before = bool(old_online.get(sid, False))
            after = _device_online_from_record(device)
            if sid in old_online and before != after:
                transitions.append((sid, before, after))

    for sid, before, after in transitions:
        _on_device_transition(client_id, sid, before, after)

    # First usable device of the process may become the local default once.
    # After _DEFAULT_ROUTE.selected becomes true, no later disconnect can cause
    # automatic failover to another person's device.
    _auto_select_default_if_unset()
    route = _default_route_snapshot()
    if route.get("selected") and _str(route.get("client_id")) == _str(client_id):
        _sync_default_backend()
    return transitions


def get_multidevice_snapshot():
    with _DEVICE_LOCK:
        apps = {}
        flat = []
        for client_id, app in _MULTI_APPS.items():
            devices = {}
            for sid, raw_device in (app.get("devices") or {}).items():
                device = dict(raw_device)
                device["online"] = _device_online_from_record(device)
                if device["online"]:
                    device["stateText"] = "在线"
                elif device.get("present") is False:
                    device["stateText"] = "设备离线·等待重连"
                elif device.get("hasDevice") is False:
                    device["stateText"] = "郊狼未连接·等待重连"
                else:
                    device["stateText"] = "设备不可用"
                devices[sid] = device
                flat.append(device)
            apps[client_id] = {
                "client_id": client_id,
                "connected_at": app.get("connected_at", 0.0),
                "last_seen": app.get("last_seen", 0.0),
                "devices": devices,
            }
    with B.dg_lock:
        controller_id = B.dg.get("controller_id")
    route = _default_route_snapshot()
    return {
        "controller_id": controller_id,
        "default_app_id": route.get("client_id") if route.get("selected") else None,
        "default_route": route,
        "apps": apps,
        "devices": flat,
    }


def send_payload_to_client(client_id, payload):
    client_id = _str(client_id).strip()
    if not client_id:
        return False, "目标 DG-LAB APP 无效"

    with _DEVICE_LOCK:
        if client_id not in _MULTI_APPS:
            return False, "目标 DG-LAB APP 已断开"

    with B.dg_lock:
        ws = B.dg_ws
    if ws is None:
        return False, "WebSocket 未连接"

    packet = json.dumps(
        {
            "type": "message",
            "clientId": client_id,
            "data": payload,
        },
        ensure_ascii=False,
    )
    try:
        with B.ws_send_lock:
            ws.send(packet)
        return True, "已发送"
    except Exception as exc:
        B.add_log("错误", "多人设备发送失败", str(exc))
        return False, str(exc)


def send_rpc_to_client(client_id, method, data=None):
    payload = {
        "t": "req",
        "reqId": uuid.uuid4().hex,
        "m": method,
    }
    if data is not None:
        payload["data"] = data
    return send_payload_to_client(client_id, payload)


def bind_player_device(player_id, client_id, slot_id):
    player_id = _str(player_id).strip()
    client_id = _str(client_id).strip()
    slot_id = _str(slot_id).strip()
    if not player_id or not client_id or not slot_id:
        return False, "玩家或设备信息不完整"
    if not _device_exists(client_id, slot_id):
        return False, "设备不存在、已断开或未检测到郊狼"

    snapshot = get_multiplayer_snapshot()
    player = (snapshot.get("players") or {}).get(player_id)
    if not isinstance(player, dict):
        return False, "玩家已离开"
    if player.get("isLocal", False):
        return False, "本地玩家继续使用原有默认设备规则，避免重复输出"

    with _BIND_LOCK:
        old = _PLAYER_BINDINGS.get(player_id)
        if old and (old.get("client_id") != client_id or old.get("slot_id") != slot_id):
            _clear_bound_device(old, "切换绑定设备")
        _PLAYER_BINDINGS[player_id] = {
            "client_id": client_id,
            "slot_id": slot_id,
            "enabled": False,  # binding never arms output automatically
        }
    return True, "已绑定；仍需手动开启该玩家的远程伤害输出"


def unbind_player_device(player_id):
    player_id = _str(player_id).strip()
    with _BIND_LOCK:
        old = _PLAYER_BINDINGS.pop(player_id, None)
        _REMOTE_LAST_TRIGGER.pop(player_id, None)
    if old:
        _clear_bound_device(old, "解除玩家绑定")
        return True, "已解绑"
    return False, "该玩家没有绑定设备"


def set_player_binding_enabled(player_id, enabled):
    player_id = _str(player_id).strip()
    with _BIND_LOCK:
        binding = _PLAYER_BINDINGS.get(player_id)
        if not isinstance(binding, dict):
            return False, "请先绑定设备"
        binding["enabled"] = bool(enabled)
    return True, "已开启" if enabled else "已关闭"


def get_player_bindings():
    with _BIND_LOCK:
        snapshot = {pid: dict(value) for pid, value in _PLAYER_BINDINGS.items()}
    for pid, value in snapshot.items():
        state = _device_state(value.get("client_id"), value.get("slot_id"))
        value["online"] = bool(state.get("online"))
        value["app_connected"] = bool(state.get("app_connected"))
        value["state"] = state.get("state", "设备不可用")
        device = state.get("device") or {}
        value["device_name"] = _str(device.get("name") or "郊狼设备")
    return snapshot


def set_remote_output_enabled(enabled):
    global REMOTE_OUTPUT_ENABLED
    REMOTE_OUTPUT_ENABLED = bool(enabled)
    if not REMOTE_OUTPUT_ENABLED:
        # Clear every currently bound remote device as a hard stop.
        for binding in get_player_bindings().values():
            _clear_bound_device(binding, "多人远程输出总开关关闭")
    return REMOTE_OUTPUT_ENABLED


# ---------------------------------------------------------------------------
# Remote player damage -> bound DG-LAB device
# ---------------------------------------------------------------------------

def _resolve_rule_waveforms(cfg, tier):
    waveform_a_name = _str(cfg.get("waveform_a", "脉冲") or "脉冲")
    waveform_b_name = _str(cfg.get("waveform_b", "脉冲") or "脉冲")

    if tier is not None:
        inherit = getattr(B, "TIER_WAVEFORM_INHERIT", "沿用基础波形")
        wa = tier.get("waveform_a", inherit)
        wb = tier.get("waveform_b", inherit)
        if wa != inherit and wa in B.COYOTE_WAVEFORMS:
            waveform_a_name = wa
        if wb != inherit and wb in B.COYOTE_WAVEFORMS:
            waveform_b_name = wb

    waveform_a = B.COYOTE_WAVEFORMS.get(waveform_a_name)
    waveform_b = B.COYOTE_WAVEFORMS.get(waveform_b_name)
    return waveform_a_name, waveform_b_name, waveform_a, waveform_b


def _next_remote_ramp_generation(player_id):
    with _REMOTE_RAMP_LOCK:
        value = int(_REMOTE_RAMP_GENERATION.get(player_id, 0) or 0) + 1
        _REMOTE_RAMP_GENERATION[player_id] = value
        return value


def _remote_ramp_worker(player_id, generation, client_id, slot_id, channel, target, duration_ms, ramp_ms, steps):
    if target <= 0:
        return
    send_rpc_to_client(
        client_id,
        "device.op",
        {"s": slot_id, "c": channel, "t": 4, "v": 0, "d": duration_ms, "im": True},
    )
    start = time.monotonic()
    interval = ramp_ms / max(1, steps) / 1000.0
    last_level = -1

    for step in range(1, steps + 1):
        deadline = start + step * interval
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.03, remaining))

        with _REMOTE_RAMP_LOCK:
            if _REMOTE_RAMP_GENERATION.get(player_id) != generation:
                return
        if not REMOTE_OUTPUT_ENABLED or not bool(getattr(B, "master_output_enabled", False)):
            return
        binding = _binding_for(player_id)
        if not binding or not binding.get("enabled", False):
            return
        if binding.get("client_id") != client_id or _str(binding.get("slot_id")) != _str(slot_id):
            return
        if not _device_exists(client_id, slot_id):
            return

        level = max(0, min(target, int(round(target * step / steps))))
        if level == last_level and step != steps:
            continue
        last_level = level
        elapsed_ms = int((time.monotonic() - start) * 1000)
        remaining_duration = max(100, duration_ms - elapsed_ms)
        send_rpc_to_client(
            client_id,
            "device.op",
            {"s": slot_id, "c": channel, "t": 4, "v": level, "d": remaining_duration, "im": True},
        )


def _handle_remote_player_damage(player, previous):
    if player.get("isLocal", False):
        return
    if player.get("dead", False) or player.get("passedOut", False) or player.get("fullyPassedOut", False):
        return

    old_hp = _float(previous.get("hp", 100.0), 100.0)
    new_hp = _float(player.get("hp", 100.0), 100.0)
    drop = old_hp - new_hp
    if drop < 0.1:
        return

    B.add_log(
        "多人",
        "远程玩家受伤",
        f"{player.get('name','未知玩家')} | HP {old_hp:.1f}% → {new_hp:.1f}%（下降 {drop:.1f}%）",
    )

    if not REMOTE_OUTPUT_ENABLED:
        return
    if not bool(getattr(B, "master_output_enabled", False)):
        return

    player_id = player["playerId"]
    binding = _binding_for(player_id)
    if not binding or not binding.get("enabled", False):
        return

    client_id = _str(binding.get("client_id", "")).strip()
    slot_id = _str(binding.get("slot_id", "")).strip()
    if not _device_exists(client_id, slot_id):
        return

    # Reuse the existing HP rule's intensity/waveform/duration configuration,
    # but do NOT require its local-player enabled checkbox. Player binding is a
    # separate opt-in gate.
    try:
        cfg = B.get_rule_copy("hp")
    except Exception:
        return

    cooldown = max(0.0, _float(cfg.get("cooldown", 2.0), 2.0))
    now = time.monotonic()
    with _BIND_LOCK:
        last = float(_REMOTE_LAST_TRIGGER.get(player_id, 0.0) or 0.0)
        if now - last < cooldown:
            return
        _REMOTE_LAST_TRIGGER[player_id] = now

    info = B.calculate_rule_intensities(cfg, new_hp, drop)
    intensity_a = int(info.get("final_a", 0) or 0)
    intensity_b = int(info.get("final_b", 0) or 0)
    duration_a = B.resolve_rule_duration_ms(B.clamp_duration(cfg.get("play_time_a", 1000)))
    duration_b = B.resolve_rule_duration_ms(B.clamp_duration(cfg.get("play_time_b", 1000)))
    tier = info.get("tier")
    wa_name, wb_name, waveform_a, waveform_b = _resolve_rule_waveforms(cfg, tier)
    if waveform_a is None or waveform_b is None:
        return

    # Send waveforms first; intensity can then be absolute or ramped.
    results = []
    if intensity_a > 0:
        results.append(send_rpc_to_client(
            client_id,
            "device.op",
            {"s": slot_id, "c": 0, "t": 0, "d": duration_a, "im": True, "v": waveform_a},
        ))
    if intensity_b > 0:
        results.append(send_rpc_to_client(
            client_id,
            "device.op",
            {"s": slot_id, "c": 1, "t": 0, "d": duration_b, "im": True, "v": waveform_b},
        ))

    ramp = bool(cfg.get("ramp_enabled", False))
    if ramp:
        generation = _next_remote_ramp_generation(player_id)
        ramp_ms = max(100, min(60000, _int(cfg.get("ramp_duration_ms", 1500), 1500)))
        steps = max(2, min(100, _int(cfg.get("ramp_steps", 10), 10)))
        if intensity_a > 0:
            threading.Thread(
                target=_remote_ramp_worker,
                args=(player_id, generation, client_id, slot_id, 0, intensity_a, duration_a, min(duration_a, ramp_ms), steps),
                name=f"CoyoteRemoteRamp-{player_id}-A",
                daemon=True,
            ).start()
        if intensity_b > 0:
            threading.Thread(
                target=_remote_ramp_worker,
                args=(player_id, generation, client_id, slot_id, 1, intensity_b, duration_b, min(duration_b, ramp_ms), steps),
                name=f"CoyoteRemoteRamp-{player_id}-B",
                daemon=True,
            ).start()
    else:
        if intensity_a > 0:
            results.append(send_rpc_to_client(
                client_id,
                "device.op",
                {"s": slot_id, "c": 0, "t": 4, "v": intensity_a, "d": duration_a, "im": True},
            ))
        if intensity_b > 0:
            results.append(send_rpc_to_client(
                client_id,
                "device.op",
                {"s": slot_id, "c": 1, "t": 4, "v": intensity_b, "d": duration_b, "im": True},
            ))

    success = bool(results) and all(ok for ok, _ in results)
    B.add_log(
        "多人输出",
        f"{player.get('name','未知玩家')} 受伤",
        (
            f"绑定 client={client_id} slot={slot_id} | "
            f"HP {old_hp:.1f}→{new_hp:.1f} | "
            f"A={intensity_a}/{wa_name}/{duration_a}ms | "
            f"B={intensity_b}/{wb_name}/{duration_b}ms | "
            f"{'渐升' if ramp else '直接'} | {'已发送' if success or ramp else '发送失败'}"
        ),
    )


# ---------------------------------------------------------------------------
# Backend monkey patches
# ---------------------------------------------------------------------------

def install_backend():
    global _BACKEND_INSTALLED
    if _BACKEND_INSTALLED:
        return
    _BACKEND_INSTALLED = True

    original_get_slot_id = getattr(B, "get_slot_id", lambda: None)
    original_on_close = getattr(B, "on_close", None)

    # Stable local-device accessor: once multiplayer mode has selected a default
    # route, an offline device behaves as "no device" instead of falling through
    # to another APP/slot.
    def stable_get_slot_id():
        route = _default_route_snapshot()
        if route.get("selected"):
            if not route.get("online"):
                return None
            return _str(route.get("slot_id", "")).strip() or None
        return original_get_slot_id()

    B.get_slot_id = stable_get_slot_id

    # 1) Replace the PEAK UDP loop only to multiplex local + multiplayer packets.
    # Local packet behavior is kept equivalent to backend.py. 64 KiB permits a
    # reasonably sized multiplayer roster while staying within one UDP datagram.
    def multiplayer_peak_udp_loop():
        B.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        B.udp_socket.settimeout(0.1)
        try:
            B.udp_socket.bind((B.PEAK_HOST, B.PEAK_PORT))
        except OSError as exc:
            B.add_log("错误", "PEAK UDP 监听失败", f"{B.PEAK_HOST}:{B.PEAK_PORT} - {exc}")
            return

        B.add_log("连接", "等待 PEAK", f"UDP {B.PEAK_HOST}:{B.PEAK_PORT}")

        while not B.stop_event.is_set():
            try:
                raw, _ = B.udp_socket.recvfrom(65535)
                current = json.loads(raw.decode("utf-8"))
                if not isinstance(current, dict):
                    continue

                if current.get("_coyotePacketType") == "multiplayer":
                    _handle_multiplayer_packet(current)
                    continue

                with B.peak_lock:
                    B.previous_peak = B.latest_peak
                    B.latest_peak = current
                    B.last_peak_time = time.time()
                    current_copy = B.latest_peak
                    previous_copy = B.previous_peak

                B.handle_extended_telemetry_events(current_copy, previous_copy)
                if not B.peak_was_online:
                    B.peak_was_online = True
                    B.add_log("连接", "PEAK 插件已连接", f"UDP {B.PEAK_PORT}")

                B.handle_game_rules(current_copy, previous_copy)
                B.handle_custom_rules(current_copy, previous_copy)

            except socket.timeout:
                if (
                    B.peak_was_online
                    and B.last_peak_time
                    and time.time() - B.last_peak_time > B.PEAK_OFFLINE
                ):
                    B.peak_was_online = False
                    B.add_log("连接", "PEAK 遥测暂停", "PEAK 可能处于大厅 / 加载 / 切图")
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass
            except Exception as exc:
                B.add_log("错误", "PEAK 数据处理异常", str(exc))
                time.sleep(0.1)

    B.peak_udp_loop = multiplayer_peak_udp_loop

    # 2) Multi-client DG-LAB routing. The relay already supports multiple
    # controlled clients. We keep exact clientId + slotId identity.
    def multiplayer_on_message(ws, message):
        try:
            data = json.loads(message)
        except Exception:
            return
        if not isinstance(data, dict):
            return

        msg_type = data.get("type")

        if msg_type == "hello":
            with B.dg_lock:
                B.dg["controller_id"] = data.get("clientId")
                B.dg["server"] = "已连接"
            B.add_log("连接", "控制端已连接", f"controller={data.get('clientId')}")
            return

        if msg_type == "client_attached":
            client_id = _str(data.get("clientId", "")).strip()
            with _DEVICE_LOCK:
                _ensure_app(client_id)
            # Do not claim it as default yet. The first real online Slot is
            # selected once a devices.snapshot arrives.
            B.add_log("连接", "DG-LAB APP 已接入", f"client={client_id}; 当前多设备APP={len(_MULTI_APPS)}")
            return

        if msg_type == "client_disconnected":
            client_id = _str(data.get("clientId", "")).strip()
            route_before = _default_route_snapshot()
            with _DEVICE_LOCK:
                _MULTI_APPS.pop(client_id, None)

            stale_players = []
            with _BIND_LOCK:
                for pid, binding in list(_PLAYER_BINDINGS.items()):
                    if _str(binding.get("client_id")) == client_id:
                        stale_players.append(pid)
                        _PLAYER_BINDINGS.pop(pid, None)
                        _REMOTE_LAST_TRIGGER.pop(pid, None)
                        _REMOTE_RAMP_GENERATION[pid] = _REMOTE_RAMP_GENERATION.get(pid, 0) + 1

            was_default = (
                route_before.get("selected")
                and _str(route_before.get("client_id")) == client_id
            )
            if was_default:
                # Critical: never promote another connected APP/Slot.
                _sync_default_backend()

            B.add_log(
                "连接",
                "DG-LAB APP 已断开",
                (
                    f"client={client_id}; 自动解除远程玩家绑定={len(stale_players)}; "
                    + ("本地主设备保持离线身份，不自动切换其他设备" if was_default else "其他设备绑定不受影响")
                ),
            )
            return

        if msg_type == "message":
            client_id = _str(data.get("clientId", "")).strip()
            payload = data.get("data")
            if client_id:
                _update_multi_device_data(client_id, payload)
            return

        if msg_type == "error":
            error_text = f"{data.get('code')}: {data.get('message') or ''}"
            with B.dg_lock:
                B.dg["error"] = error_text
            B.add_log("错误", "DG-LAB", error_text)
            return

    B.on_message = multiplayer_on_message

    # If the controller WebSocket itself dies, all controlled APP routes are
    # invalid. Clear remote bindings, but keep the selected local route identity
    # as offline so it cannot silently fail over after reconnect.
    def multiplayer_on_close(ws, code, reason):
        if callable(original_on_close):
            try:
                original_on_close(ws, code, reason)
            except Exception:
                pass
        with _DEVICE_LOCK:
            _MULTI_APPS.clear()
        with _BIND_LOCK:
            affected = list(_PLAYER_BINDINGS.keys())
            _PLAYER_BINDINGS.clear()
            _REMOTE_LAST_TRIGGER.clear()
            for pid in affected:
                _REMOTE_RAMP_GENERATION[pid] = _REMOTE_RAMP_GENERATION.get(pid, 0) + 1
        _sync_default_backend()
        if affected:
            B.add_log("连接", "多设备路由已清理", f"控制连接断开；解除远程绑定={len(affected)}")

    B.on_close = multiplayer_on_close

    # Public integration API used by the UI.
    B.get_multiplayer_snapshot = get_multiplayer_snapshot
    B.get_multidevice_snapshot = get_multidevice_snapshot
    B.get_player_bindings = get_player_bindings
    B.bind_player_device = bind_player_device
    B.unbind_player_device = unbind_player_device
    B.set_player_binding_enabled = set_player_binding_enabled
    B.set_multiplayer_remote_output_enabled = set_remote_output_enabled
    B.set_multiplayer_default_device = set_default_device
    B.send_rpc_to_client = send_rpc_to_client
    B.COYOTE_MULTIPLAYER_VERSION = MULTIPLAYER_VERSION


# ---------------------------------------------------------------------------
# Qt multiplayer page
# ---------------------------------------------------------------------------

def install_ui(UI):
    global _UI_INSTALLED
    if _UI_INSTALLED:
        return
    _UI_INSTALLED = True

    BaseWindow = UI.Window

    class MultiplayerWindow(BaseWindow):
        def __init__(self):
            super().__init__()
            self._mp_selected_player = ""
            self._mp_last_player_revision = -1
            self._mp_last_device_signature = None
            self._mp_last_qr_url = ""
            self._build_multiplayer_page()

            self._mp_timer = UI.QTimer(self)
            self._mp_timer.setInterval(250)
            self._mp_timer.timeout.connect(self._refresh_multiplayer_page)
            self._mp_timer.start()
            self._refresh_multiplayer_page()

        def _main_tab_widget(self):
            tabs = self.findChildren(UI.QTabWidget)
            if not tabs:
                return None
            # Main application tab widget normally has the most pages.
            return max(tabs, key=lambda item: item.count())

        def _build_multiplayer_page(self):
            tabs = self._main_tab_widget()
            if tabs is None:
                return

            self.multiplayer_tab = UI.QWidget()
            root = UI.QVBoxLayout(self.multiplayer_tab)
            root.setContentsMargins(8, 8, 8, 8)

            top_note = UI.QLabel(
                "实时显示 PEAK 当前联机角色。玩家退出后会从列表移除并解除其远程设备绑定。"
                "郊狼临时掉线只暂停输出并保留玩家绑定；同一 APP + Slot 恢复后自动恢复。"
                "本地主设备掉线绝不会自动切换到其他玩家的设备。"
            )
            top_note.setObjectName("muted")
            top_note.setWordWrap(True)
            root.addWidget(top_note)

            # Pairing / multi-device row.
            pairing = UI.QGroupBox("多郊狼连接")
            pairing_layout = UI.QHBoxLayout(pairing)

            qr_col = UI.QVBoxLayout()
            self.mp_qr_label = UI.QLabel("等待 DG-LAB 控制端连接…")
            self.mp_qr_label.setAlignment(UI.Qt.AlignmentFlag.AlignCenter)
            self.mp_qr_label.setMinimumSize(180, 180)
            self.mp_qr_label.setMaximumSize(220, 220)
            self.mp_qr_text = UI.QLabel("")
            self.mp_qr_text.setObjectName("muted")
            self.mp_qr_text.setWordWrap(True)
            qr_col.addWidget(self.mp_qr_label)
            qr_col.addWidget(self.mp_qr_text)
            pairing_layout.addLayout(qr_col)

            device_col = UI.QVBoxLayout()
            self.mp_device_summary = UI.QLabel("已连接 APP：0 / 郊狼：0")
            device_col.addWidget(self.mp_device_summary)
            self.mp_device_table = UI.QTableWidget(0, 6)
            self.mp_device_table.setHorizontalHeaderLabels(
                ["APP", "Slot", "设备", "类型", "有设备", "连接状态"]
            )
            self.mp_device_table.setEditTriggers(UI.QAbstractItemView.EditTrigger.NoEditTriggers)
            self.mp_device_table.setSelectionMode(UI.QAbstractItemView.SelectionMode.NoSelection)
            self.mp_device_table.horizontalHeader().setSectionResizeMode(UI.QHeaderView.ResizeMode.Stretch)
            self.mp_device_table.setMaximumHeight(190)
            device_col.addWidget(self.mp_device_table)

            default_row = UI.QHBoxLayout()
            default_row.addWidget(UI.QLabel("本地主设备"))
            self.mp_default_combo = UI.QComboBox()
            self.mp_set_default_button = UI.QPushButton("设为本地主设备")
            default_row.addWidget(self.mp_default_combo, 1)
            default_row.addWidget(self.mp_set_default_button)
            device_col.addLayout(default_row)
            self.mp_default_status = UI.QLabel("本地主设备：尚未选择；首次检测到的在线设备会自动选择一次")
            self.mp_default_status.setObjectName("muted")
            self.mp_default_status.setWordWrap(True)
            device_col.addWidget(self.mp_default_status)

            self.mp_remote_master = UI.QCheckBox("允许多人绑定设备响应远程玩家受伤")
            self.mp_remote_master.setChecked(False)
            self.mp_remote_master.setToolTip(
                "额外总开关。还需要左侧“允许电击输出”以及玩家自己的绑定输出开关同时开启。"
            )
            self.mp_remote_master.toggled.connect(
                lambda checked: B.set_multiplayer_remote_output_enabled(bool(checked))
            )
            device_col.addWidget(self.mp_remote_master)
            safety = UI.QLabel(
                "同一个二维码可以让多台 DG-LAB APP 接入；系统按 APP clientId + slotId 单独路由。"
                "仅给明确同意由游戏事件控制设备的玩家启用绑定输出。"
            )
            safety.setObjectName("muted")
            safety.setWordWrap(True)
            device_col.addWidget(safety)
            pairing_layout.addLayout(device_col, 1)
            root.addWidget(pairing)

            splitter = UI.QSplitter(UI.Qt.Orientation.Horizontal)
            root.addWidget(splitter, 1)

            left = UI.QWidget()
            left_layout = UI.QVBoxLayout(left)
            self.mp_player_count = UI.QLabel("联机玩家：0")
            left_layout.addWidget(self.mp_player_count)
            self.mp_player_list = UI.QListWidget()
            self.mp_player_list.currentItemChanged.connect(self._mp_player_selected)
            left_layout.addWidget(self.mp_player_list, 1)
            splitter.addWidget(left)

            right_scroll = UI.QScrollArea()
            right_scroll.setWidgetResizable(True)
            right = UI.QWidget()
            right_layout = UI.QVBoxLayout(right)

            self.mp_player_title = UI.QLabel("请选择玩家")
            try:
                font = self.mp_player_title.font()
                font.setPointSize(max(12, font.pointSize() + 2))
                font.setBold(True)
                self.mp_player_title.setFont(font)
            except Exception:
                pass
            right_layout.addWidget(self.mp_player_title)

            detail_box = UI.QGroupBox("玩家信息")
            detail_layout = UI.QGridLayout(detail_box)
            self.mp_detail_labels = {}
            fields = [
                ("id", "玩家 ID"),
                ("scene", "关卡 / Scene"),
                ("position", "世界坐标"),
                ("distance", "距本地玩家"),
                ("hp", "血量"),
                ("stamina", "体力"),
                ("extraStamina", "额外体力"),
                ("state", "角色状态"),
            ]
            for row, (key, title) in enumerate(fields):
                detail_layout.addWidget(UI.QLabel(title), row, 0)
                label = UI.QLabel("-")
                label.setTextInteractionFlags(UI.Qt.TextInteractionFlag.TextSelectableByMouse)
                detail_layout.addWidget(label, row, 1)
                self.mp_detail_labels[key] = label
            right_layout.addWidget(detail_box)

            status_box = UI.QGroupBox("异常状态 / 进度")
            status_layout = UI.QVBoxLayout(status_box)
            self.mp_status_table = UI.QTableWidget(0, 2)
            self.mp_status_table.setHorizontalHeaderLabels(["状态", "进度"])
            self.mp_status_table.setEditTriggers(UI.QAbstractItemView.EditTrigger.NoEditTriggers)
            self.mp_status_table.setSelectionMode(UI.QAbstractItemView.SelectionMode.NoSelection)
            self.mp_status_table.horizontalHeader().setSectionResizeMode(UI.QHeaderView.ResizeMode.Stretch)
            status_layout.addWidget(self.mp_status_table)
            status_note = UI.QLabel(
                "石化按 statusNames 名称匹配 Petrify / Petrified / Petrification，"
                "不使用固定下标；多个别名同时存在时取真实进度较大的值。"
            )
            status_note.setObjectName("muted")
            status_note.setWordWrap(True)
            status_layout.addWidget(status_note)
            right_layout.addWidget(status_box)

            bind_box = UI.QGroupBox("玩家 ↔ 郊狼绑定")
            bind_layout = UI.QGridLayout(bind_box)
            bind_layout.addWidget(UI.QLabel("目标设备"), 0, 0)
            self.mp_bind_combo = UI.QComboBox()
            bind_layout.addWidget(self.mp_bind_combo, 0, 1, 1, 3)
            self.mp_bind_button = UI.QPushButton("绑定")
            self.mp_unbind_button = UI.QPushButton("解绑")
            bind_layout.addWidget(self.mp_bind_button, 1, 1)
            bind_layout.addWidget(self.mp_unbind_button, 1, 2)
            self.mp_player_output = UI.QCheckBox("该玩家受伤时输出到绑定设备")
            bind_layout.addWidget(self.mp_player_output, 2, 0, 1, 4)
            self.mp_binding_status = UI.QLabel("未绑定")
            self.mp_binding_status.setObjectName("muted")
            self.mp_binding_status.setWordWrap(True)
            bind_layout.addWidget(self.mp_binding_status, 3, 0, 1, 4)
            bind_note = UI.QLabel(
                "远程受伤输出复用“血量下降”规则的 A/B 强度、随机强度、百分比档位、"
                "瞬时加强、波形、随机波形、持续时间、冷却和渐升参数；但不会要求本地 HP 规则本身开启。"
            )
            bind_note.setObjectName("muted")
            bind_note.setWordWrap(True)
            bind_layout.addWidget(bind_note, 4, 0, 1, 4)
            right_layout.addWidget(bind_box)
            right_layout.addStretch(1)

            self.mp_set_default_button.clicked.connect(self._mp_set_default_selected)
            self.mp_bind_button.clicked.connect(self._mp_bind_selected)
            self.mp_unbind_button.clicked.connect(self._mp_unbind_selected)
            self.mp_player_output.toggled.connect(self._mp_player_output_toggled)

            right_scroll.setWidget(right)
            splitter.addWidget(right_scroll)
            splitter.setStretchFactor(0, 1)
            splitter.setStretchFactor(1, 3)

            tabs.addTab(self.multiplayer_tab, "多人 / 多设备")

        def _mp_player_selected(self, current, _previous):
            self._mp_selected_player = ""
            if current is not None:
                value = current.data(UI.Qt.ItemDataRole.UserRole)
                self._mp_selected_player = _str(value).strip()
            self._refresh_selected_player(force=True)

        def _mp_current_player(self):
            snapshot = B.get_multiplayer_snapshot()
            return (snapshot.get("players") or {}).get(self._mp_selected_player)

        def _mp_set_default_selected(self):
            data = self.mp_default_combo.currentData(UI.Qt.ItemDataRole.UserRole)
            if not isinstance(data, tuple) or len(data) != 2:
                UI.QMessageBox.warning(self, "设置失败", "当前没有在线的郊狼可设为本地主设备")
                return
            ok, message = B.set_multiplayer_default_device(data[0], data[1])
            if not ok:
                UI.QMessageBox.warning(self, "设置失败", message)
            self._refresh_multiplayer_page()

        def _mp_bind_selected(self):
            player = self._mp_current_player()
            if not player:
                return
            data = self.mp_bind_combo.currentData(UI.Qt.ItemDataRole.UserRole)
            if not isinstance(data, tuple) or len(data) != 2:
                UI.QMessageBox.warning(self, "绑定失败", "当前没有可绑定的郊狼设备")
                return
            ok, message = B.bind_player_device(player["playerId"], data[0], data[1])
            if not ok:
                UI.QMessageBox.warning(self, "绑定失败", message)
            self._refresh_selected_player(force=True)

        def _mp_unbind_selected(self):
            player = self._mp_current_player()
            if not player:
                return
            B.unbind_player_device(player["playerId"])
            self._refresh_selected_player(force=True)

        def _mp_player_output_toggled(self, checked):
            if getattr(self, "_mp_syncing", False):
                return
            player = self._mp_current_player()
            if not player:
                return
            ok, message = B.set_player_binding_enabled(player["playerId"], bool(checked))
            if not ok and checked:
                self._mp_syncing = True
                self.mp_player_output.setChecked(False)
                self._mp_syncing = False
                UI.QMessageBox.warning(self, "无法开启", message)
            self._refresh_selected_player(force=True)

        def _refresh_qr(self, device_snapshot):
            url = B.pairing_url(device_snapshot.get("controller_id"))
            url = _str(url or "")
            if url == self._mp_last_qr_url:
                return
            self._mp_last_qr_url = url
            if not url:
                self.mp_qr_label.setPixmap(UI.QPixmap())
                self.mp_qr_label.setText("等待 DG-LAB 控制端连接…")
                self.mp_qr_text.setText("")
                return
            try:
                image = UI.qrcode.make(url)
                bio = BytesIO()
                image.save(bio, format="PNG")
                pixmap = UI.QPixmap()
                pixmap.loadFromData(bio.getvalue())
                self.mp_qr_label.setText("")
                self.mp_qr_label.setPixmap(
                    pixmap.scaled(
                        200,
                        200,
                        UI.Qt.AspectRatioMode.KeepAspectRatio,
                        UI.Qt.TransformationMode.SmoothTransformation,
                    )
                )
                self.mp_qr_text.setText("多台手机可依次扫描同一个二维码接入")
            except Exception as exc:
                self.mp_qr_label.setText("二维码生成失败")
                self.mp_qr_text.setText(str(exc))

        def _refresh_devices(self, snapshot):
            devices = snapshot.get("devices") or []
            apps = snapshot.get("apps") or {}
            default_route = snapshot.get("default_route") or {}
            signature = (
                tuple(
                    sorted(
                        (
                            _str(d.get("client_id")),
                            _str(d.get("slot_id")),
                            _str(d.get("name")),
                            bool(d.get("online", False)),
                            d.get("hasDevice"),
                            d.get("present"),
                            _str(d.get("connectState")),
                        )
                        for d in devices
                    )
                ),
                (
                    bool(default_route.get("selected")),
                    _str(default_route.get("client_id")),
                    _str(default_route.get("slot_id")),
                    bool(default_route.get("online", False)),
                    _str(default_route.get("state")),
                ),
            )
            online_count = sum(1 for d in devices if d.get("online"))
            self.mp_device_summary.setText(
                f"已连接 APP：{len(apps)} / 郊狼 Slot：{len(devices)} / 在线：{online_count}"
            )
            if signature == self._mp_last_device_signature:
                return
            self._mp_last_device_signature = signature

            self.mp_device_table.setRowCount(0)
            previous_bind = self.mp_bind_combo.currentData(UI.Qt.ItemDataRole.UserRole)
            previous_default = self.mp_default_combo.currentData(UI.Qt.ItemDataRole.UserRole)
            self.mp_bind_combo.clear()
            self.mp_default_combo.clear()

            default_key = (
                _str(default_route.get("client_id")),
                _str(default_route.get("slot_id")),
            )

            for d in devices:
                client_id = _str(d.get("client_id"))
                slot_id = _str(d.get("slot_id"))
                is_default = bool(default_route.get("selected")) and (client_id, slot_id) == default_key
                row = self.mp_device_table.rowCount()
                self.mp_device_table.insertRow(row)
                name = _str(d.get("name") or "郊狼设备")
                if is_default:
                    name = "★ " + name
                values = [
                    client_id,
                    slot_id,
                    name,
                    _str(d.get("type")),
                    "是" if d.get("online") else "否",
                    _str(d.get("stateText") or d.get("connectState") or "-"),
                ]
                for col, value in enumerate(values):
                    self.mp_device_table.setItem(row, col, UI.QTableWidgetItem(value))

                if not d.get("online"):
                    continue
                display = f"{d.get('name') or '郊狼设备'} | APP {client_id} | Slot {slot_id}"
                data = (client_id, slot_id)
                self.mp_bind_combo.addItem(display, data)
                self.mp_default_combo.addItem(display, data)

            # Keep combobox selections stable during periodic refresh.
            for combo, previous in (
                (self.mp_bind_combo, previous_bind),
                (self.mp_default_combo, previous_default),
            ):
                wanted = previous
                if combo is self.mp_default_combo and default_route.get("online"):
                    wanted = default_key
                if wanted is not None:
                    for i in range(combo.count()):
                        if combo.itemData(i, UI.Qt.ItemDataRole.UserRole) == wanted:
                            combo.setCurrentIndex(i)
                            break

            if not default_route.get("selected"):
                self.mp_default_status.setText(
                    "本地主设备：尚未选择；首次检测到的在线设备会自动选择一次"
                )
            else:
                device_name = _str(default_route.get("device_name") or "郊狼设备")
                state_text = _str(default_route.get("state") or "离线")
                suffix = "" if default_route.get("online") else "；不会自动切换到其他设备"
                self.mp_default_status.setText(
                    f"本地主设备：{device_name} | APP {default_key[0]} | Slot {default_key[1]} | {state_text}{suffix}"
                )

            self.mp_set_default_button.setEnabled(self.mp_default_combo.count() > 0)

        def _refresh_player_list(self, snapshot):
            revision = int(snapshot.get("revision", 0) or 0)
            if revision == self._mp_last_player_revision:
                return
            self._mp_last_player_revision = revision
            players = snapshot.get("players") or {}
            self.mp_player_count.setText(f"联机玩家：{len(players)}")

            selected = self._mp_selected_player
            self.mp_player_list.blockSignals(True)
            self.mp_player_list.clear()
            selected_row = -1

            ordered = sorted(
                players.values(),
                key=lambda p: (not bool(p.get("isLocal", False)), _str(p.get("name", "")).lower()),
            )
            bindings = B.get_player_bindings()
            for index, player in enumerate(ordered):
                pid = player["playerId"]
                status_bits = []
                if player.get("dead"):
                    status_bits.append("死亡")
                elif player.get("passedOut") or player.get("fullyPassedOut"):
                    status_bits.append("昏迷")
                status_map = player.get("statusMap") or {}
                if status_map.get("Petrify", 0.0) > 0.05:
                    status_bits.append(f"石化 {status_map['Petrify']:.1f}%")
                bound = bindings.get(pid)
                if bound:
                    device_mark = " 🔗" if bound.get("online") else " 🔗⚠"
                else:
                    device_mark = ""
                local_mark = "★ " if player.get("isLocal") else ""
                text = (
                    f"{local_mark}{player.get('name','未知玩家')}{device_mark}\n"
                    f"HP {player.get('hp',0):.1f}% | 体力 {player.get('staminaCurrent',0):.1f}/{player.get('staminaMax',0):.1f}"
                )
                if status_bits:
                    text += " | " + " / ".join(status_bits)
                item = UI.QListWidgetItem(text)
                item.setData(UI.Qt.ItemDataRole.UserRole, pid)
                self.mp_player_list.addItem(item)
                if pid == selected:
                    selected_row = index

            self.mp_player_list.blockSignals(False)
            if selected_row >= 0:
                self.mp_player_list.setCurrentRow(selected_row)
            elif self.mp_player_list.count() > 0:
                self.mp_player_list.setCurrentRow(0)
            else:
                self._mp_selected_player = ""
                self._refresh_selected_player(force=True)

        def _refresh_selected_player(self, force=False):
            player = self._mp_current_player()
            if not player:
                self.mp_player_title.setText("请选择玩家")
                for label in self.mp_detail_labels.values():
                    label.setText("-")
                self.mp_status_table.setRowCount(0)
                self.mp_binding_status.setText("未绑定")
                self.mp_player_output.setEnabled(False)
                self.mp_bind_button.setEnabled(False)
                self.mp_unbind_button.setEnabled(False)
                return

            local = bool(player.get("isLocal", False))
            self.mp_player_title.setText(
                ("★ " if local else "") + _str(player.get("name", "未知玩家"))
            )
            pos = player.get("position") or {}
            states = []
            for flag, text in (
                (player.get("dead"), "死亡"),
                (player.get("passedOut") or player.get("fullyPassedOut"), "昏迷"),
                (player.get("climbing"), "攀爬"),
                (player.get("crouching"), "蹲下"),
                (player.get("grounded"), "接地"),
            ):
                if flag:
                    states.append(text)
            if not states:
                states.append("正常")

            self.mp_detail_labels["id"].setText(
                f"{player.get('playerId')} | instance={player.get('instanceId') or '-'}"
            )
            self.mp_detail_labels["scene"].setText(_str(player.get("scene") or "-"))
            self.mp_detail_labels["position"].setText(
                f"X {pos.get('x',0):.3f} / Y {pos.get('y',0):.3f} / Z {pos.get('z',0):.3f}"
            )
            self.mp_detail_labels["distance"].setText(f"{player.get('distanceToLocal',0):.2f} m")
            self.mp_detail_labels["hp"].setText(
                f"{player.get('hp',0):.1f} / {player.get('hpMax',100):.1f}"
            )
            self.mp_detail_labels["stamina"].setText(
                f"{player.get('staminaCurrent',0):.2f} / {player.get('staminaMax',0):.2f}"
            )
            self.mp_detail_labels["extraStamina"].setText(f"{player.get('extraStamina',0):.2f}")
            self.mp_detail_labels["state"].setText(" / ".join(states))

            status_map = player.get("statusMap") or {}
            # Use game order when known, then append any newly added statuses.
            order = []
            for raw, _zh in getattr(B, "STATUS_ORDER", []):
                canonical = _canonical_status_name(raw)
                if canonical not in order:
                    order.append(canonical)
            for key in status_map:
                if key not in order:
                    order.append(key)

            self.mp_status_table.setRowCount(0)
            for key in order:
                if key not in status_map:
                    continue
                value = float(status_map.get(key, 0.0) or 0.0)
                display = getattr(B, "STATUS_TRANSLATIONS", {}).get(key, key)
                row = self.mp_status_table.rowCount()
                self.mp_status_table.insertRow(row)
                self.mp_status_table.setItem(row, 0, UI.QTableWidgetItem(display))
                self.mp_status_table.setItem(row, 1, UI.QTableWidgetItem(f"{value:.1f}%"))

            binding = B.get_player_bindings().get(player["playerId"])
            self._mp_syncing = True
            if binding:
                online = bool(binding.get("online", False))
                state_text = _str(binding.get("state") or ("在线" if online else "离线"))
                output_text = "开" if binding.get("enabled") else "关"
                if binding.get("enabled") and not online:
                    output_text += "（暂停，等待原设备恢复）"
                self.mp_binding_status.setText(
                    f"已绑定：{binding.get('device_name') or '郊狼设备'} | "
                    f"APP {binding.get('client_id')} / Slot {binding.get('slot_id')} | "
                    f"{state_text} | 远程伤害输出={output_text}"
                )
                self.mp_player_output.setChecked(bool(binding.get("enabled", False)))
            else:
                self.mp_binding_status.setText("未绑定")
                self.mp_player_output.setChecked(False)
            self._mp_syncing = False

            # Local player continues using the original default-device rule path.
            self.mp_player_output.setEnabled(not local and bool(binding))
            self.mp_bind_button.setEnabled(not local and self.mp_bind_combo.count() > 0)
            self.mp_unbind_button.setEnabled(not local and bool(binding))

        def _refresh_multiplayer_page(self):
            if not hasattr(self, "mp_player_list"):
                return
            player_snapshot = B.get_multiplayer_snapshot()
            device_snapshot = B.get_multidevice_snapshot()
            self._refresh_qr(device_snapshot)
            self._refresh_devices(device_snapshot)
            self._refresh_player_list(player_snapshot)
            self._refresh_selected_player()

    UI.Window = MultiplayerWindow
