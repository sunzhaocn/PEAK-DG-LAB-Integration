"""User-facing polish for the visual rule editor.

This layer changes presentation only: node identity, colours, port labels,
property labels and redundant help text. Rule evaluation and saved graph data
remain unchanged.
"""
from __future__ import annotations

import json

import backend as B

_INSTALLED = False

_TYPE_CATEGORY = {
    "death": "special",
    "passed": "special",
    "trigger": "event",
    "telemetry": "data",
    "status": "data",
    "item": "data",
    "changed": "data",
    "constant": "data",
    "compare": "logic",
    "and": "logic",
    "or": "logic",
    "not": "logic",
    "edge": "logic",
    "cooldown": "logic",
    "intensity": "parameter",
    "duration": "parameter",
    "waveform": "parameter",
    "threshold": "parameter",
    "spike": "parameter",
    "random_waveform": "parameter",
    "disable_builtin": "control",
    "output": "action",
    "comment": "note",
}

_CATEGORY_COLOURS = {
    "special": ("#45232D", "#FF6B81"),
    "event": ("#453518", "#F1B84B"),
    "data": ("#17384A", "#4DB6E6"),
    "logic": ("#332652", "#A78BFA"),
    "parameter": ("#173E38", "#46C6A8"),
    "guard": ("#1C402B", "#67D391"),
    "control": ("#4A3519", "#F5A64A"),
    "action": ("#482326", "#FF6B6B"),
    "note": ("#2D3138", "#98A2B3"),
}

_NODE_HELP = {
    "death": "角色进入死亡状态时输出条件。",
    "passed": "角色进入昏迷状态时输出条件。",
    "trigger": "监听所选游戏事件；事件发生时输出条件。",
    "telemetry": "读取一个游戏遥测字段。",
    "status": "读取 Injury / Hunger / Cold / Poison 等状态百分比。",
    "item": "检查手持、口袋或背包中的物品名称。",
    "changed": "指定遥测字段发生变化时输出条件。",
    "constant": "提供一个固定数值给比较或输出计算。",
    "compare": "比较 A 与 B，结果作为条件输出。",
    "and": "所有输入条件都成立时输出真。",
    "or": "任意一个输入条件成立时输出真。",
    "not": "把输入条件取反。",
    "edge": "把持续条件转换成上升沿、下降沿或持续条件。",
    "cooldown": "限制条件通过的最短时间间隔。",
    "intensity": "设置 A/B 通道强度及随机/渐升参数。",
    "duration": "设置 A/B 通道持续时间。",
    "waveform": "设置 A/B 通道波形。",
    "threshold": "按百分比区间附加强度或波形修正。",
    "spike": "根据瞬时变化量附加强度。",
    "random_waveform": "从指定波形池中随机选择波形。",
    "disable_builtin": "只要该模块存在于有效启用的普通图中，就关闭软件官方自动规则。",
    "output": "条件成立后把已连接的强度、时长、波形和修正发送到设备。",
    "comment": "仅用于给规则图添加备注，不参与规则计算。",
}

_PARAM_LABELS = {
    "rule_key": "规则事件",
    "path": "遥测字段",
    "default": "默认值",
    "name": "状态名称",
    "where": "物品位置",
    "contains": "名称包含",
    "value": "数值",
    "op": "比较方式",
    "seconds": "冷却时间（秒）",
    "mode": "触发方式",
    "trigger_delta": "触发变化量（%）",
    "speed_threshold": "速度阈值",
    "item_filter": "物品名称筛选",
    "trigger_mode": "事件触发方式",
    "area_zones": "区域列表",
    "area_dwell_seconds": "区域停留时间（秒）",
    "ramp_enabled": "启用强度渐升",
    "ramp_duration_ms": "渐升时间（毫秒）",
    "ramp_steps": "渐升步数",
    "pool": "随机波形池",
    "below": "低于百分比",
    "add_a": "A 通道附加强度",
    "add_b": "B 通道附加强度",
    "waveform_a": "A 通道波形",
    "waveform_b": "B 通道波形",
    "delta": "变化量阈值",
    "min_a": "A 通道最小值",
    "max_a": "A 通道最大值",
    "min_b": "B 通道最小值",
    "max_b": "B 通道最大值",
    "max_rand_a": "A 随机最大值",
    "max_rand_b": "B 随机最大值",
    "random": "启用随机强度",
    "a": "A",
    "b": "B",
    "cooldown": "输出冷却（秒）",
    "text": "备注内容",
}

_HIDDEN_PARAMS = {"custom_guard"}


def _rule_display_name(key: str) -> str:
    for rule_key, display, *_ in getattr(B, "RULE_META", ()):
        if str(rule_key) == str(key):
            return str(display)
    return str(key or "游戏事件")


def _status_display_name(name: str) -> str:
    for raw, display in getattr(B, "STATUS_ORDER", ()):
        if str(name) in {str(raw), str(display)}:
            return str(display)
    return str(name or "状态")


def _guard_kind(data: dict) -> str:
    if data.get("type") != "telemetry":
        return ""
    params = data.get("params") if isinstance(data.get("params"), dict) else {}
    return str(params.get("custom_guard") or "").strip().lower()


def _node_title(data: dict) -> str:
    typ = str(data.get("type") or "")
    params = data.get("params") if isinstance(data.get("params"), dict) else {}
    guard = _guard_kind(data)
    if guard:
        return {
            "alive": "未死亡保护",
            "conscious": "未昏迷保护",
            "active": "存活且清醒保护",
        }.get(guard, "自定义保护")
    if typ == "trigger":
        return f"事件 · {_rule_display_name(params.get('rule_key'))}"
    if typ == "telemetry":
        return f"遥测 · {params.get('path', '字段')}"
    if typ == "status":
        return f"状态 · {_status_display_name(params.get('name'))}"
    if typ == "item":
        where = {"held": "手持", "pocket": "口袋", "backpack": "背包"}.get(
            str(params.get("where") or ""), "物品"
        )
        needle = str(params.get("contains") or "").strip()
        return f"物品 · {where}" + (f" · {needle}" if needle else "")
    if typ == "changed":
        return f"变化 · {params.get('path', '字段')}"
    if typ == "constant":
        return f"常量 · {params.get('value', 0)}"
    if typ == "compare":
        return f"比较 · {params.get('op', '>')}"
    if typ == "edge":
        mode = {
            "rising": "上升沿",
            "falling": "下降沿",
            "while": "持续",
        }.get(str(params.get("mode") or "").lower(), "边沿")
        return mode
    if typ == "cooldown":
        return f"冷却 · {params.get('seconds', 2)}s"
    return {
        "death": "死亡（专用）",
        "passed": "昏迷（专用）",
        "and": "AND · 全部成立",
        "or": "OR · 任一成立",
        "not": "NOT · 条件取反",
        "intensity": "强度设置",
        "duration": "持续时间",
        "waveform": "波形设置",
        "threshold": "百分比档位",
        "spike": "瞬时变化加强",
        "random_waveform": "随机波形",
        "disable_builtin": "禁用软件内置规则",
        "output": "电击输出",
        "comment": "备注",
    }.get(typ, typ or "模块")


def _port_label(data: dict, name: str, output: bool) -> str:
    typ = str(data.get("type") or "")
    if not output:
        if typ == "compare":
            return {"a": "左值 A", "b": "右值 B"}.get(name, name)
        if typ == "output":
            return {
                "in": "触发条件",
                "intensity": "强度设置",
                "duration": "持续时间",
                "waveform": "波形",
                "modifier": "附加修正",
                "value": "当前值",
                "delta": "变化量",
            }.get(name, name)
        if name == "in":
            return "条件"
        return name
    if name == "config":
        return {
            "intensity": "强度配置",
            "duration": "时间配置",
            "waveform": "波形配置",
            "threshold": "档位修正",
            "spike": "变化修正",
            "random_waveform": "随机波形",
        }.get(typ, "配置")
    if name == "value":
        if typ in {"death", "passed", "trigger", "changed", "compare", "and", "or", "not", "edge", "cooldown"}:
            return "条件"
        if typ == "item":
            return "匹配结果"
        return "数值"
    return name


def _param_label(node_type: str, key: str) -> str:
    if node_type == "intensity":
        local = {
            "a": "A 通道强度",
            "b": "B 通道强度",
            "max_a": "A 通道强度上限",
            "max_b": "B 通道强度上限",
            "min_a": "A 随机最小值",
            "min_b": "B 随机最小值",
        }
        if key in local:
            return local[key]
    if node_type == "duration":
        return {"a": "A 通道时长（毫秒）", "b": "B 通道时长（毫秒）"}.get(
            key, _PARAM_LABELS.get(key, key)
        )
    if node_type == "waveform":
        return {"a": "A 通道波形", "b": "B 通道波形"}.get(
            key, _PARAM_LABELS.get(key, key)
        )
    if node_type == "compare":
        return {"a": "默认左值 A", "b": "默认右值 B"}.get(
            key, _PARAM_LABELS.get(key, key)
        )
    return _PARAM_LABELS.get(key, key)


def _format_value(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if value is True:
        return "true"
    if value is False:
        return "false"
    return str(value)


def _decorate_node(node, UI) -> None:
    from PySide6.QtGui import QBrush, QPen
    from PySide6.QtWidgets import QGraphicsTextItem

    data = node.data if isinstance(getattr(node, "data", None), dict) else {}
    typ = str(data.get("type") or "")
    category = "guard" if _guard_kind(data) else _TYPE_CATEGORY.get(typ, "note")
    background, border = _CATEGORY_COLOURS[category]
    node.setBrush(QBrush(UI.QColor(background)))
    node.setPen(QPen(UI.QColor(border), 2.0))

    title = _node_title(data)
    node.title = title
    existing_text = [
        child for child in node.childItems() if isinstance(child, QGraphicsTextItem)
    ]
    if existing_text:
        title_item = min(existing_text, key=lambda item: item.pos().y())
        title_item.setPlainText(title)
        title_item.setDefaultTextColor(UI.QColor("#FFFFFF"))
        title_item.setPos(10, 5)

    width = 276
    rows = max(len(getattr(node, "ins", {})), len(getattr(node, "outs", {})), 1)
    height = max(90, 64 + rows * 25)
    node.setRect(0, 0, width, height)
    node.setToolTip(_NODE_HELP.get(typ, title))

    for index, (name, port) in enumerate(getattr(node, "ins", {}).items()):
        y = 52 + index * 25
        port.setPos(0, y)
        label = _port_label(data, name, False)
        port.setToolTip(f"输入：{label}")
        text = QGraphicsTextItem(label, node)
        text.setDefaultTextColor(UI.QColor("#DCE5F2"))
        text.setPos(12, y - 11)

    for index, (name, port) in enumerate(getattr(node, "outs", {}).items()):
        y = 52 + index * 25
        port.setPos(width, y)
        label = _port_label(data, name, True)
        port.setToolTip(f"输出：{label}")
        text = QGraphicsTextItem(label, node)
        text.setDefaultTextColor(UI.QColor("#DCE5F2"))
        text.setPos(width - text.boundingRect().width() - 12, y - 11)


def _decorate_palette(editor, UI) -> None:
    from PySide6.QtGui import QBrush

    def walk(item):
        data = item.data(0, UI.Qt.ItemDataRole.UserRole)
        if isinstance(data, dict):
            typ = str(data.get("type") or "")
            category = _TYPE_CATEGORY.get(typ, "note")
            if typ == "telemetry" and str(
                (data.get("params") or {}).get("custom_guard") or ""
            ):
                category = "guard"
            item.setForeground(0, QBrush(UI.QColor(_CATEGORY_COLOURS[category][1])))
            item.setToolTip(0, _NODE_HELP.get(typ, "双击添加模块"))
            if typ == "disable_builtin":
                item.setText(0, "禁用软件内置规则")
        for index in range(item.childCount()):
            walk(item.child(index))

    root = editor.palette.invisibleRootItem()
    for index in range(root.childCount()):
        walk(root.child(index))


def _install_editor_polish(editor, UI) -> None:
    cls = type(editor)
    if getattr(cls, "_coyote_user_polish_installed", False):
        _decorate_palette(editor, UI)
        return
    cls._coyote_user_polish_installed = True

    original_rebuild = cls.rebuild
    original_apply = cls.apply

    def rebuild(self):
        result = original_rebuild(self)
        for node in self.nodes.values():
            _decorate_node(node, UI)
        for edge in self.edges:
            edge.update_path()
        _decorate_palette(self, UI)
        return result

    def show_props(self, node):
        self.selected = node
        params = node.data.get("params", {}) if isinstance(node.data, dict) else {}
        node_type = str(node.data.get("type") or "")
        hidden = set(_HIDDEN_PARAMS)
        if node_type == "trigger":
            hidden.add("rule_key")
        if _guard_kind(node.data):
            hidden.update({"path", "default"})
        entries = [(key, value) for key, value in params.items() if key not in hidden]
        self.props.setRowCount(len(entries))
        self.props.setHorizontalHeaderLabels(["设置", "值"])
        for row, (key, value) in enumerate(entries):
            key_item = UI.QTableWidgetItem(_param_label(node_type, key))
            key_item.setFlags(key_item.flags() & ~UI.Qt.ItemFlag.ItemIsEditable)
            key_item.setData(UI.Qt.ItemDataRole.UserRole, key)
            self.props.setItem(row, 0, key_item)
            self.props.setItem(row, 1, UI.QTableWidgetItem(_format_value(value)))

    def apply(self):
        if not self.selected:
            return
        params = self.selected.data.setdefault("params", {})
        for row in range(self.props.rowCount()):
            key_item = self.props.item(row, 0)
            value_item = self.props.item(row, 1)
            if key_item is None or value_item is None:
                continue
            key = key_item.data(UI.Qt.ItemDataRole.UserRole) or key_item.text()
            text = value_item.text().strip()
            old = params.get(key)
            try:
                if isinstance(old, bool):
                    value = text.lower() in {"1", "true", "yes", "on", "是"}
                elif isinstance(old, int):
                    value = int(float(text))
                elif isinstance(old, float):
                    value = float(text)
                elif isinstance(old, (list, dict)):
                    value = json.loads(text)
                else:
                    value = text
                params[key] = value
            except Exception:
                continue
        self.rebuild()

    cls.rebuild = rebuild
    cls.show_props = show_props
    cls.apply = apply

    # The embedded manual, bottom instruction banner and repeated policy text make
    # the editor visually noisy. Keep help in tooltips and the dedicated md docs.
    try:
        editor.status.hide()
    except Exception:
        pass
    for text_edit in editor.findChildren(UI.QTextEdit):
        try:
            if text_edit.isReadOnly():
                text_edit.hide()
        except Exception:
            pass

    _decorate_palette(editor, UI)
    editor.rebuild()


def install_ui(UI) -> None:
    """Install after the visual rule engine/integration/policy UI layers."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    BaseWindow = UI.Window

    class UserPolishedVisualWindow(BaseWindow):
        def build_custom_code(self):
            super().build_custom_code()
            try:
                _install_editor_polish(self.visual_rule_editor, UI)
                for label in self.code_page.findChildren(UI.QLabel):
                    text = label.text()
                    if (
                        "自定义规则已改为模块 + 连线" in text
                        or "死亡/昏迷必须使用专用独立规则图" in text
                    ):
                        label.hide()
            except Exception as exc:
                B.add_log("错误", "图形规则界面优化失败", repr(exc))

        def switch_page(self, index):
            super().switch_page(index)
            try:
                if index == self.page_indices.get("code"):
                    self.header_title.setText("图形化规则")
                    self.header_subtitle.setText("")
            except Exception:
                pass

    UI.Window = UserPolishedVisualWindow
