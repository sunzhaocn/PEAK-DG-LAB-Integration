"""Unity-style presentation for the visual rule graph.

This module is presentation-only. It keeps the existing graph format and runtime
semantics, but makes every node self-explanatory: a header, named sockets,
port-type colours and matching wire colours are rendered directly on the node.
"""
from __future__ import annotations

import backend as B
from coyote_app.visual_rules import usability as U

_INSTALLED = False
_MARKER = 0xC07E

_CATEGORY_NAMES = {
    "special": "专用事件",
    "event": "游戏事件",
    "data": "数据",
    "logic": "逻辑",
    "parameter": "输出参数",
    "guard": "自定义保护",
    "control": "控制",
    "action": "动作",
    "note": "备注",
}

_PORT_COLOURS = {
    "condition": "#F2C94C",
    "number": "#56CCF2",
    "intensity": "#EB5757",
    "duration": "#BB6BD9",
    "waveform": "#2D9CDB",
    "modifier": "#F2994A",
    "config": "#6FCF97",
}

_PORT_TYPE_NAMES = {
    "condition": "条件",
    "number": "数值",
    "intensity": "强度",
    "duration": "时间",
    "waveform": "波形",
    "modifier": "修正",
    "config": "配置",
}


def _node_category(data: dict) -> str:
    if U._guard_kind(data):
        return "guard"
    return U._TYPE_CATEGORY.get(str(data.get("type") or ""), "note")


def _port_kind(data: dict, name: str, output: bool) -> str:
    typ = str(data.get("type") or "")
    if not output:
        if typ == "output":
            return {
                "in": "condition",
                "intensity": "intensity",
                "duration": "duration",
                "waveform": "waveform",
                "modifier": "modifier",
                "value": "number",
                "delta": "number",
            }.get(name, "config")
        if typ == "compare" and name in {"a", "b"}:
            return "number"
        if name == "in":
            return "condition"
        return "number"

    if name == "config":
        return {
            "intensity": "intensity",
            "duration": "duration",
            "waveform": "waveform",
            "threshold": "modifier",
            "spike": "modifier",
            "random_waveform": "waveform",
        }.get(typ, "config")
    if name == "value":
        if typ in {
            "death",
            "passed",
            "trigger",
            "changed",
            "compare",
            "and",
            "or",
            "not",
            "edge",
            "cooldown",
            "item",
        }:
            return "condition"
        return "number"
    return "config"


def _port_help(data: dict, name: str, output: bool) -> str:
    typ = str(data.get("type") or "")
    label = U._port_label(data, name, output)
    kind = _PORT_TYPE_NAMES[_port_kind(data, name, output)]

    if not output:
        details = {
            ("output", "in"): "连接一个或多个条件。所有已连接条件都成立时才允许输出。",
            ("output", "intensity"): "连接“强度设置”模块，决定 A/B 通道强度。",
            ("output", "duration"): "连接“持续时间”模块，决定 A/B 通道输出时长。",
            ("output", "waveform"): "连接“波形设置”或“随机波形”模块。",
            ("output", "modifier"): "连接百分比档位、瞬时变化加强等附加修正，可连接多个。",
            ("output", "value"): "把当前百分比/数值传给档位等输出计算。",
            ("output", "delta"): "把本次变化量传给瞬时变化加强等输出计算。",
            ("compare", "a"): "要参与比较的左侧数值。未连接时使用属性中的默认 A。",
            ("compare", "b"): "要参与比较的右侧数值。未连接时使用属性中的默认 B。",
            ("and", "in"): "可连接多个条件；全部为真时结果为真。",
            ("or", "in"): "可连接多个条件；任意一个为真时结果为真。",
            ("not", "in"): "连接一个条件并将结果取反。",
            ("edge", "in"): "连接持续条件，用于检测上升沿/下降沿或持续状态。",
            ("cooldown", "in"): "连接条件；在冷却时间允许时才向后传递。",
        }.get((typ, name), "连接与此端口类型兼容的上游模块。")
        return f"输入｜{label}｜{kind}\n{details}"

    details = {
        ("trigger", "value"): "所选游戏事件发生时输出真，可接到条件端口。",
        ("death", "value"): "角色刚进入死亡状态时输出真。",
        ("passed", "value"): "角色刚进入昏迷状态时输出真。",
        ("telemetry", "value"): "输出指定遥测字段的当前值。",
        ("status", "value"): "输出指定状态的当前百分比。",
        ("item", "value"): "物品名称匹配时输出真。",
        ("changed", "value"): "指定字段发生变化时输出真。",
        ("constant", "value"): "输出属性中设置的固定数值。",
        ("compare", "value"): "A 与 B 满足比较关系时输出真。",
        ("and", "value"): "全部输入条件成立时输出真。",
        ("or", "value"): "任意输入条件成立时输出真。",
        ("not", "value"): "输出输入条件的反值。",
        ("edge", "value"): "输出边沿/持续判断后的条件。",
        ("cooldown", "value"): "冷却允许时输出条件。",
        ("intensity", "config"): "输出 A/B 强度配置，连接到“电击输出”的强度设置。",
        ("duration", "config"): "输出 A/B 持续时间配置。",
        ("waveform", "config"): "输出 A/B 波形配置。",
        ("threshold", "config"): "输出百分比档位附加修正。",
        ("spike", "config"): "输出瞬时变化附加修正。",
        ("random_waveform", "config"): "输出随机波形配置。",
    }.get((typ, name), "把该模块的结果连接到下游模块。")
    return f"输出｜{label}｜{kind}\n{details}"


def _remove_overlay(node) -> None:
    for child in list(node.childItems()):
        try:
            if child.data(_MARKER) == "node-ui":
                scene = child.scene()
                if scene is not None:
                    scene.removeItem(child)
        except Exception:
            pass


def _mark(item):
    item.setData(_MARKER, "node-ui")
    return item


def _simple_text(parent, text, x, y, colour, font, z=30):
    from PySide6.QtGui import QBrush
    from PySide6.QtWidgets import QGraphicsSimpleTextItem

    item = _mark(QGraphicsSimpleTextItem(str(text), parent))
    item.setBrush(QBrush(colour))
    item.setFont(font)
    item.setPos(x, y)
    item.setZValue(z)
    return item


def _render_node(node, UI) -> None:
    from PySide6.QtGui import QBrush, QFont, QPen
    from PySide6.QtWidgets import QGraphicsRectItem, QGraphicsTextItem

    data = node.data if isinstance(getattr(node, "data", None), dict) else {}
    category = _node_category(data)
    header_bg, accent_hex = U._CATEGORY_COLOURS[category]
    accent = UI.QColor(accent_hex)

    _remove_overlay(node)

    # Hide the legacy title/labels. They remain attached for compatibility with
    # older policy wrappers, but the card below is the authoritative rendering.
    for child in node.childItems():
        if isinstance(child, QGraphicsTextItem):
            child.setVisible(False)

    ins = getattr(node, "ins", {})
    outs = getattr(node, "outs", {})
    rows = max(len(ins), len(outs), 1)
    width = 344 if str(data.get("type")) == "output" else 316
    header_h = 40
    row_h = 30
    row_start = 66
    height = max(108, row_start + rows * row_h + 14)

    node.setRect(0, 0, width, height)
    node.setBrush(QBrush(UI.QColor("#151922")))
    node.setPen(QPen(accent, 2.0))
    node.setToolTip(U._NODE_HELP.get(str(data.get("type") or ""), U._node_title(data)))

    header = _mark(QGraphicsRectItem(0, 0, width, header_h, node))
    header.setBrush(QBrush(UI.QColor(header_bg)))
    header.setPen(QPen(accent, 0.8))
    header.setZValue(10)

    title_font = QFont()
    title_font.setBold(True)
    title_font.setPointSize(10)
    title = _simple_text(node, U._node_title(data), 12, 9, UI.QColor("#FFFFFF"), title_font, 40)
    title.setToolTip(node.toolTip())

    tag_font = QFont()
    tag_font.setPointSize(8)
    tag = _simple_text(
        node,
        _CATEGORY_NAMES.get(category, "模块"),
        0,
        11,
        UI.QColor("#D5DEEA"),
        tag_font,
        40,
    )
    tag.setPos(width - tag.boundingRect().width() - 12, 12)

    section_font = QFont()
    section_font.setBold(True)
    section_font.setPointSize(8)
    if ins:
        _simple_text(node, "输入", 14, 45, UI.QColor("#7F8A9B"), section_font, 35)
    if outs:
        out_header = _simple_text(node, "输出", 0, 45, UI.QColor("#7F8A9B"), section_font, 35)
        out_header.setPos(width - out_header.boundingRect().width() - 14, 45)

    port_font = QFont()
    port_font.setPointSize(9)

    input_items = list(ins.items())
    output_items = list(outs.items())
    for row in range(rows):
        y = row_start + row * row_h
        row_bg = _mark(QGraphicsRectItem(8, y - 4, width - 16, row_h - 3, node))
        row_bg.setBrush(QBrush(UI.QColor("#11151D" if row % 2 == 0 else "#131821")))
        row_bg.setPen(QPen(UI.QColor("#202735"), 0.5))
        row_bg.setZValue(12)

        if row < len(input_items):
            name, port = input_items[row]
            label = U._port_label(data, name, False)
            kind = _port_kind(data, name, False)
            colour = UI.QColor(_PORT_COLOURS[kind])
            port.setRect(-6, -6, 12, 12)
            port.setPos(0, y + 9)
            port.setBrush(QBrush(colour))
            port.setPen(QPen(UI.QColor("#EAF0F7"), 1.0))
            port.setZValue(60)
            port.setToolTip(_port_help(data, name, False))
            text = _simple_text(node, label, 17, y + 1, UI.QColor("#E5EBF3"), port_font, 40)
            text.setToolTip(port.toolTip())

        if row < len(output_items):
            name, port = output_items[row]
            label = U._port_label(data, name, True)
            kind = _port_kind(data, name, True)
            colour = UI.QColor(_PORT_COLOURS[kind])
            port.setRect(-6, -6, 12, 12)
            port.setPos(width, y + 9)
            port.setBrush(QBrush(colour))
            port.setPen(QPen(UI.QColor("#EAF0F7"), 1.0))
            port.setZValue(60)
            port.setToolTip(_port_help(data, name, True))
            text = _simple_text(node, label, 0, y + 1, UI.QColor("#E5EBF3"), port_font, 40)
            text.setPos(width - text.boundingRect().width() - 17, y + 1)
            text.setToolTip(port.toolTip())


def _wire_colour(edge, UI):
    try:
        port = edge.a
        data = port.node.data if isinstance(port.node.data, dict) else {}
        kind = _port_kind(data, str(port.name), True)
        return UI.QColor(_PORT_COLOURS[kind])
    except Exception:
        return UI.QColor("#7AA2FF")


def _install_node_ui(editor, UI) -> None:
    from PySide6.QtGui import QPainter, QPen

    cls = type(editor)
    if getattr(cls, "_coyote_unity_node_ui_installed", False):
        for node in editor.nodes.values():
            _render_node(node, UI)
        return
    cls._coyote_unity_node_ui_installed = True

    original_rebuild = cls.rebuild

    def rebuild(self):
        result = original_rebuild(self)
        for node in self.nodes.values():
            _render_node(node, UI)
        for edge in self.edges:
            edge.setPen(QPen(_wire_colour(edge, UI), 2.5))
            edge.update_path()
        return result

    cls.rebuild = rebuild

    try:
        editor.view.setRenderHints(
            editor.view.renderHints()
            | QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.TextAntialiasing
        )
    except Exception:
        pass

    try:
        editor.palette.setMinimumWidth(250)
    except Exception:
        pass

    editor.rebuild()


def install_ui(UI) -> None:
    """Install after the base visual usability layer."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    BaseWindow = UI.Window

    class NodeCardVisualWindow(BaseWindow):
        def build_custom_code(self):
            super().build_custom_code()
            try:
                _install_node_ui(self.visual_rule_editor, UI)
            except Exception as exc:
                B.add_log("错误", "图形规则节点界面安装失败", repr(exc))

    UI.Window = NodeCardVisualWindow
