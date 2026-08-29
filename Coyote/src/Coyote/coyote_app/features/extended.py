"""Extended Coyote rules for PEAK ↔ DG-LAB integration.

Thin runtime extension. It keeps backend.py/ui_qt.py untouched while avoiding
per-telemetry deep copies and unnecessary work for disabled extension rules.

Features:
- HP damage ramp: 0 -> resolved target intensity (keeps random intensity,
  percentage tiers and spike bonuses).
- Per-trigger random waveform with a user-selected waveform pool.
- Consumed-item trigger.
- HP / stamina / affliction recovery triggers.
- User-defined world areas (scene + XYZ + radius): enter and dwell rules.
- All new rules default OFF and participate in existing batch controls.
- v3: disabled extension rules are skipped early, area math is computed once per
  packet, and ramp/area fields are kept only on rules that actually use them.
"""

import json
import math
import random
import threading
import time
import backend as B


# ---------------------------------------------------------------------------
# Constants / runtime state
# ---------------------------------------------------------------------------

EXTENSION_VERSION = 3
_EXTENSION_INSTALLED = False
_UI_INSTALLED = False

_RAMP_LOCK = threading.Lock()
_RAMP_GENERATION = 0
_RAMP_CONTEXT = threading.local()
_GAME_CONTEXT = threading.local()

_AUTO_GUARD_LOCK = threading.Lock()
_LAST_AUTO_OUTPUT_AT = 0.0
_RESPAWN_GUARD_UNTIL = 0.0

EXTENSION_SETTINGS_DEFAULTS = {
    # Short cross-rule anti-spam window; keeps distinct Coyote rules responsive.
    "global_guard_enabled": True,
    "global_guard_ms": 250,
    # HP is derived from Injury in the current telemetry; do not punish the same hit twice.
    "dedupe_hp_injury": True,
    # Ignore state jumps while a character/scene is being rebuilt or respawned.
    "respawn_guard_enabled": True,
    "respawn_guard_seconds": 2.5,
}

extension_settings = dict(EXTENSION_SETTINGS_DEFAULTS)

_AREA_LOCK = threading.Lock()
_AREA_RUNTIME = {
    "areaEnter": {},
    "areaDwell": {},
}

_RECOVERY_LOCK = threading.Lock()
_RECOVERY_RUNTIME = {
    "hpRecover": {"active": False, "start": None, "fired": False},
    "staminaRecover": {"active": False, "start": None, "fired": False},
}


NEW_RULE_META = [
    ("consumedItem", "食用物品", None, "检测到一次新的食用/消耗事件时触发"),
    ("hpRecover", "血量恢复", None, "连续回血达到设定幅度时触发"),
    ("staminaRecover", "体力恢复", None, "连续体力恢复达到设定幅度时触发"),
    ("statusRecover", "状态恢复", None, "任一异常状态数值下降达到设定幅度时触发"),
    ("areaEnter", "进入区域", None, "从区域外进入用户定义区域时触发"),
    ("areaDwell", "区域停留", None, "在用户定义区域连续停留达到设定时间后触发"),
]

NEW_RULE_GROUPS = [
    (
        "recovery_items",
        "食用 / 恢复",
        ["consumedItem", "hpRecover", "staminaRecover", "statusRecover"],
    ),
    (
        "areas",
        "区域",
        ["areaEnter", "areaDwell"],
    ),
]


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def _clamp_float(value, low, high, default):
    try:
        return max(float(low), min(float(high), float(value)))
    except Exception:
        return float(default)


def _clamp_int(value, low, high, default):
    try:
        return max(int(low), min(int(high), int(float(value))))
    except Exception:
        return int(default)




def _normalize_extension_settings(raw=None):
    raw = raw if isinstance(raw, dict) else {}
    extension_settings["global_guard_enabled"] = bool(
        raw.get("global_guard_enabled", extension_settings.get("global_guard_enabled", True))
    )
    extension_settings["global_guard_ms"] = _clamp_int(
        raw.get("global_guard_ms", extension_settings.get("global_guard_ms", 250)),
        0, 5000, 250,
    )
    extension_settings["dedupe_hp_injury"] = bool(
        raw.get("dedupe_hp_injury", extension_settings.get("dedupe_hp_injury", True))
    )
    extension_settings["respawn_guard_enabled"] = bool(
        raw.get("respawn_guard_enabled", extension_settings.get("respawn_guard_enabled", True))
    )
    extension_settings["respawn_guard_seconds"] = _clamp_float(
        raw.get(
            "respawn_guard_seconds",
            extension_settings.get("respawn_guard_seconds", 2.5),
        ),
        0.5, 15.0, 2.5,
    )
    return dict(extension_settings)


def _packet_transition_reason(current, previous):
    if not isinstance(current, dict) or not isinstance(previous, dict):
        return ""

    # Future-proof: if a newer DLL explicitly marks a non-local packet, never use it.
    if current.get("localPlayer") is False:
        return "检测到非本地玩家遥测"

    if previous.get("hasCharacter", True) is False and current.get("hasCharacter", True) is not False:
        return "角色对象刚创建"

    # Same-scene respawn commonly changes dead/passedOut -> normal and restores HP at once.
    was_incapacitated = bool(previous.get("dead", False) or previous.get("passedOut", False))
    now_incapacitated = bool(current.get("dead", False) or current.get("passedOut", False))
    if was_incapacitated and not now_incapacitated:
        return "角色刚从死亡/昏迷状态恢复"

    old_scene = str(previous.get("scene", "") or "").strip()
    new_scene = str(current.get("scene", "") or "").strip()
    if old_scene and new_scene and old_scene != new_scene:
        return f"场景切换 {old_scene} → {new_scene}"

    # If a future DLL supplies a stable local character identity, use it automatically.
    old_id = previous.get("characterInstanceId")
    new_id = current.get("characterInstanceId")
    if old_id not in (None, "") and new_id not in (None, "") and old_id != new_id:
        return "本地 Character 实例已更换"

    old_gen = previous.get("characterGeneration")
    new_gen = current.get("characterGeneration")
    if old_gen not in (None, "") and new_gen not in (None, "") and old_gen != new_gen:
        return "本地 Character 世代已更换"

    # Out-of-order/duplicate packet protection when packetSeq is available.
    old_seq = previous.get("packetSeq")
    new_seq = current.get("packetSeq")
    try:
        if old_seq is not None and new_seq is not None and int(new_seq) <= int(old_seq):
            return "收到重复或乱序遥测包"
    except Exception:
        pass

    return ""


def _start_respawn_guard(reason):
    global _RESPAWN_GUARD_UNTIL
    _reset_extension_runtime()
    try:
        B.last_trigger_time.clear()
    except Exception:
        pass
    if not bool(extension_settings.get("respawn_guard_enabled", True)):
        _RESPAWN_GUARD_UNTIL = 0.0
        return
    seconds = _clamp_float(
        extension_settings.get("respawn_guard_seconds", 2.5),
        0.5, 15.0, 2.5,
    )
    _RESPAWN_GUARD_UNTIL = time.monotonic() + seconds
    B.add_log("系统", "角色同步保护", f"{reason}；{seconds:.1f}s 内不触发自动电击")


def _auto_guard_remaining():
    if not bool(extension_settings.get("global_guard_enabled", True)):
        return 0.0
    window = float(extension_settings.get("global_guard_ms", 250) or 0) / 1000.0
    if window <= 0:
        return 0.0
    with _AUTO_GUARD_LOCK:
        elapsed = time.monotonic() - _LAST_AUTO_OUTPUT_AT
    return max(0.0, window - elapsed)


def _mark_auto_output(_rule_key=None):
    global _LAST_AUTO_OUTPUT_AT
    with _AUTO_GUARD_LOCK:
        _LAST_AUTO_OUTPUT_AT = time.monotonic()


def _throttled_log(key, event, detail, interval=0.8):
    fn = getattr(B, "throttled_game_log", None)
    if callable(fn):
        try:
            return fn(key, event, detail, interval=interval)
        except Exception:
            pass
    try:
        B.add_log("系统", event, detail)
    except Exception:
        pass


RAMP_FIELDS = ("ramp_enabled", "ramp_duration_ms", "ramp_steps")
AREA_FIELDS = ("area_zones", "area_dwell_seconds")
AREA_RULE_KEYS = frozenset(("areaEnter", "areaDwell"))


def normalize_waveform_pool(value):
    if not isinstance(value, (list, tuple)):
        return []
    available = set(B.waveform_names())
    seen = set()
    result = []
    for raw in value:
        name = str(raw or "").strip()
        if name in available and name not in seen:
            seen.add(name)
            result.append(name)
    return result


def normalize_area_zones(value):
    """Return a bounded, JSON-safe list of spherical world regions."""
    if not isinstance(value, list):
        return []

    result = []
    for index, raw in enumerate(value[:64], start=1):
        if not isinstance(raw, dict):
            continue

        name = str(raw.get("name", "") or "").strip()[:80] or f"区域 {index}"
        scene = str(raw.get("scene", "") or "").strip()[:120]
        try:
            x = float(raw.get("x", 0.0))
            y = float(raw.get("y", 0.0))
            z = float(raw.get("z", 0.0))
        except Exception:
            continue
        if not all(math.isfinite(v) for v in (x, y, z)):
            continue

        radius = _clamp_float(raw.get("radius", 5.0), 0.25, 5000.0, 5.0)
        result.append({
            "name": name,
            "scene": scene,
            "x": round(x, 4),
            "y": round(y, 4),
            "z": round(z, 4),
            "radius": round(radius, 3),
        })
    return result


def _normalize_extension_rule_fields(key, cfg):
    """Normalize only fields used by this rule and prune stale v2 keys."""
    cfg["random_waveform"] = bool(cfg.get("random_waveform", False))
    cfg["random_waveforms"] = normalize_waveform_pool(cfg.get("random_waveforms", []))

    if key == "hp":
        cfg["ramp_enabled"] = bool(cfg.get("ramp_enabled", False))
        cfg["ramp_duration_ms"] = _clamp_int(cfg.get("ramp_duration_ms", 1500), 100, 60000, 1500)
        cfg["ramp_steps"] = _clamp_int(cfg.get("ramp_steps", 10), 2, 100, 10)
    else:
        for field in RAMP_FIELDS:
            cfg.pop(field, None)

    if key in AREA_RULE_KEYS:
        cfg["area_zones"] = normalize_area_zones(cfg.get("area_zones", []))
        cfg["area_dwell_seconds"] = _clamp_float(cfg.get("area_dwell_seconds", 30.0), 0.5, 86400.0, 30.0)
    else:
        for field in AREA_FIELDS:
            cfg.pop(field, None)
    return cfg


def _rule_fields(key, *fields):
    """Cheap hot-path snapshot; avoids JSON deep-copying entire rules."""
    with B.rule_lock:
        cfg = B.rules.get(key)
        if not isinstance(cfg, dict):
            return {}
        return {field: cfg.get(field) for field in fields}


def _rebuild_rule_maps():
    B.RULE_GROUP_BY_KEY = {
        key: group_id
        for group_id, _, keys in B.RULE_GROUPS
        for key in keys
    }
    B.RULE_META_BY_KEY = {
        key: {"display": display, "index": index, "trigger": trigger}
        for key, display, index, trigger in B.RULE_META
    }


# ---------------------------------------------------------------------------
# Backend installation
# ---------------------------------------------------------------------------

def install_backend():
    global _EXTENSION_INSTALLED
    if _EXTENSION_INSTALLED:
        return
    _EXTENSION_INSTALLED = True
    base_rule_meta = tuple(B.RULE_META)

    # 1) Keep only fields that can affect each rule.
    original_default_rule = B.default_rule

    def extended_default_rule():
        cfg = original_default_rule()
        cfg.setdefault("random_waveform", False)
        cfg.setdefault("random_waveforms", [])
        return cfg

    B.default_rule = extended_default_rule

    with B.rule_lock:
        for key, cfg in B.rules.items():
            _normalize_extension_rule_fields(key, cfg)

        existing = {key for key, _, _, _ in B.RULE_META}
        for key, display, index, trigger in NEW_RULE_META:
            if key not in existing:
                B.RULE_META.append((key, display, index, trigger))
                existing.add(key)
            if key not in B.rules:
                B.rules[key] = extended_default_rule()
                B.rules[key]["enabled"] = False
            _normalize_extension_rule_fields(key, B.rules[key])

        existing_groups = {gid for gid, _, _ in B.RULE_GROUPS}
        for group in NEW_RULE_GROUPS:
            if group[0] not in existing_groups:
                B.RULE_GROUPS.append(group)

    _rebuild_rule_maps()

    # HP/stamina recovery are numeric percentages and may reuse the existing
    # threshold-tier editor / intensity calculation if the user wants it.
    original_percentage_support = B.rule_supports_percentage_tiers

    def extended_percentage_support(key):
        if key in ("hpRecover", "staminaRecover"):
            return True
        return original_percentage_support(key)

    B.rule_supports_percentage_tiers = extended_percentage_support


    # The stock continuous scheduler deep-copies every rule on every telemetry
    # packet before it even checks whether that rule is enabled. Keep its
    # behavior, but reject disabled/non-continuous rules with a tiny locked read
    # first. Extension-only rules are scheduled by their own handlers below.
    original_handle_continuous_rules = getattr(B, "handle_continuous_rules", None)
    if (
        callable(original_handle_continuous_rules)
        and callable(getattr(B, "continuous_rule_condition", None))
        and callable(getattr(B, "rule_has_continuous_duration", None))
    ):
        def optimized_handle_continuous_rules(current):
            if B.peak_is_incapacitated(current):
                return
            if time.time() < float(getattr(B, "single_output_guard_until", 0.0) or 0.0):
                return

            for key, display, _, _ in base_rule_meta:
                with B.rule_lock:
                    raw = B.rules.get(key, {})
                    if not raw.get("enabled", False):
                        continue
                    repeat = str(raw.get("trigger_mode", "single") or "single").lower() == "repeat"
                    duration_hold = B.rule_has_continuous_duration(raw)
                if not repeat and not duration_hold:
                    continue

                active, detail, value_pct = B.continuous_rule_condition(key, current)
                if not active:
                    continue
                if B.send_rule_output(
                    key,
                    display,
                    detail,
                    current_value_pct=value_pct,
                    continuous=True,
                ):
                    return

        B.handle_continuous_rules = optimized_handle_continuous_rules

    # 2) Load extra fields that legacy backend.load_config does not know.
    original_load_config = B.load_config

    def extended_load_config():
        original_load_config()
        data = {}
        if B.CONFIG_FILE.exists():
            try:
                data = json.loads(B.CONFIG_FILE.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        _normalize_extension_settings(data.get("extended_features", {}))
        loaded_rules = data.get("rules") if isinstance(data, dict) else None

        with B.rule_lock:
            for key, cfg in B.rules.items():
                incoming = loaded_rules.get(key) if isinstance(loaded_rules, dict) else None
                if isinstance(incoming, dict):
                    cfg["random_waveform"] = incoming.get("random_waveform", cfg.get("random_waveform", False))
                    cfg["random_waveforms"] = incoming.get("random_waveforms", cfg.get("random_waveforms", []))
                    if key == "hp":
                        cfg["ramp_enabled"] = incoming.get("ramp_enabled", cfg.get("ramp_enabled", False))
                        cfg["ramp_duration_ms"] = incoming.get("ramp_duration_ms", cfg.get("ramp_duration_ms", 1500))
                        cfg["ramp_steps"] = incoming.get("ramp_steps", cfg.get("ramp_steps", 10))
                    if key in AREA_RULE_KEYS:
                        cfg["area_zones"] = incoming.get("area_zones", cfg.get("area_zones", []))
                        cfg["area_dwell_seconds"] = incoming.get("area_dwell_seconds", cfg.get("area_dwell_seconds", 30.0))
                _normalize_extension_rule_fields(key, cfg)

    B.load_config = extended_load_config

    # 3) Random waveform. We hook get_rule_copy because send_rule_output already
    # centralizes every built-in rule. This preserves all current safety checks,
    # intensity randomization, spike tiers, durations and max-intensity caps.
    original_get_rule_copy = B.get_rule_copy

    def extended_get_rule_copy(key):
        cfg = original_get_rule_copy(key)
        if not cfg.get("random_waveform", False):
            return cfg

        available = getattr(B, "COYOTE_WAVEFORMS", None)
        if isinstance(available, dict):
            pool = [name for name in (cfg.get("random_waveforms") or ()) if name in available]
            if not pool:
                pool = list(available.keys())
        else:
            pool = normalize_waveform_pool(cfg.get("random_waveforms", [])) or list(B.waveform_names())
        if not pool:
            return cfg

        cfg["waveform_a"] = random.choice(pool)
        cfg["waveform_b"] = random.choice(pool)
        for tier in cfg.get("thresholds", ()) or ():
            if isinstance(tier, dict):
                tier["waveform_a"] = B.TIER_WAVEFORM_INHERIT
                tier["waveform_b"] = B.TIER_WAVEFORM_INHERIT
        return cfg

    B.get_rule_copy = extended_get_rule_copy

    # 4) HP ramp. Keep original send_rule_output intact and intercept only the
    # final absolute-intensity RPC generated by that function. This means the
    # ramp target is exactly the already-resolved result after random intensity,
    # status tier and spike bonus calculations.
    original_send_rpc = B.send_rpc

    def ramp_send_rpc(method, data=None):
        ctx = getattr(_RAMP_CONTEXT, "value", None)
        if (
            ctx
            and method == "device.op"
            and isinstance(data, dict)
            and int(data.get("t", -1)) == 4
        ):
            try:
                channel = 0 if int(data.get("c", 0)) == 0 else 1
                target = B.clamp_int(data.get("v", 0))
                duration_ms = max(100, int(data.get("d", 1000)))
            except Exception:
                return original_send_rpc(method, data)

            if target <= 0:
                return original_send_rpc(method, data)

            ramp_ms = min(
                duration_ms,
                _clamp_int(ctx.get("ramp_duration_ms", 1500), 100, 60000, 1500),
            )
            steps = _clamp_int(ctx.get("ramp_steps", 10), 2, 100, 10)
            generation = int(ctx.get("generation", 0))
            slot_id = data.get("s")

            threading.Thread(
                target=_ramp_worker,
                args=(
                    original_send_rpc,
                    generation,
                    slot_id,
                    channel,
                    target,
                    duration_ms,
                    ramp_ms,
                    steps,
                ),
                name=f"CoyoteDamageRamp-{channel}",
                daemon=True,
            ).start()
            return True, f"渐升输出 0→{target} 已启动"

        return original_send_rpc(method, data)

    B.send_rpc = ramp_send_rpc

    original_send_rule_output = B.send_rule_output

    def extended_send_rule_output(
        rule_key,
        event_name,
        change_detail,
        current_value_pct=None,
        continuous=False,
        change_delta_pct=None,
    ):
        global _RAMP_GENERATION

        # Transition/respawn synchronization guard. Manual control is untouched;
        # this applies only to automatic rule output.
        if bool(extension_settings.get("respawn_guard_enabled", True)):
            remaining = _RESPAWN_GUARD_UNTIL - time.monotonic()
            if remaining > 0:
                _throttled_log(
                    f"guard:respawn:{rule_key}",
                    f"{event_name} 已抑制",
                    f"角色同步保护剩余 {remaining:.2f}s",
                    interval=0.8,
                )
                return False

        ctx = getattr(_GAME_CONTEXT, "value", None)

        # HP in this project is derived from Injury. If the hp rule already
        # produced the output for this telemetry packet, do not also fire Injury.
        if (
            rule_key == "Injury"
            and bool(extension_settings.get("dedupe_hp_injury", True))
            and isinstance(ctx, dict)
            and bool(ctx.get("hp_sent", False))
        ):
            _throttled_log(
                "guard:hp-injury",
                "重复伤害已合并",
                "同一次伤害已由“血量下降”规则输出，跳过重复 Injury 输出",
                interval=0.5,
            )
            return False

        # Short cross-rule anti-spam window. It is intentionally much shorter
        # than normal rule cooldowns so separate events remain expressive.
        if not continuous:
            remaining = _auto_guard_remaining()
            if remaining > 0:
                _throttled_log(
                    f"guard:global:{rule_key}",
                    f"{event_name} 已抑制",
                    f"全局防连击窗口剩余 {remaining * 1000:.0f}ms",
                    interval=0.5,
                )
                return False

        ramp_cfg = (
            _rule_fields("hp", "ramp_enabled", "ramp_duration_ms", "ramp_steps")
            if rule_key == "hp" and not continuous
            else {}
        )
        ramp = bool(ramp_cfg.get("ramp_enabled", False))

        if ramp:
            with _RAMP_LOCK:
                _RAMP_GENERATION += 1
                generation = _RAMP_GENERATION

            _RAMP_CONTEXT.value = {
                "generation": generation,
                "ramp_duration_ms": ramp_cfg.get("ramp_duration_ms", 1500),
                "ramp_steps": ramp_cfg.get("ramp_steps", 10),
            }
            B.add_log(
                "输出",
                "受伤渐升",
                (
                    f"已启用 0→目标强度渐升；"
                    f"渐升={_clamp_int(ramp_cfg.get('ramp_duration_ms', 1500), 100, 60000, 1500)}ms / "
                    f"步数={_clamp_int(ramp_cfg.get('ramp_steps', 10), 2, 100, 10)}"
                ),
            )

        try:
            sent = original_send_rule_output(
                rule_key,
                event_name,
                change_detail,
                current_value_pct=current_value_pct,
                continuous=continuous,
                change_delta_pct=change_delta_pct,
            )
        finally:
            if ramp:
                _RAMP_CONTEXT.value = None

        if sent:
            if (
                not continuous
                and bool(extension_settings.get("global_guard_enabled", True))
            ):
                _mark_auto_output(rule_key)
            if rule_key == "hp" and isinstance(ctx, dict):
                ctx["hp_sent"] = True
        return sent

    B.send_rule_output = extended_send_rule_output

    original_clear_output = B.clear_device_output

    def extended_clear_device_output(reason="手动停止"):
        global _RAMP_GENERATION
        with _RAMP_LOCK:
            _RAMP_GENERATION += 1
        return original_clear_output(reason)

    B.clear_device_output = extended_clear_device_output

    # 5) Add consume / recovery / area event detection after the original game
    # rule handler. Existing logic remains untouched.
    original_handle_game_rules = B.handle_game_rules

    def extended_handle_game_rules(current, previous):
        if not isinstance(current, dict) or not isinstance(previous, dict):
            return original_handle_game_rules(current, previous)

        if current.get("localPlayer") is False:
            _throttled_log(
                "guard:nonlocal",
                "非本地玩家遥测已忽略",
                "只允许 Character.localCharacter 驱动自动规则",
                interval=2.0,
            )
            return

        transition_reason = _packet_transition_reason(current, previous)
        if transition_reason:
            if transition_reason == "收到重复或乱序遥测包":
                _throttled_log(
                    "guard:packet-order",
                    "重复/乱序遥测已忽略",
                    transition_reason,
                    interval=1.0,
                )
                return
            _start_respawn_guard(transition_reason)
            return

        if current.get("hasCharacter", True) is False:
            _reset_extension_runtime()
            return original_handle_game_rules(current, previous)
        if previous.get("hasCharacter", True) is False:
            _start_respawn_guard("角色对象刚创建")
            return

        if (
            bool(extension_settings.get("respawn_guard_enabled", True))
            and time.monotonic() < _RESPAWN_GUARD_UNTIL
        ):
            return

        _GAME_CONTEXT.value = {"hp_sent": False}
        try:
            original_handle_game_rules(current, previous)

            if B.peak_is_incapacitated(current):
                _reset_extension_runtime()
                return

            _handle_consumed_item(current, previous)
            _handle_recovery_rules(current, previous)
            _handle_area_rules(current)
        except Exception as exc:
            B.add_log("错误", "扩展规则处理失败", repr(exc))
        finally:
            _GAME_CONTEXT.value = None

    B.handle_game_rules = extended_handle_game_rules

    # Expose helpers for the UI and custom integrations.
    B.normalize_area_zones = normalize_area_zones
    B.normalize_random_waveforms = normalize_waveform_pool
    B.coyote_extension_settings = extension_settings
    B.COYOTE_EXTENDED_FEATURES_VERSION = EXTENSION_VERSION


# ---------------------------------------------------------------------------
# Damage ramp worker
# ---------------------------------------------------------------------------

def _ramp_worker(
    original_send_rpc,
    generation,
    slot_id,
    channel,
    target,
    duration_ms,
    ramp_ms,
    steps,
):
    if not slot_id:
        return

    # Start at zero explicitly, then climb in evenly spaced absolute levels.
    original_send_rpc(
        "device.op",
        {
            "s": slot_id,
            "c": channel,
            "t": 4,
            "v": 0,
            "d": duration_ms,
            "im": True,
        },
    )

    start = time.monotonic()
    step_interval = ramp_ms / max(1, steps) / 1000.0
    last_level = -1

    for step in range(1, steps + 1):
        deadline = start + step * step_interval
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.03, remaining))

        with _RAMP_LOCK:
            if generation != _RAMP_GENERATION:
                return

        if not bool(getattr(B, "master_output_enabled", False)):
            return
        if B.get_slot_id() != slot_id:
            return
        try:
            if B.peak_is_incapacitated():
                return
        except Exception:
            return

        level = int(round(target * step / steps))
        level = max(0, min(target, level))
        if level == last_level and step != steps:
            continue
        last_level = level

        elapsed_ms = int((time.monotonic() - start) * 1000)
        remaining_duration = max(100, duration_ms - elapsed_ms)

        original_send_rpc(
            "device.op",
            {
                "s": slot_id,
                "c": channel,
                "t": 4,
                "v": level,
                "d": remaining_duration,
                "im": True,
            },
        )


# ---------------------------------------------------------------------------
# Consume / recovery rules
# ---------------------------------------------------------------------------

def _event_id(packet, field):
    value = packet.get(field) if isinstance(packet, dict) else None
    if not isinstance(value, dict):
        return ""
    return str(value.get("id", "") or "").strip()


def _handle_consumed_item(current, previous):
    new_id = _event_id(current, "lastConsumedItem")
    if not new_id or new_id == _event_id(previous, "lastConsumedItem"):
        return

    cfg = _rule_fields("consumedItem", "enabled", "item_filter")
    if not cfg.get("enabled", False):
        return

    event = current.get("lastConsumedItem") or {}
    item_name = str(event.get("item", "") or "").strip()
    if item_name and not B.item_rule_matches(cfg, item_name):
        return

    detail = str(event.get("detail", "") or "").strip()
    suffix = "（推断）" if bool(event.get("inferred", False)) else ""
    B.send_rule_output(
        "consumedItem",
        "食用物品",
        f"{item_name or '未知物品'}{suffix}" + (f" | {detail}" if detail else ""),
        current_value_pct=None,
    )


def _numeric(packet, key, default=0.0):
    try:
        return float(packet.get(key, default))
    except Exception:
        return float(default)


def _stamina_pct(packet):
    try:
        return float(B.stamina_percent(packet))
    except Exception:
        current = _numeric(packet, "staminaCurrent", 0.0)
        maximum = max(0.0001, _numeric(packet, "staminaMax", 1.0))
        return max(0.0, min(100.0, current / maximum * 100.0))


def _recovery_trigger_delta(cfg, default=1.0):
    return _clamp_float(cfg.get("trigger_delta", default), 0.1, 100.0, default)


def _reset_recovery_state(rule_key):
    with _RECOVERY_LOCK:
        state = _RECOVERY_RUNTIME.get(rule_key)
        if state is not None:
            state.update({"active": False, "start": None, "fired": False})


def _handle_contiguous_recovery(rule_key, display, old_value, new_value, cfg=None):
    cfg = cfg or _rule_fields(rule_key, "enabled", "trigger_delta", "trigger_mode")
    if not cfg.get("enabled", False):
        _reset_recovery_state(rule_key)
        return
    if new_value <= old_value + 1e-4:
        _reset_recovery_state(rule_key)
        return

    threshold = _recovery_trigger_delta(cfg)
    repeat = str(cfg.get("trigger_mode", "single") or "single").lower() == "repeat"

    with _RECOVERY_LOCK:
        state = _RECOVERY_RUNTIME[rule_key]
        if not state["active"] or state["start"] is None:
            state.update({"active": True, "start": old_value, "fired": False})
        start_value = float(state["start"])
        cumulative = max(0.0, new_value - start_value)
        if cumulative + 1e-6 < threshold or (not repeat and state["fired"]):
            return

    sent = B.send_rule_output(
        rule_key,
        display,
        f"{start_value:.1f}% → {new_value:.1f}%（累计恢复 {cumulative:.1f}%）",
        current_value_pct=new_value,
        change_delta_pct=cumulative,
    )
    if sent and not repeat:
        with _RECOVERY_LOCK:
            _RECOVERY_RUNTIME[rule_key]["fired"] = True


def _handle_recovery_rules(current, previous):
    hp_cfg = _rule_fields("hpRecover", "enabled", "trigger_delta", "trigger_mode")
    if hp_cfg.get("enabled", False):
        _handle_contiguous_recovery(
            "hpRecover", "血量恢复",
            _numeric(previous, "hp", 100.0),
            _numeric(current, "hp", 100.0),
            hp_cfg,
        )
    else:
        _reset_recovery_state("hpRecover")

    st_cfg = _rule_fields("staminaRecover", "enabled", "trigger_delta", "trigger_mode")
    if st_cfg.get("enabled", False):
        _handle_contiguous_recovery(
            "staminaRecover", "体力恢复",
            _stamina_pct(previous),
            _stamina_pct(current),
            st_cfg,
        )
    else:
        _reset_recovery_state("staminaRecover")

    status_cfg = _rule_fields("statusRecover", "enabled", "trigger_delta")
    if not status_cfg.get("enabled", False):
        return
    threshold = _recovery_trigger_delta(status_cfg)

    names = current.get("statusNames") or ()
    old_values = previous.get("statuses") or ()
    new_values = current.get("statuses") or ()
    if not isinstance(old_values, list) or not isinstance(new_values, list):
        return

    best = None
    for index, (old_raw, new_raw) in enumerate(zip(old_values, new_values)):
        try:
            old = float(old_raw) * 100.0
            new = float(new_raw) * 100.0
        except Exception:
            continue
        delta = old - new
        if delta + 1e-6 < threshold:
            continue

        raw_name = str(names[index]) if isinstance(names, list) and index < len(names) else f"状态{index}"
        display_name = B.STATUS_TRANSLATIONS.get(raw_name, raw_name)
        if best is None or delta > best[0]:
            best = (delta, display_name, old, new)

    if best is not None:
        delta, display_name, old, new = best
        B.send_rule_output(
            "statusRecover",
            "状态恢复",
            f"{display_name}: {old:.1f}% → {new:.1f}%（恢复 {delta:.1f}%）",
            current_value_pct=None,
            change_delta_pct=delta,
        )


# ---------------------------------------------------------------------------
# Area rules
# ---------------------------------------------------------------------------

def _packet_position(packet):
    position = packet.get("position") if isinstance(packet, dict) else None
    if not isinstance(position, dict):
        return None
    try:
        xyz = (
            float(position.get("x", 0.0)),
            float(position.get("y", 0.0)),
            float(position.get("z", 0.0)),
        )
    except Exception:
        return None
    return xyz if all(math.isfinite(v) for v in xyz) else None


def _zone_state_key(zone, index):
    return (
        index,
        zone.get("name", ""),
        zone.get("scene", ""),
        zone.get("x", 0.0),
        zone.get("y", 0.0),
        zone.get("z", 0.0),
        zone.get("radius", 5.0),
    )


def _handle_area_rules(current):
    pos = _packet_position(current)
    if pos is None:
        return

    scene = str(current.get("scene", "") or "")
    scene_lower = scene.lower()
    now = time.monotonic()

    for rule_key, display in (("areaEnter", "进入区域"), ("areaDwell", "区域停留")):
        cfg = _rule_fields(
            rule_key,
            "enabled", "area_zones", "area_dwell_seconds", "trigger_mode",
        )
        if not cfg.get("enabled", False):
            with _AREA_LOCK:
                _AREA_RUNTIME[rule_key].clear()
            continue

        zones = cfg.get("area_zones") or ()
        if not zones:
            continue

        dwell_seconds = _clamp_float(
            cfg.get("area_dwell_seconds", 30.0), 0.5, 86400.0, 30.0
        )
        repeat = str(cfg.get("trigger_mode", "single") or "single").lower() == "repeat"
        active_ids = set()
        pending = []

        with _AREA_LOCK:
            runtime = _AREA_RUNTIME[rule_key]
            for index, zone in enumerate(zones):
                if not isinstance(zone, dict):
                    continue
                zid = _zone_state_key(zone, index)
                active_ids.add(zid)

                scene_filter = str(zone.get("scene", "") or "").strip().lower()
                distance_sq = None
                if scene_filter and scene_filter not in scene_lower:
                    inside = False
                else:
                    dx = pos[0] - float(zone.get("x", 0.0))
                    dy = pos[1] - float(zone.get("y", 0.0))
                    dz = pos[2] - float(zone.get("z", 0.0))
                    distance_sq = dx * dx + dy * dy + dz * dz
                    radius = float(zone.get("radius", 5.0))
                    inside = distance_sq <= radius * radius

                state = runtime.setdefault(
                    zid, {"inside": False, "entered_at": None, "fired": False}
                )
                was_inside = bool(state["inside"])
                if not inside:
                    state.update({"inside": False, "entered_at": None, "fired": False})
                    continue
                if not was_inside:
                    state.update({"inside": True, "entered_at": now, "fired": False})

                if rule_key == "areaEnter":
                    if not was_inside:
                        pending.append((zid, zone, math.sqrt(distance_sq or 0.0), None))
                    continue

                entered_at = state["entered_at"] or now
                elapsed = max(0.0, now - float(entered_at))
                if elapsed + 1e-6 >= dwell_seconds and (repeat or not state["fired"]):
                    pending.append((zid, zone, None, elapsed))

            for stale in tuple(runtime):
                if stale not in active_ids:
                    runtime.pop(stale, None)

        # Never hold runtime locks while network/device calls execute.
        for zid, zone, distance, elapsed in pending:
            if rule_key == "areaEnter":
                B.send_rule_output(
                    rule_key,
                    display,
                    f"{zone['name']} | scene={scene} | 距离中心 {distance:.2f}m / 半径 {float(zone.get('radius', 5.0)):.2f}m",
                    current_value_pct=None,
                )
                continue

            sent = B.send_rule_output(
                rule_key,
                display,
                f"{zone['name']} 已连续停留 {elapsed:.1f}s（阈值 {dwell_seconds:.1f}s）",
                current_value_pct=None,
            )
            if sent and not repeat:
                with _AREA_LOCK:
                    state = _AREA_RUNTIME[rule_key].get(zid)
                    if state is not None:
                        state["fired"] = True

def _reset_extension_runtime():
    with _AREA_LOCK:
        for value in _AREA_RUNTIME.values():
            value.clear()
    with _RECOVERY_LOCK:
        for state in _RECOVERY_RUNTIME.values():
            state.update({"active": False, "start": None, "fired": False})


# ---------------------------------------------------------------------------
# Qt UI extension
# ---------------------------------------------------------------------------

def install_ui(UI):
    """Patch ui_qt.RuleEditor before ui_qt.main() constructs the window."""
    global _UI_INSTALLED
    if _UI_INSTALLED:
        return
    _UI_INSTALLED = True

    BaseRuleEditor = UI.RuleEditor

    class ExtendedRuleEditor(BaseRuleEditor):
        def __init__(self, key, display, trigger):
            super().__init__(key, display, trigger)

            self.random_waveform_enabled = UI.QCheckBox("每次电击随机波形")
            self.random_waveform_list = UI.QListWidget()
            self.random_waveform_list.setMaximumHeight(150)
            self._refresh_random_waveform_list([])

            random_wave_box = UI.QGroupBox("随机波形")
            rw_layout = UI.QVBoxLayout(random_wave_box)
            rw_layout.addWidget(self.random_waveform_enabled)
            rw_note = UI.QLabel(
                "默认关闭。开启后，每次规则真正输出时都会从下方勾选的波形中随机选择；"
                "A/B 通道独立抽取。若一个也未勾选，则从当前全部可用波形中随机。"
            )
            rw_note.setObjectName("muted")
            rw_note.setWordWrap(True)
            rw_layout.addWidget(rw_note)
            rw_layout.addWidget(self.random_waveform_list)
            self._insert_extension_widget(random_wave_box)

            self.ramp_enabled = None
            self.ramp_duration_ms = None
            self.ramp_steps = None
            if key == "hp":
                ramp_box = UI.QGroupBox("受伤后强度渐升")
                form = UI.QFormLayout(ramp_box)
                self.ramp_enabled = UI.QCheckBox("从 0 逐渐升到本次最终目标强度")
                self.ramp_duration_ms = UI.QSpinBox()
                self.ramp_duration_ms.setRange(100, 60000)
                self.ramp_duration_ms.setSingleStep(100)
                self.ramp_duration_ms.setSuffix(" ms")
                self.ramp_steps = UI.QSpinBox()
                self.ramp_steps.setRange(2, 100)
                form.addRow("", self.ramp_enabled)
                form.addRow("渐升时间", self.ramp_duration_ms)
                form.addRow("渐升步数", self.ramp_steps)
                note = UI.QLabel(
                    "默认关闭。目标强度不是固定值：仍先应用随机强度、百分比档位、"
                    "瞬时大变化加强和最大强度限制，再从 0 渐升到计算后的最终值。"
                )
                note.setObjectName("muted")
                note.setWordWrap(True)
                form.addRow("", note)
                self._insert_extension_widget(ramp_box)

            self.ext_item_filter = None
            if key == "consumedItem":
                box = UI.QGroupBox("食用物品筛选")
                form = UI.QFormLayout(box)
                self.ext_item_filter = UI.QLineEdit()
                self.ext_item_filter.setPlaceholderText(
                    "留空=任意；支持逗号 / 分号 / 竖线分隔，例如 Marshmallow, Berry"
                )
                form.addRow("物品名称", self.ext_item_filter)
                note = UI.QLabel(
                    "使用 PEAK 插件发送的 lastConsumedItem 事件；部分物品只能通过饥饿值变化推断，日志会标记“推断”。"
                )
                note.setObjectName("muted")
                note.setWordWrap(True)
                form.addRow("", note)
                self._insert_extension_widget(box)

            self.ext_trigger_delta = None
            if key in ("hpRecover", "staminaRecover", "statusRecover"):
                box = UI.QGroupBox("恢复触发条件")
                form = UI.QFormLayout(box)
                self.ext_trigger_delta = UI.QDoubleSpinBox()
                self.ext_trigger_delta.setRange(0.1, 100.0)
                self.ext_trigger_delta.setDecimals(1)
                self.ext_trigger_delta.setSingleStep(0.5)
                self.ext_trigger_delta.setSuffix(" %")
                form.addRow("至少恢复", self.ext_trigger_delta)
                note = UI.QLabel(
                    "血量/体力会按一次连续恢复过程累计；单次模式每段连续恢复最多触发一次。"
                    "持续模式则在恢复仍在进行时按规则冷却重复触发。"
                    if key != "statusRecover" else
                    "任一异常状态数值下降达到该幅度即可触发；同一遥测包只选择恢复幅度最大的状态。"
                )
                note.setObjectName("muted")
                note.setWordWrap(True)
                form.addRow("", note)
                self._insert_extension_widget(box)

            self.area_table = None
            self.area_dwell_seconds = None
            if key in ("areaEnter", "areaDwell"):
                self._build_area_editor(UI)

        def _insert_extension_widget(self, widget):
            layout = self.layout()
            index = max(0, layout.count() - 1)
            layout.insertWidget(index, widget)

        def _refresh_random_waveform_list(self, checked_names=None):
            checked = set(checked_names or [])
            # If called after user interaction and no explicit list was passed,
            # preserve the current checked state.
            if checked_names is None and hasattr(self, "random_waveform_list"):
                checked = set(self._selected_random_waveforms())

            self.random_waveform_list.clear()
            for name in B.waveform_names():
                item = UI.QListWidgetItem(name)
                item.setFlags(item.flags() | UI.Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    UI.Qt.CheckState.Checked
                    if name in checked
                    else UI.Qt.CheckState.Unchecked
                )
                self.random_waveform_list.addItem(item)

        def _selected_random_waveforms(self):
            if not hasattr(self, "random_waveform_list"):
                return []
            result = []
            for i in range(self.random_waveform_list.count()):
                item = self.random_waveform_list.item(i)
                if item.checkState() == UI.Qt.CheckState.Checked:
                    result.append(item.text())
            return result

        def refresh_waveforms(self):
            # Base constructor calls this before extension widgets exist.
            super().refresh_waveforms()
            if hasattr(self, "random_waveform_list"):
                selected = self._selected_random_waveforms()
                self._refresh_random_waveform_list(selected)

        def _build_area_editor(self, UI_mod):
            box = UI_mod.QGroupBox("区域定义")
            v = UI_mod.QVBoxLayout(box)
            note = UI_mod.QLabel(
                "区域使用“场景 + 世界坐标中心 + 半径”判断。可站到山顶篝火旁、沙漠虫洞等位置后点击“抓取当前位置”。"
                "场景留空表示任意场景。新增区域规则默认关闭。"
            )
            note.setObjectName("muted")
            note.setWordWrap(True)
            v.addWidget(note)

            self.area_table = UI_mod.QTableWidget(0, 6)
            self.area_table.setHorizontalHeaderLabels(["名称", "场景", "X", "Y", "Z", "半径(m)"])
            self.area_table.horizontalHeader().setSectionResizeMode(
                0, UI_mod.QHeaderView.ResizeMode.Stretch
            )
            self.area_table.horizontalHeader().setSectionResizeMode(
                1, UI_mod.QHeaderView.ResizeMode.Stretch
            )
            for col in range(2, 6):
                self.area_table.horizontalHeader().setSectionResizeMode(
                    col, UI_mod.QHeaderView.ResizeMode.ResizeToContents
                )
            self.area_table.setMinimumHeight(150)
            v.addWidget(self.area_table)

            buttons = UI_mod.QHBoxLayout()
            add_btn = UI_mod.QPushButton("＋ 添加区域")
            add_btn.clicked.connect(lambda: self._add_area_row())
            capture_btn = UI_mod.QPushButton("抓取当前位置")
            capture_btn.clicked.connect(self._capture_current_area)
            delete_btn = UI_mod.QPushButton("删除选中区域")
            delete_btn.setObjectName("dangerGhost")
            delete_btn.clicked.connect(self._delete_selected_area_rows)
            buttons.addWidget(add_btn)
            buttons.addWidget(capture_btn)
            buttons.addWidget(delete_btn)
            buttons.addStretch(1)
            v.addLayout(buttons)

            if self.key == "areaDwell":
                form = UI_mod.QFormLayout()
                self.area_dwell_seconds = UI_mod.QDoubleSpinBox()
                self.area_dwell_seconds.setRange(0.5, 86400.0)
                self.area_dwell_seconds.setDecimals(1)
                self.area_dwell_seconds.setSingleStep(5.0)
                self.area_dwell_seconds.setSuffix(" s")
                form.addRow("连续停留达到", self.area_dwell_seconds)
                v.addLayout(form)

            self._insert_extension_widget(box)

        def _add_area_row(self, zone=None):
            if self.area_table is None:
                return
            zone = zone or {
                "name": f"区域 {self.area_table.rowCount() + 1}",
                "scene": "",
                "x": 0.0,
                "y": 0.0,
                "z": 0.0,
                "radius": 5.0,
            }
            row = self.area_table.rowCount()
            self.area_table.insertRow(row)
            values = [
                zone.get("name", ""),
                zone.get("scene", ""),
                zone.get("x", 0.0),
                zone.get("y", 0.0),
                zone.get("z", 0.0),
                zone.get("radius", 5.0),
            ]
            for col, value in enumerate(values):
                self.area_table.setItem(row, col, UI.QTableWidgetItem(str(value)))

        def _delete_selected_area_rows(self):
            if self.area_table is None:
                return
            rows = sorted({idx.row() for idx in self.area_table.selectedIndexes()}, reverse=True)
            for row in rows:
                self.area_table.removeRow(row)

        def _capture_current_area(self):
            try:
                with B.peak_lock:
                    packet = dict(B.latest_peak or {})
            except Exception:
                packet = {}

            pos = _packet_position(packet)
            if not packet or packet.get("hasCharacter", True) is False or pos is None:
                UI.QMessageBox.information(
                    self,
                    "无法抓取位置",
                    "当前没有有效的 PEAK 局内遥测。请进入游戏并站到目标位置后再点击。",
                )
                return

            scene = str(packet.get("scene", "") or "")
            default_name = f"区域 {self.area_table.rowCount() + 1}"
            self._add_area_row({
                "name": default_name,
                "scene": scene,
                "x": round(pos[0], 3),
                "y": round(pos[1], 3),
                "z": round(pos[2], 3),
                "radius": 5.0,
            })

        def _area_data(self):
            if self.area_table is None:
                return []
            raw = []
            for row in range(self.area_table.rowCount()):
                def text(col):
                    item = self.area_table.item(row, col)
                    return item.text().strip() if item is not None else ""
                try:
                    raw.append({
                        "name": text(0),
                        "scene": text(1),
                        "x": float(text(2) or 0),
                        "y": float(text(3) or 0),
                        "z": float(text(4) or 0),
                        "radius": float(text(5) or 5),
                    })
                except Exception:
                    continue
            return normalize_area_zones(raw)

        def load_rule(self, c):
            super().load_rule(c)
            # During base __init__, load_rule is not called until after extension
            # widgets have been created by Window, so these fields are present.
            if hasattr(self, "random_waveform_enabled"):
                self.random_waveform_enabled.setChecked(bool(c.get("random_waveform", False)))
                pool = normalize_waveform_pool(c.get("random_waveforms", []))
                self._refresh_random_waveform_list(pool)

            if self.ramp_enabled is not None:
                self.ramp_enabled.setChecked(bool(c.get("ramp_enabled", False)))
                self.ramp_duration_ms.setValue(
                    _clamp_int(c.get("ramp_duration_ms", 1500), 100, 60000, 1500)
                )
                self.ramp_steps.setValue(
                    _clamp_int(c.get("ramp_steps", 10), 2, 100, 10)
                )

            if self.ext_item_filter is not None:
                self.ext_item_filter.setText(str(c.get("item_filter", "") or ""))

            if self.ext_trigger_delta is not None:
                self.ext_trigger_delta.setValue(
                    _recovery_trigger_delta(c, 1.0)
                )

            if self.area_table is not None:
                self.area_table.setRowCount(0)
                for zone in normalize_area_zones(c.get("area_zones", [])):
                    self._add_area_row(zone)
                if self.area_dwell_seconds is not None:
                    self.area_dwell_seconds.setValue(
                        _clamp_float(c.get("area_dwell_seconds", 30.0), 0.5, 86400.0, 30.0)
                    )

        def data(self):
            result = super().data()
            result["random_waveform"] = bool(self.random_waveform_enabled.isChecked())
            result["random_waveforms"] = self._selected_random_waveforms()

            if self.ramp_enabled is not None:
                result["ramp_enabled"] = bool(self.ramp_enabled.isChecked())
                result["ramp_duration_ms"] = int(self.ramp_duration_ms.value())
                result["ramp_steps"] = int(self.ramp_steps.value())
            if self.ext_item_filter is not None:
                result["item_filter"] = self.ext_item_filter.text().strip()

            if self.ext_trigger_delta is not None:
                result["trigger_delta"] = float(self.ext_trigger_delta.value())

            if self.area_table is not None:
                result["area_zones"] = self._area_data()
                if self.area_dwell_seconds is not None:
                    result["area_dwell_seconds"] = float(self.area_dwell_seconds.value())
                else:
                    result["area_dwell_seconds"] = 30.0

            return result

    UI.RuleEditor = ExtendedRuleEditor

    # Persist extension-level hardening settings next to the normal JSON config.
    original_save_full_config = UI.save_full_config

    def extended_save_full_config():
        result = original_save_full_config()
        ok = bool(result[0]) if isinstance(result, tuple) and result else bool(result)
        if not ok or not B.CONFIG_FILE.exists():
            return result
        try:
            data = json.loads(B.CONFIG_FILE.read_text(encoding="utf-8"))
            data["config_schema"] = max(2, int(data.get("config_schema", 0) or 0))
            data["extended_features"] = {
                "version": EXTENSION_VERSION,
                **_normalize_extension_settings(extension_settings),
            }
            temp_path = B.CONFIG_FILE.with_name(B.CONFIG_FILE.name + ".tmp")
            temp_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temp_path.replace(B.CONFIG_FILE)
        except Exception as exc:
            B.add_log("错误", "扩展配置保存失败", repr(exc))
        return result

    UI.save_full_config = extended_save_full_config

    BaseWindow = UI.Window

    class ExtendedWindow(BaseWindow):
        def __init__(self):
            super().__init__()
            self._install_peakshock_hardening_panel()

        def _install_peakshock_hardening_panel(self):
            layout = self.rules_tab.layout() if hasattr(self, "rules_tab") else None
            if layout is None:
                return

            box = UI.QGroupBox("全局防误触")
            grid = UI.QGridLayout(box)

            self.ext_global_guard = UI.QCheckBox("启用全局防连击")
            self.ext_global_guard.setChecked(bool(extension_settings.get("global_guard_enabled", True)))
            self.ext_global_guard_ms = UI.QSpinBox()
            self.ext_global_guard_ms.setRange(0, 5000)
            self.ext_global_guard_ms.setSingleStep(50)
            self.ext_global_guard_ms.setSuffix(" ms")
            self.ext_global_guard_ms.setValue(_clamp_int(extension_settings.get("global_guard_ms", 250), 0, 5000, 250))

            self.ext_dedupe = UI.QCheckBox("同一次 HP / Injury 伤害只输出一次")
            self.ext_dedupe.setChecked(bool(extension_settings.get("dedupe_hp_injury", True)))

            self.ext_respawn_guard = UI.QCheckBox("角色重生 / 场景切换时启用同步保护")
            self.ext_respawn_guard.setChecked(bool(extension_settings.get("respawn_guard_enabled", True)))
            self.ext_respawn_seconds = UI.QDoubleSpinBox()
            self.ext_respawn_seconds.setRange(0.5, 15.0)
            self.ext_respawn_seconds.setDecimals(1)
            self.ext_respawn_seconds.setSingleStep(0.5)
            self.ext_respawn_seconds.setSuffix(" s")
            self.ext_respawn_seconds.setValue(_clamp_float(extension_settings.get("respawn_guard_seconds", 2.5), 0.5, 15.0, 2.5))

            grid.addWidget(self.ext_global_guard, 0, 0)
            grid.addWidget(UI.QLabel("全局窗口"), 0, 1)
            grid.addWidget(self.ext_global_guard_ms, 0, 2)
            grid.addWidget(self.ext_dedupe, 1, 0, 1, 3)
            grid.addWidget(self.ext_respawn_guard, 2, 0)
            grid.addWidget(UI.QLabel("保护时间"), 2, 1)
            grid.addWidget(self.ext_respawn_seconds, 2, 2)

            note = UI.QLabel(
                "本地玩家隔离沿用 Coyote.dll 的 Character.localCharacter 遥测；"
                "死亡/昏迷安全锁继续保留，不照搬 PeakShock 在失去行动能力后仍继续输出的做法。"
                "全局防连击只作用于自动规则，手动设备控制不受影响。"
            )
            note.setObjectName("muted")
            note.setWordWrap(True)
            grid.addWidget(note, 3, 0, 1, 3)

            def sync_settings(*_):
                extension_settings["global_guard_enabled"] = self.ext_global_guard.isChecked()
                extension_settings["global_guard_ms"] = int(self.ext_global_guard_ms.value())
                extension_settings["dedupe_hp_injury"] = self.ext_dedupe.isChecked()
                extension_settings["respawn_guard_enabled"] = self.ext_respawn_guard.isChecked()
                extension_settings["respawn_guard_seconds"] = float(self.ext_respawn_seconds.value())

            self.ext_global_guard.toggled.connect(sync_settings)
            self.ext_global_guard_ms.valueChanged.connect(sync_settings)
            self.ext_dedupe.toggled.connect(sync_settings)
            self.ext_respawn_guard.toggled.connect(sync_settings)
            self.ext_respawn_seconds.valueChanged.connect(sync_settings)

            # Put it directly under the existing rule toolbar.
            insert_at = 1 if layout.count() > 1 else layout.count()
            layout.insertWidget(insert_at, box)

        def batch_apply_checked_rules(self):
            current_item = self.rule_list.currentItem() if hasattr(self, "rule_list") else None
            source_key = current_item.data(UI.Qt.ItemDataRole.UserRole) if current_item is not None else None
            checked_keys = []
            if hasattr(self, "rule_list"):
                for i in range(self.rule_list.count()):
                    item = self.rule_list.item(i)
                    if item.checkState() == UI.Qt.CheckState.Checked:
                        checked_keys.append(item.data(UI.Qt.ItemDataRole.UserRole))

            source_ext = None
            if source_key in getattr(self, "rule_editors", {}):
                source_ext = self.rule_editors[source_key].data()

            super().batch_apply_checked_rules()

            if not source_ext or not checked_keys:
                return

            # Base UI already copied its native fields. Add extension-native fields
            # without changing rule enable states or incompatible area/ramp settings.
            for key in checked_keys:
                editor = self.rule_editors.get(key)
                if editor is None:
                    continue
                merged = editor.data()
                merged["random_waveform"] = bool(source_ext.get("random_waveform", False))
                merged["random_waveforms"] = list(source_ext.get("random_waveforms", []))
                if key == "hp" and source_key == "hp":
                    merged["ramp_enabled"] = bool(source_ext.get("ramp_enabled", False))
                    merged["ramp_duration_ms"] = int(source_ext.get("ramp_duration_ms", 1500))
                    merged["ramp_steps"] = int(source_ext.get("ramp_steps", 10))
                editor.load_rule(merged)

            self.apply_rules(show=False)
            self.update_rule_group_states()

    UI.Window = ExtendedWindow

    # Existing Window.set_all_rules_enabled() iterates every RuleEditor, so the
    # newly injected rules automatically support 全部开启 / 全部关闭. Nothing
    # else is required here.

