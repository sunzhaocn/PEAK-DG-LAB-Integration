"""Strict separation between built-in rules and visual custom rules.

This layer intentionally makes the two rule systems independent:

- ``disable_builtin`` disables *all* built-in automatic rule effects, including
  built-in death/passed-out stimulation and the built-in incapacitation clear.
- Visual custom rules never inherit the built-in incapacitation output lock.
- If users want visual rules to stop after death/passed-out, they must add the
  visual custom guard nodes exposed by ``install_ui``.
- Built-in and visual death/passed-out rules may coexist when built-ins are not
  disabled. A visual special graph no longer suppresses its built-in counterpart.

The physical DG-LAB connection, master output switch, slot validation, intensity
limits and explicit stop/disconnect operations remain shared infrastructure.
"""
from __future__ import annotations

import random
import time

import backend as B
import extended_features as EXT
import visual_rules as V

_INSTALLED = False
_UI_INSTALLED = False
_ORIGINAL_CLEAR_DEVICE_OUTPUT = None
_ORIGINAL_EVAL = None


def _graph_is_special(graph):
    return any(
        node.get("type") in V._SPECIAL_TYPES
        for node in graph.get("nodes", [])
        if isinstance(node, dict)
    )


def _custom_guard_value(node, current):
    """Return a guard result, or None when this is not a guard node."""
    if not isinstance(node, dict) or node.get("type") != "telemetry":
        return None
    params = node.get("params") if isinstance(node.get("params"), dict) else {}
    guard = str(params.get("custom_guard") or "").strip().lower()
    if guard == "alive":
        return not bool(current.get("dead", False))
    if guard == "conscious":
        return not bool(current.get("passedOut", False))
    if guard == "active":
        return not (
            bool(current.get("dead", False))
            or bool(current.get("passedOut", False))
        )
    return None


def _eval_separated(graph, node_id, current, previous, cache, stack):
    node = V._node_map(graph).get(node_id)
    guard = _custom_guard_value(node, current)
    if guard is not None:
        cache[node_id] = guard
        return guard
    return _ORIGINAL_EVAL(graph, node_id, current, previous, cache, stack)


def _send_visual_graph(graph, output_node, cfg, pool, value, delta, privileged):
    """Send visual output without consulting the built-in incapacitation lock."""
    cfg = V._copy(cfg)
    params = output_node.get("params", {}) if isinstance(output_node, dict) else {}
    mode = str(params.get("mode", "edge") or "edge").lower()
    repeated = (
        mode in {"while", "repeat"}
        or B.is_continuous_duration(cfg.get("play_time_a", 1000))
        or B.is_continuous_duration(cfg.get("play_time_b", 1000))
    )
    if repeated:
        cfg["cooldown"] = B.continuous_effective_cooldown(cfg)

    if not B.master_output_enabled:
        return False
    slot = B.get_slot_id()
    if not slot:
        return False

    runtime = V._rt(graph["id"], output_node["id"])
    now = time.monotonic()
    cooldown = B.clamp_cooldown(cfg.get("cooldown", 2))
    if now - V._num(runtime.get("last", -1e12), -1e12) < cooldown:
        return False
    runtime["last"] = now

    # The existing ramp worker has its own built-in incapacitation guard.
    # Reuse it only while the character is active. During death/passed-out,
    # custom rules remain authoritative and therefore send immediately instead
    # of being cancelled by that built-in guard.
    ramp = bool(cfg.get("_visual_ramp_enabled", False)) and not B.peak_is_incapacitated()
    if ramp:
        with EXT._RAMP_LOCK:
            EXT._RAMP_GENERATION += 1
            generation = EXT._RAMP_GENERATION
        EXT._RAMP_CONTEXT.value = {
            "generation": generation,
            "ramp_duration_ms": int(cfg.get("_visual_ramp_duration_ms", 1500)),
            "ramp_steps": int(cfg.get("_visual_ramp_steps", 10)),
        }

    try:
        info = B.calculate_rule_intensities(cfg, value, delta)
        intensity_a, intensity_b = info["final_a"], info["final_b"]
        duration_a_cfg = B.clamp_duration(cfg.get("play_time_a", 1000))
        duration_b_cfg = B.clamp_duration(cfg.get("play_time_b", 1000))
        duration_a = B.resolve_rule_duration_ms(duration_a_cfg)
        duration_b = B.resolve_rule_duration_ms(duration_b_cfg)

        if pool:
            waveform_a, waveform_b = random.choice(pool), random.choice(pool)
        else:
            waveform_a = cfg.get("waveform_a", "脉冲")
            waveform_b = cfg.get("waveform_b", "脉冲")

        tier = info.get("tier")
        if tier:
            tier_a = tier.get("waveform_a")
            tier_b = tier.get("waveform_b")
            if tier_a not in (None, B.TIER_WAVEFORM_INHERIT) and tier_a in B.COYOTE_WAVEFORMS:
                waveform_a = tier_a
            if tier_b not in (None, B.TIER_WAVEFORM_INHERIT) and tier_b in B.COYOTE_WAVEFORMS:
                waveform_b = tier_b

        results = []
        for channel, intensity, duration, waveform in (
            (0, intensity_a, duration_a, waveform_a),
            (1, intensity_b, duration_b, waveform_b),
        ):
            if intensity <= 0:
                continue
            results.append(
                B.send_rpc(
                    "device.op",
                    {
                        "s": slot,
                        "c": channel,
                        "t": 4,
                        "v": intensity,
                        "d": duration,
                        "im": True,
                    },
                )
            )
            results.append(
                B.send_rpc(
                    "device.op",
                    {
                        "s": slot,
                        "c": channel,
                        "t": 0,
                        "v": B.COYOTE_WAVEFORMS.get(
                            waveform,
                            B.COYOTE_WAVEFORMS["脉冲"],
                        ),
                        "d": duration,
                        "im": True,
                    },
                )
            )

        success = bool(results) and all(ok for ok, _ in results)
        special = _graph_is_special(graph)
        try:
            with B.log_lock:
                B.output_count += 1
                B.last_output = {
                    "event": graph.get("name", "图形规则"),
                    "change": (
                        "自定义死亡/昏迷规则"
                        if special
                        else "自定义图形条件成立"
                    ),
                    "a_intensity": intensity_a,
                    "b_intensity": intensity_b,
                    "a_duration": duration_a_cfg,
                    "b_duration": duration_b_cfg,
                    "a_waveform": waveform_a,
                    "b_waveform": waveform_b,
                    "success": success,
                    "visual_graph": True,
                    "custom_rule": True,
                }
        except Exception:
            pass

        B.add_log(
            "图形规则",
            graph.get("name", "规则图"),
            (
                f"A={intensity_a}/{duration_a_cfg}ms/{waveform_a} | "
                f"B={intensity_b}/{duration_b_cfg}ms/{waveform_b} | "
                f"{'发送成功' if success else '发送失败'}"
            ),
        )
        return success
    finally:
        if ramp:
            EXT._RAMP_CONTEXT.value = None


def _evaluate_visual_graph(graph, current, previous, privileged=False):
    """Evaluate any enabled visual graph regardless of PEAK incapacitation."""
    if (
        not graph.get("enabled")
        or graph.get("id") not in V.valid_graph_ids
    ):
        return False

    cache = {}
    sent = False
    for output_node in [
        node
        for node in graph.get("nodes", [])
        if node.get("type") == "output"
    ]:
        conditions = [
            V._eval(
                graph,
                edge["from"],
                current,
                previous,
                cache,
                set(),
            )
            for edge in V._incoming(graph, output_node["id"], "in")
        ]
        active = bool(conditions) and all(V._truth(value) for value in conditions)
        cfg, pool = V._config(graph, output_node, current, previous, cache)
        runtime = V._rt(graph["id"], output_node["id"])
        old = bool(runtime.get("active", False))
        runtime["active"] = active
        mode = str(output_node.get("params", {}).get("mode", "edge")).lower()
        continuous = (
            B.is_continuous_duration(cfg.get("play_time_a"))
            or B.is_continuous_duration(cfg.get("play_time_b"))
        )

        if not (
            active
            if mode in {"while", "repeat"} or continuous
            else active and not old
        ):
            continue

        value_inputs = V._incoming(graph, output_node["id"], "value")
        delta_inputs = V._incoming(graph, output_node["id"], "delta")
        value = delta = None
        if value_inputs:
            value = V._num(
                V._eval(
                    graph,
                    value_inputs[0]["from"],
                    current,
                    previous,
                    cache,
                    set(),
                )
            )
        if delta_inputs:
            delta = abs(
                V._num(
                    V._eval(
                        graph,
                        delta_inputs[0]["from"],
                        current,
                        previous,
                        cache,
                        set(),
                    )
                )
            )

        sent = (
            V._send_graph(
                graph,
                output_node,
                cfg,
                pool,
                value,
                delta,
                _graph_is_special(graph),
            )
            or sent
        )
    return sent


def _evaluate_all_visual(current, previous, privileged=False):
    # ``privileged`` is intentionally ignored. It belongs to the old coupled
    # model. Every enabled custom graph is evaluated in the custom rule domain.
    with V._LOCK:
        snapshot = list(V.graphs)
    sent = False
    for graph in snapshot:
        sent = V.evaluate_graph(graph, current, previous, False) or sent
    return sent


def _send_builtin_special(key, name, detail):
    """Built-in death/passed-out fallback, independent from custom graphs."""
    if V.builtins_disabled():
        return False

    cfg = B.get_rule_copy(key)
    if not cfg.get("enabled") or not B.master_output_enabled:
        return False
    slot = B.get_slot_id()
    if not slot or not B.rule_can_trigger(
        key,
        B.clamp_cooldown(cfg.get("cooldown", 2)),
    ):
        return False

    info = B.calculate_rule_intensities(cfg, None, None)
    intensity_a, intensity_b = info["final_a"], info["final_b"]
    duration_a_cfg = B.clamp_duration(cfg.get("play_time_a", 1000))
    duration_b_cfg = B.clamp_duration(cfg.get("play_time_b", 1000))
    duration_a = B.resolve_rule_duration_ms(duration_a_cfg)
    duration_b = B.resolve_rule_duration_ms(duration_b_cfg)
    waveform_a = str(cfg.get("waveform_a", "脉冲"))
    waveform_b = str(cfg.get("waveform_b", "脉冲"))
    results = []

    for channel, intensity, duration, waveform in (
        (0, intensity_a, duration_a, waveform_a),
        (1, intensity_b, duration_b, waveform_b),
    ):
        if intensity <= 0:
            continue
        results.append(
            B.send_rpc(
                "device.op",
                {
                    "s": slot,
                    "c": channel,
                    "t": 4,
                    "v": intensity,
                    "d": duration,
                    "im": True,
                },
            )
        )
        results.append(
            B.send_rpc(
                "device.op",
                {
                    "s": slot,
                    "c": channel,
                    "t": 0,
                    "v": B.COYOTE_WAVEFORMS.get(
                        waveform,
                        B.COYOTE_WAVEFORMS["脉冲"],
                    ),
                    "d": duration,
                    "im": True,
                },
            )
        )

    success = bool(results) and all(ok for ok, _ in results)
    B.add_log(
        "输出",
        name,
        (
            f"{detail} | 官方死亡/昏迷专用规则 | "
            f"{'发送成功' if success else '发送失败'}"
        ),
    )
    return success


def _clear_device_output_separated(reason=""):
    """Do not apply the built-in incapacity clear while built-ins are disabled."""
    text = str(reason or "")
    is_builtin_incapacity_clear = (
        "死亡/昏迷" in text
        and (
            "安全锁" in text
            or "先清除普通输出" in text
            or "清除" in text
        )
    )
    if V.builtins_disabled() and is_builtin_incapacity_clear:
        B.add_log(
            "系统",
            "官方死亡/昏迷保护已禁用",
            "禁用软件内置规则生效：不清除自定义图形规则输出。",
        )
        return True
    return _ORIGINAL_CLEAR_DEVICE_OUTPUT(reason)


def install():
    global _INSTALLED, _ORIGINAL_CLEAR_DEVICE_OUTPUT, _ORIGINAL_EVAL
    if _INSTALLED:
        return
    _INSTALLED = True

    _ORIGINAL_EVAL = V._eval
    V._eval = _eval_separated
    V._send_graph = _send_visual_graph
    V.evaluate_graph = _evaluate_visual_graph
    V.evaluate_all = _evaluate_all_visual

    # Replace the hardening layer's "custom special graph owns the edge" rule.
    # Built-in and custom special rules are independent unless built-ins are
    # explicitly disabled by disable_builtin.
    V._send_special_builtin = _send_builtin_special

    _ORIGINAL_CLEAR_DEVICE_OUTPUT = B.clear_device_output
    B.clear_device_output = _clear_device_output_separated

    B.COYOTE_VISUAL_RULES_SEPARATION = 1


def _label_guard_nodes(editor):
    labels = {
        "alive": "未死亡保护（自定义）",
        "conscious": "未昏迷保护（自定义）",
        "active": "存活且清醒保护（自定义）",
    }
    for node_data in (editor.current or {}).get("nodes", []):
        params = (
            node_data.get("params")
            if isinstance(node_data.get("params"), dict)
            else {}
        )
        guard = str(params.get("custom_guard") or "").strip().lower()
        label = labels.get(guard)
        item = editor.nodes.get(node_data.get("id"))
        if not label or item is None:
            continue
        item.title = label
        for child in item.childItems():
            setter = getattr(child, "setPlainText", None)
            if callable(setter):
                setter(label)
                break


def _enhance_editor(editor, UI):
    cls = type(editor)
    if getattr(cls, "_coyote_rule_separation_installed", False):
        return
    cls._coyote_rule_separation_installed = True

    from PySide6.QtWidgets import QTreeWidgetItem

    safety = QTreeWidgetItem(editor.palette, ["自定义规则保护"])
    editor.leaf(
        safety,
        "未死亡保护（仅自定义）",
        "telemetry",
        {
            "path": "dead",
            "default": False,
            "custom_guard": "alive",
        },
    )
    editor.leaf(
        safety,
        "未昏迷保护（仅自定义）",
        "telemetry",
        {
            "path": "passedOut",
            "default": False,
            "custom_guard": "conscious",
        },
    )
    editor.leaf(
        safety,
        "存活且清醒保护（仅自定义）",
        "telemetry",
        {
            "path": "dead",
            "default": False,
            "custom_guard": "active",
        },
    )
    safety.setExpanded(True)

    original_rebuild = cls.rebuild

    def rebuild(self):
        result = original_rebuild(self)
        _label_guard_nodes(self)
        return result

    cls.rebuild = rebuild
    editor.rebuild()

    try:
        editor.status.setText(
            "官方规则与自定义图形规则已完全分域。"
            "“禁用软件内置规则”会同时关闭官方死亡/昏迷电击与官方死亡/昏迷保护；"
            "自定义规则若要死亡/昏迷后停止输出，请自行加入“自定义规则保护”模块。"
        )
    except Exception:
        pass


def install_ui(UI):
    global _UI_INSTALLED
    if _UI_INSTALLED:
        return
    _UI_INSTALLED = True

    BaseWindow = UI.Window

    class SeparatedVisualWindow(BaseWindow):
        def build_custom_code(self):
            super().build_custom_code()
            try:
                _enhance_editor(self.visual_rule_editor, UI)
            except Exception as exc:
                B.add_log(
                    "错误",
                    "图形规则分域 UI 安装失败",
                    repr(exc),
                )

    UI.Window = SeparatedVisualWindow
