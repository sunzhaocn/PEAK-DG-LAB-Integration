import ast
import json
import os
import re
import random
import shutil
import socket
import subprocess
import tempfile
import urllib.request
import zipfile
import sys
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from urllib.parse import quote

import qrcode
import websocket



# ============================================================
# 1. 基础配置
# ============================================================

PEAK_HOST = "127.0.0.1"
DEFAULT_PEAK_PORT = 8765
PEAK_PORT = DEFAULT_PEAK_PORT
PEAK_OFFLINE = 5.0

DG_HOST = "127.0.0.1"
DEFAULT_DG_PORT = 9998
DG_PORT = DEFAULT_DG_PORT
DG_URL = f"ws://{DG_HOST}:{DG_PORT}"

network_settings = {
    "peak_port": DEFAULT_PEAK_PORT,
    "dg_port": DEFAULT_DG_PORT,
    "peak_game_dir": "",
}

# -------------------- PEAK / BepInEx --------------------
PEAK_STEAM_APP_ID = "3527290"

# Thunderstore 当前 PEAK 专用预配置包。
# 包版本 5.4.75301，内部基于 BepInEx 5.4.23.3。
BEPINEX_PEAK_PACKAGE_VERSION = "5.4.75301"
BEPINEX_CORE_VERSION = "5.4.23.3"
BEPINEX_PEAK_DOWNLOAD_URL = (
    "https://thunderstore.io/package/download/"
    "BepInEx/BepInExPack_PEAK/"
    f"{BEPINEX_PEAK_PACKAGE_VERSION}/"
)

# Coyote 插件的正式程序集/文件名。
# 旧版曾使用 hot.Coyote.dll；仅用于升级兼容检测和迁移，
# 新安装与发布包统一使用 Coyote.dll。
COYOTE_PLUGIN_FILENAME = "Coyote.dll"
COYOTE_PLUGIN_LEGACY_FILENAMES = (
    "hot.Coyote.dll",
)

GUI_INTENSITY_MAX = 200
GUI_DURATION_MAX_MS = 10000000

# 自动规则持续时间特殊值：
# -1 = 条件成立时持续续播。
# 底层仍只发送有限片段，不把 -1 直接发给设备。
DURATION_CONTINUOUS = -1
CONTINUOUS_SEGMENT_MS = 1000

# -------------------- 现代深色 UI 配色 --------------------
APP_BG = "#090D13"
APP_PANEL = "#0F1623"
APP_CARD = "#151E2E"
APP_CARD_ALT = "#1A2538"
APP_BORDER = "#263449"
APP_TEXT = "#E7EDF7"
APP_MUTED = "#8FA0B8"
APP_ACCENT = "#5B8CFF"
APP_ACCENT_ACTIVE = "#75A0FF"
APP_DANGER = "#E05260"
APP_DANGER_ACTIVE = "#F06A76"
APP_SUCCESS = "#46C58A"

appearance = {
    "background_image": "",
    "background_opacity": 0.32,
}

STATUS_TRANSLATIONS = {
    "Injury": "受伤",
    "Hunger": "饥饿",
    "Cold": "寒冷",
    "Poison": "中毒",
    "Crab": "螃蟹",
    "Curse": "诅咒",
    "Drowsy": "困倦",
    "Weight": "负重",
    "Hot": "高温",
    "Thorns": "刺伤",
    "Spores": "孢子",
    "Web": "蛛网",
    "Arrow": "箭伤",
    "Petrify": "石化",
    "Petrified": "石化",
    "Petrification": "石化",
    "FlyTrap": "捕蝇草",
}

STATUS_ORDER = [
    ("Injury", "受伤"),
    ("Hunger", "饥饿"),
    ("Cold", "寒冷"),
    ("Poison", "中毒"),
    ("Crab", "螃蟹"),
    ("Curse", "诅咒"),
    ("Drowsy", "困倦"),
    ("Weight", "负重"),
    ("Hot", "高温"),
    ("Thorns", "刺伤"),
    ("Spores", "孢子"),
    ("Web", "蛛网"),
    ("Arrow", "箭伤"),
    ("Petrify", "石化"),
    ("FlyTrap", "捕蝇草"),
]

# 这里恢复你之前项目里已经使用过的波形。
# 如果以后继续增加官方波形，只需要继续往这个字典里加。
TIER_WAVEFORM_INHERIT = "沿用基础波形"

COYOTE_WAVEFORMS = {
    "气泡": [
        "2D2D2D2D00000000",
        "2D2D2D2D64646464",
    ],
    "挤压": [
        "0A0A0A0A00000000",
        "0A0A0A0A64646464",
    ],
    "攀登": [
        "3030303032323232",
        "282828283C3C3C3C",
        "2020202046464646",
        "1919191950505050",
        "111111115A5A5A5A",
        "0A0A0A0A64646464",
    ],
    "树荫": [
        "6464646464646464",
        "6464646464646464",
    ],
    "律动": [
        "0A0A0A0A00000000",
        "0A0A0A0A32323232",
        "0A0A0A0A64646464",
        "0A0A0A0A00000000",
        "0A0A0A0A32323232",
        "0A0A0A0A64646464",
        "1919191964646464",
        "1D1D1D1D64646464",
        "2222222264646464",
        "2626262664646464",
        "2B2B2B2B64646464",
        "0A0A0A0A00000000",
        "0A0A0A0A00000000",
    ],
    "电波": [
        "0A0A0A0A64646464",
        "1717171764646464",
        "2424242464646464",
        "3232323264646464",
        "0A0A0A0A00000000",
        "0A0A0A0A64646464",
        "0A0A0A0A00000000",
        "0A0A0A0A64646464",
        "0A0A0A0A00000000",
        "0A0A0A0A64646464",
        "0A0A0A0A00000000",
        "0A0A0A0A64646464",
        "0A0A0A0A00000000",
    ],
    "舞步": [
        "0A0A0A0A00000000",
        "0A0A0A0A00000000",
        "0A0A0A0A64646464",
        "0A0A0A0A00000000",
        "0A0A0A0A00000000",
        "0A0A0A0A64646464",
        "0A0A0A0A00000000",
        "0A0A0A0A00000000",
        "0A0A0A0A64646464",
        "0A0A0A0A64646464",
        "0A0A0A0A64646464",
        "0A0A0A0A00000000",
        "0A0A0A0A00000000",
        "0A0A0A0A64646464",
        "0A0A0A0A64646464",
        "0A0A0A0A64646464",
    ],
    "呼吸": [
        "0A0A0A0A00000000",
        "0A0A0A0A14141414",
        "0A0A0A0A28282828",
        "0A0A0A0A3C3C3C3C",
        "0A0A0A0A50505050",
        "0A0A0A0A64646464",
        "0A0A0A0A64646464",
        "0A0A0A0A64646464",
        "0A0A0A0A00000000",
        "0A0A0A0A00000000",
        "0A0A0A0A00000000",
        "0A0A0A0A00000000",
    ],
    "脉冲": [
        "0A0A0A0A64646464",
        "0D0D0D0D64646464",
        "1010101064646464",
        "1313131364646464",
        "1616161664646464",
        "1C1C1C1C64646464",
        "2525252564646464",
        "2E2E2E2E64646464",
        "3737373764646464",
        "4040404064646464",
        "4E4E4E4E64646464",
        "6C6C6C6C64646464",
        "7979797964646464",
        "8686868664646464",
        "9393939364646464",
        "A0A0A0A064646464",
    ],
}


# 内置波形名称固定保存，防止自定义波形覆盖官方/预置波形。
BUILTIN_WAVEFORM_NAMES = tuple(
    COYOTE_WAVEFORMS.keys()
)

# 用户自定义波形。
# 运行时会同步合并进 COYOTE_WAVEFORMS，
# 因此原来的发送代码无需另外分支。
custom_waveforms = {}


def validate_waveform_name(name):
    name = str(name or "").strip()

    if not name:
        return False, "波形名称不能为空"

    if len(name) > 30:
        return False, "波形名称最多 30 个字符"

    if name == TIER_WAVEFORM_INHERIT:
        return False, f"不能使用保留名称：{TIER_WAVEFORM_INHERIT}"

    return True, name


def normalize_waveform_frame(frame):
    """
    DG-LAB 当前项目使用的波形帧是 16 位十六进制字符串。
    例如：
        2D2D2D2D64646464
    """
    value = str(frame or "").strip().upper()

    # 允许用户为了可读性输入空格。
    value = value.replace(" ", "")

    if len(value) != 16:
        return None

    if any(
        ch not in "0123456789ABCDEF"
        for ch in value
    ):
        return None

    return value


def parse_waveform_text(raw_text):
    """
    自定义波形编辑器：
      - 一行一个 16 位 HEX 帧
      - 空行忽略
      - 也允许逗号分隔
    """
    raw_text = str(raw_text or "")

    pieces = []

    for line in raw_text.splitlines():
        # 一行里也允许用逗号写多帧
        for part in line.split(","):
            part = part.strip()

            if part:
                pieces.append(part)

    if not pieces:
        return False, "至少需要 1 个波形帧"

    frames = []

    for index, piece in enumerate(
        pieces,
        start=1,
    ):
        frame = normalize_waveform_frame(
            piece
        )

        if frame is None:
            return (
                False,
                (
                    f"第 {index} 帧格式错误：{piece}\n"
                    "每帧必须正好是 16 位十六进制字符。"
                ),
            )

        frames.append(frame)

    # 防止误粘贴超大量数据造成 GUI / 发送异常。
    if len(frames) > 512:
        return (
            False,
            "单个自定义波形最多 512 帧",
        )

    return True, frames


def waveform_names():
    return list(
        COYOTE_WAVEFORMS.keys()
    )


def tier_waveform_names():
    return [
        TIER_WAVEFORM_INHERIT,
        *waveform_names(),
    ]


def install_custom_waveforms(items):
    """
    从配置文件加载自定义波形，并合并进 COYOTE_WAVEFORMS。
    """
    custom_waveforms.clear()

    # 先移除旧的自定义项，保留内置项。
    for name in list(
        COYOTE_WAVEFORMS.keys()
    ):
        if name not in BUILTIN_WAVEFORM_NAMES:
            COYOTE_WAVEFORMS.pop(
                name,
                None,
            )

    if not isinstance(items, dict):
        return

    for raw_name, raw_frames in items.items():
        ok, name_or_error = validate_waveform_name(
            raw_name
        )

        if not ok:
            continue

        name = name_or_error

        if name in BUILTIN_WAVEFORM_NAMES:
            # 不允许配置文件覆盖内置波形。
            continue

        if not isinstance(
            raw_frames,
            list,
        ):
            continue

        frames = []

        for raw_frame in raw_frames:
            frame = normalize_waveform_frame(
                raw_frame
            )

            if frame is None:
                frames = []
                break

            frames.append(frame)

        if not frames:
            continue

        custom_waveforms[
            name
        ] = frames

        COYOTE_WAVEFORMS[
            name
        ] = frames


# ============================================================
# 2. 路径
# ============================================================

if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).resolve().parent
    SOURCE_DIR = ROOT / "src" / "Coyote"

    # 打包后的便携目录如果没有 src/Coyote，
    # 则允许 md/language 与 EXE 同级。
    if not SOURCE_DIR.exists():
        SOURCE_DIR = ROOT
else:
    SOURCE_DIR = Path(__file__).resolve().parent
    ROOT = SOURCE_DIR.parents[1]

SERVER_DIR = ROOT / "dglab-websocket-server-main"
BUN = SERVER_DIR / "bun.exe"
V4_SERVER = SERVER_DIR / "v4-server.ts"

LOG_DIR = ROOT / "logs"
SERVER_LOG_FILE = LOG_DIR / "dglab-server.log"
EVENT_LOG_FILE = LOG_DIR / "coyote-events.log"
CONFIG_FILE = ROOT / "coyote_gui_config.json"

CUSTOM_RULE_DIR = ROOT / "custom_rules"

# 打包版把可安装插件放在：
#   Coyote.exe 同级/plugin/Coyote.dll
# 源码版也使用该目录作为预编译插件兜底。
PLUGIN_BUNDLE_DIR = ROOT / "plugin"
BUNDLED_COYOTE_DLL = (
    PLUGIN_BUNDLE_DIR
    / COYOTE_PLUGIN_FILENAME
)

# 用户当前目录结构：
#   src/Coyote/md/
#
# 同时兼容旧版：
#   Coyote/docs/
NEW_DOC_DIR = SOURCE_DIR / "md"
LEGACY_DOC_DIR = ROOT / "docs"

if NEW_DOC_DIR.exists() or not LEGACY_DOC_DIR.exists():
    DOC_DIR = NEW_DOC_DIR
else:
    DOC_DIR = LEGACY_DOC_DIR

CUSTOM_RULE_DOC_FILE = DOC_DIR / "自定义规则开发指南.md"
APP_INTRO_DOC_FILE = DOC_DIR / "Coyote软件介绍.md"

CUSTOM_RULE_EXAMPLE_FILE = (
    CUSTOM_RULE_DIR
    / "example_speed_climb.py"
)


# ============================================================
# 3. 全局状态
# ============================================================

stop_event = threading.Event()

dg_lock = threading.Lock()
peak_lock = threading.Lock()
rule_lock = threading.Lock()
log_lock = threading.Lock()
ws_send_lock = threading.Lock()

latest_peak = None
previous_peak = None
last_peak_time = 0.0
peak_was_online = False

dg_ws = None
dg_process = None
dg_log = None
udp_socket = None

dg = {
    "server": "未连接",
    "error": "",
    "controller_id": None,
    "app_id": None,
    "slot_id": None,
    "device_name": None,
    "device_type": None,
    "has_device": None,
    "connect_state": None,
}

event_logs = deque()
log_revision = 0

# 单次规则输出期间，持续规则不得覆盖它。
single_output_guard_until = 0.0
output_count = 0
last_output = None

master_output_enabled = False

last_trigger_time = {}
last_game_log_time = {}

custom_rule_lock = threading.Lock()
custom_rules = {}
custom_rule_runtime = {}

# -------------------- 手动设备持续会话 --------------------
manual_session_lock = threading.Lock()
manual_session = {
    "generation": 0,
    "active": False,
    "a": None,
    "b": None,
}

# PEAK 进程检测缓存。
# Qt UI 每 100ms 刷新一次，但 tasklist 没必要跟着 100ms 查询。
peak_process_cache = {
    "running": False,
    "checked_at": 0.0,
}

PEAK_PROCESS_CHECK_INTERVAL = 2.0


# ============================================================
# 4. 规则配置
# ============================================================

RULE_META = [
    ("hp", "血量下降", None, "血量下降时触发"),
    ("dead", "死亡", None, "否 → 是时触发"),
    ("passedOut", "昏迷", None, "否 → 是时触发"),

    ("staminaUse", "体力消耗", None, "体力下降达到阈值时触发"),
    ("speedBelow", "速度低于阈值", None, "速度从阈值以上进入阈值以下时触发"),
    ("speedAbove", "速度高于阈值", None, "速度从阈值以下进入阈值以上时触发"),
    ("jump", "跳跃", None, "检测到跳跃时触发"),
    ("climbStart", "开始攀爬", None, "否 → 是时触发"),
    ("crouchStart", "蹲下", None, "否 → 是时触发"),

    ("heldItem", "拿起手持物", None, "手持物切换为匹配物品时触发"),
    ("backpackItem", "背包装入物品", None, "背包中新增加匹配物品时触发"),

    # 状态型物品规则：
    # 与上面两个“变化事件”规则分开。
    ("heldState", "当前手持匹配物品", None, "当前手持物进入匹配条件时触发"),
    ("backpackState", "背包存在匹配物品", None, "背包从无匹配物品进入有匹配物品时触发"),

    ("Injury", "受伤", 0, "百分比增加时触发"),
    ("Hunger", "饥饿", 1, "百分比增加时触发"),
    ("Cold", "寒冷", 2, "百分比增加时触发"),
    ("Poison", "中毒", 3, "百分比增加时触发"),
    ("Crab", "螃蟹", 4, "百分比增加时触发"),
    ("Curse", "诅咒", 5, "百分比增加时触发"),
    ("Drowsy", "困倦", 6, "百分比增加时触发"),
    ("Weight", "负重", 7, "百分比增加时触发"),
    ("Hot", "高温", 8, "百分比增加时触发"),
    ("Thorns", "刺伤", 9, "百分比增加时触发"),
    ("Spores", "孢子", 10, "百分比增加时触发"),
    ("Web", "蛛网", 11, "百分比增加时触发"),
    ("Arrow", "箭伤", 12, "百分比增加时触发"),
    ("Petrify", "石化", 13, "百分比增加时触发"),
    ("FlyTrap", "捕蝇草", 14, "百分比增加时触发"),
]

RULE_GROUPS = [
    ("survival", "生命状态", ["hp", "dead", "passedOut"]),
    (
        "action",
        "动作 / 速度 / 体力",
        [
            "staminaUse",
            "speedBelow",
            "speedAbove",
            "jump",
            "climbStart",
            "crouchStart",
        ],
    ),
    (
        "items",
        "物品 / 背包",
        [
            "heldItem",
            "backpackItem",
            "heldState",
            "backpackState",
        ],
    ),
    (
        "afflictions",
        "异常状态",
        [
            "Injury", "Hunger", "Cold", "Poison", "Crab",
            "Curse", "Drowsy", "Weight", "Hot", "Thorns",
            "Spores", "Web", "Arrow", "Petrify", "FlyTrap",
        ],
    ),
]

RULE_GROUP_BY_KEY = {
    key: group_id
    for group_id, _, keys in RULE_GROUPS
    for key in keys
}


RULE_META_BY_KEY = {
    key: {
        "display": display,
        "index": index,
        "trigger": trigger,
    }
    for key, display, index, trigger in RULE_META
}


def rule_supports_percentage_tiers(key):
    """
    有百分数值的事件才支持“低于 X% 增加强度”：
      - hp
      - 15 个 statuses

    dead / passedOut 是布尔事件，不做百分比阶梯。
    """
    if key in (
        "hp",
        "staminaUse",
    ):
        return True

    meta = RULE_META_BY_KEY.get(key)

    return bool(
        meta
        and meta["index"] is not None
    )


def default_rule():
    return {
        # 安全默认：所有自动规则默认关闭。
        "enabled": False,

        # 基础输出
        "intensity_a": 5,
        "intensity_b": 5,
        "play_time_a": 5000,
        "play_time_b": 5000,
        "waveform_a": "脉冲",
        "waveform_b": "脉冲",
        "cooldown": 2.0,

        # 触发模式：
        # single = 事件/状态变化时单次触发
        # repeat = 条件持续成立时，按冷却重复触发有限时长输出
        #
        # repeat 并不是无限时长 device.op。
        "trigger_mode": "single",

        # 特殊规则的触发阈值。
        # staminaUse 使用：体力单次至少下降多少个百分点才触发。
        "trigger_delta": 1.0,

        # 物品规则筛选；空或 * 代表任意物品。
        "item_filter": "",

        # 速度规则阈值。
        # 单位沿用 PEAK telemetry 的 speed 数值，不自行换算。
        "speed_threshold": 1.0,

        # 每条规则自己的最大强度限制。
        # 最终强度 = min(基础强度 + 阶梯增量, 规则最大值, GUI硬上限)
        "max_intensity_a": 10,
        "max_intensity_b": 10,

        # 随机强度：开启后，每次触发先在范围内随机生成“基础强度”，
        # 再叠加百分比档位 / 瞬时大变化加成，最终仍受规则最大值与 GUI 硬上限限制。
        "random_intensity": False,
        "random_min_a": 1,
        "random_max_a": 5,
        "random_min_b": 1,
        "random_max_b": 5,

        # 瞬时大变化加强：仅对有百分比变化量的规则生效。
        # 例如 HP 100 -> 0，变化量为 100%，达到阈值后额外增加档位。
        "spike_enabled": False,
        # 旧版单阈值字段继续保留，用于自动迁移旧配置。
        "spike_delta": 50.0,
        "spike_add_a": 5,
        "spike_add_b": 5,
        # 新版：用户可添加任意数量的瞬时变化档位。
        # delta = 单次变化量百分比；A/B 额外值可分别设随机范围。
        # 多档同时命中时只采用 delta 最大（最严重）的一档，不累加。
        "spike_tiers": [],

        # 动态强度 / 波形阶梯。
        #
        # 示例：
        # [
        #   {
        #       "below": 80,
        #       "add_a": 1,
        #       "add_b": 1,
        #       "waveform_a": "沿用基础波形",
        #       "waveform_b": "沿用基础波形",
        #   },
        #   {
        #       "below": 20,
        #       "add_a": 5,
        #       "add_b": 5,
        #       "waveform_a": "脉冲",
        #       "waveform_b": "气泡",
        #   },
        # ]
        #
        # 当前值 10% 时若多个条件同时满足，只取最严重的一档；
        # 强度不会累加，波形也只采用该档位指定的覆盖值。
        "thresholds": [],
    }


rules = {
    key: default_rule()
    for key, _, _, _ in RULE_META
}


def clamp_int(value):
    try:
        return max(
            0,
            min(
                GUI_INTENSITY_MAX,
                int(float(value)),
            ),
        )
    except Exception:
        return 0


def is_continuous_duration(value):
    try:
        return int(float(value)) == DURATION_CONTINUOUS
    except Exception:
        return False


def clamp_duration(
    value,
    allow_continuous=True,
):
    try:
        parsed = int(float(value))

        if (
            allow_continuous
            and parsed == DURATION_CONTINUOUS
        ):
            return DURATION_CONTINUOUS

        return max(
            100,
            min(
                GUI_DURATION_MAX_MS,
                parsed,
            ),
        )
    except Exception:
        return 5000


def clamp_finite_duration(value):
    return clamp_duration(
        value,
        allow_continuous=False,
    )


def resolve_rule_duration_ms(value):
    # 真正发送给 DG-LAB 的时长始终是有限值。
    if is_continuous_duration(value):
        return CONTINUOUS_SEGMENT_MS

    return clamp_finite_duration(value)


def rule_has_continuous_duration(cfg):
    return (
        is_continuous_duration(
            cfg.get("play_time_a", 5000)
        )
        or is_continuous_duration(
            cfg.get("play_time_b", 5000)
        )
    )


def clamp_cooldown(value):
    try:
        return max(
            0.0,
            min(
                60.0,
                float(value),
            ),
        )
    except Exception:
        return 2.0


def clamp_percent(value):
    try:
        return max(
            0.0,
            min(
                100.0,
                float(value),
            ),
        )
    except Exception:
        return 50.0


def normalize_intensity_range(min_value, max_value, default_min=0, default_max=5):
    low = clamp_int(min_value if min_value is not None else default_min)
    high = clamp_int(max_value if max_value is not None else default_max)
    if low > high:
        low, high = high, low
    return low, high


def normalize_status_name(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def resolve_status_index(packet, key, fallback_index=None):
    """
    优先使用 DLL 实时发送的 statusNames 解析状态位置。
    这样 PEAK 增删/调整 STATUSTYPE 枚举后，规则不会继续死用旧下标。
    老 DLL 没有 statusNames 时才回退旧版固定下标。
    """
    packet = packet if isinstance(packet, dict) else {}
    names = packet.get("statusNames", [])
    target = normalize_status_name(key)

    aliases = {
        "Petrify": {"petrify", "petrified", "petrification", "petrifying"},
    }
    expected = {normalize_status_name(x) for x in aliases.get(key, {key})}
    expected.add(target)

    if isinstance(names, list) and names:
        normalized = [normalize_status_name(name) for name in names]

        for index, raw in enumerate(normalized):
            if raw in expected:
                return index

        # 石化在不同 PEAK 版本中可能使用 Petrify/Petrified/Petrification。
        if key == "Petrify":
            for index, raw in enumerate(normalized):
                if raw.startswith("petrif"):
                    return index

        # 通用兼容：枚举名称带 Status/Affliction 等前后缀时仍可匹配。
        if target:
            for index, raw in enumerate(normalized):
                if raw and (raw.endswith(target) or target.endswith(raw)):
                    return index

    try:
        fallback = int(fallback_index)
    except Exception:
        return None

    statuses = packet.get("statuses", [])
    if isinstance(statuses, list) and 0 <= fallback < len(statuses):
        return fallback
    return None


def status_percent_for_rule(packet, key, fallback_index=None):
    packet = packet if isinstance(packet, dict) else {}
    statuses = packet.get("statuses", [])
    if not isinstance(statuses, list):
        return None

    index = resolve_status_index(packet, key, fallback_index)
    if index is None or index >= len(statuses):
        return None

    try:
        return round(max(0.0, min(100.0, float(statuses[index]) * 100.0)), 1)
    except Exception:
        return None


def peak_is_incapacitated(packet=None):
    if packet is None:
        with peak_lock:
            packet = dict(latest_peak) if isinstance(latest_peak, dict) else {}
    if not isinstance(packet, dict) or packet.get("hasCharacter", True) is False:
        return False
    return bool(packet.get("dead", False) or packet.get("passedOut", False))


def normalize_thresholds(items):
    """
    清理配置文件 / GUI 中的动态强度阶梯。

    不做累加。运行时会选择：
        当前值 < 阈值
    的所有候选中“阈值最小”的一档。
    """
    if not isinstance(items, list):
        return []

    result = []

    for item in items:
        if not isinstance(item, dict):
            continue

        waveform_a = str(
            item.get(
                "waveform_a",
                TIER_WAVEFORM_INHERIT,
            )
            or TIER_WAVEFORM_INHERIT
        )

        waveform_b = str(
            item.get(
                "waveform_b",
                TIER_WAVEFORM_INHERIT,
            )
            or TIER_WAVEFORM_INHERIT
        )

        valid_tier_waveforms = set(
            tier_waveform_names()
        )

        if waveform_a not in valid_tier_waveforms:
            waveform_a = TIER_WAVEFORM_INHERIT

        if waveform_b not in valid_tier_waveforms:
            waveform_b = TIER_WAVEFORM_INHERIT

        result.append({
            "below": clamp_percent(
                item.get("below", 50.0)
            ),
            "add_a": clamp_int(
                item.get("add_a", 0)
            ),
            "add_b": clamp_int(
                item.get("add_b", 0)
            ),
            "waveform_a": waveform_a,
            "waveform_b": waveform_b,
        })

    # 高阈值 -> 低阈值显示，更符合阶梯阅读顺序。
    result.sort(
        key=lambda x: x["below"],
        reverse=True,
    )

    return result



def normalize_spike_tiers(items):
    """
    清理“瞬时大变化加强”的自定义档位。

    每档格式：
        delta: 单次变化量至少多少百分比
        min_a/max_a: A 通道额外增加的随机范围
        min_b/max_b: B 通道额外增加的随机范围

    多档命中时运行时只取 delta 最大的一档，不累计。
    """
    if not isinstance(items, list):
        return []

    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            delta = max(0.1, min(100.0, float(item.get("delta", 50.0))))
        except Exception:
            delta = 50.0

        min_a, max_a = normalize_intensity_range(
            item.get("min_a", item.get("add_a", 0)),
            item.get("max_a", item.get("add_a", 0)),
            0, 0,
        )
        min_b, max_b = normalize_intensity_range(
            item.get("min_b", item.get("add_b", 0)),
            item.get("max_b", item.get("add_b", 0)),
            0, 0,
        )
        result.append({
            "delta": delta,
            "min_a": min_a,
            "max_a": max_a,
            "min_b": min_b,
            "max_b": max_b,
        })

    # 小变化 -> 大变化，便于界面阅读；选择时会取命中阈值最大的档。
    result.sort(key=lambda x: x["delta"])
    return result


def spike_tiers_from_config(cfg):
    """读取新版档位；没有新版档位时自动兼容旧版单阈值配置。"""
    tiers = normalize_spike_tiers(cfg.get("spike_tiers", []))
    if tiers:
        return tiers

    if bool(cfg.get("spike_enabled", False)):
        try:
            delta = max(0.1, min(100.0, float(cfg.get("spike_delta", 50.0))))
        except Exception:
            delta = 50.0
        a = clamp_int(cfg.get("spike_add_a", 0))
        b = clamp_int(cfg.get("spike_add_b", 0))
        return [{
            "delta": delta,
            "min_a": a, "max_a": a,
            "min_b": b, "max_b": b,
        }]
    return []


def validate_port(value, default):
    try:
        value = int(value)
    except Exception:
        return int(default)
    if not 1024 <= value <= 65535:
        return int(default)
    return value


def apply_network_settings():
    global PEAK_PORT, DG_PORT, DG_URL

    PEAK_PORT = validate_port(
        network_settings.get("peak_port", DEFAULT_PEAK_PORT),
        DEFAULT_PEAK_PORT,
    )
    DG_PORT = validate_port(
        network_settings.get("dg_port", DEFAULT_DG_PORT),
        DEFAULT_DG_PORT,
    )

    if DG_PORT == PEAK_PORT:
        DG_PORT = DEFAULT_DG_PORT if DEFAULT_DG_PORT != PEAK_PORT else 9997

    network_settings["peak_port"] = PEAK_PORT
    network_settings["dg_port"] = DG_PORT
    DG_URL = f"ws://{DG_HOST}:{DG_PORT}"


def _parse_steam_library_paths(vdf_path):
    paths = []
    try:
        text = Path(vdf_path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return paths

    for match in re.finditer(r'"path"\s+"([^"]+)"', text, re.IGNORECASE):
        raw = match.group(1).replace("\\\\", "\\").strip()
        if raw:
            paths.append(Path(raw))

    for match in re.finditer(r'"\d+"\s+"([A-Za-z]:\\\\[^"]+)"', text):
        raw = match.group(1).replace("\\\\", "\\").strip()
        if raw:
            paths.append(Path(raw))
    return paths


def _candidate_steam_roots():
    roots = []
    def add(path):
        if not path:
            return
        p = Path(str(path))
        key = str(p).lower()
        if key and all(str(existing).lower() != key for existing in roots):
            roots.append(p)

    if sys.platform.startswith("win"):
        try:
            import winreg
            entries = [
                (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
            ]
            for hive, key_name, value_name in entries:
                try:
                    with winreg.OpenKey(hive, key_name) as key:
                        raw, _ = winreg.QueryValueEx(key, value_name)
                    add(raw)
                except Exception:
                    pass
        except Exception:
            pass

    for env_name in ("ProgramFiles(x86)", "ProgramFiles"):
        base = os.environ.get(env_name)
        if base:
            add(Path(base) / "Steam")
    add(Path(r"C:\Program Files (x86)\Steam"))
    add(Path(r"C:\Program Files\Steam"))
    return roots


def _fixed_peak_candidates():
    candidates = []
    if not sys.platform.startswith("win"):
        return candidates
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        drive = Path(f"{letter}:\\")
        if not drive.exists():
            continue
        candidates.extend([
            drive / "steam" / "steamapps" / "common" / "PEAK",
            drive / "Steam" / "steamapps" / "common" / "PEAK",
            drive / "SteamLibrary" / "steamapps" / "common" / "PEAK",
            drive / "Games" / "Steam" / "steamapps" / "common" / "PEAK",
        ])
    return candidates


def find_peak_game_dir_from_steam():
    if not sys.platform.startswith("win"):
        return None

    libraries = []
    def add_library(path):
        try:
            p = Path(path)
            key = str(p).lower()
            if not any(str(x).lower() == key for x in libraries):
                libraries.append(p)
        except Exception:
            pass

    for steam_root in _candidate_steam_roots():
        add_library(steam_root)
        vdf = steam_root / "steamapps" / "libraryfolders.vdf"
        for library in _parse_steam_library_paths(vdf):
            add_library(library)

    for library in libraries:
        direct = library / "steamapps" / "common" / "PEAK"
        if (direct / "PEAK.exe").exists():
            return direct

        manifest = library / "steamapps" / f"appmanifest_{PEAK_STEAM_APP_ID}.acf"
        if not manifest.exists():
            continue
        try:
            text = manifest.read_text(encoding="utf-8", errors="ignore")
            match = re.search(r'"installdir"\s+"([^"]+)"', text, re.IGNORECASE)
            if match:
                game_dir = library / "steamapps" / "common" / match.group(1)
                if (game_dir / "PEAK.exe").exists():
                    return game_dir
        except Exception:
            pass

    for candidate in _fixed_peak_candidates():
        if (candidate / "PEAK.exe").exists():
            return candidate
    return None


def get_peak_game_dir():
    configured = str(network_settings.get("peak_game_dir", "") or "").strip()
    if configured:
        path = Path(configured)
        if path.exists() and (path / "PEAK.exe").exists():
            return path

    if not sys.platform.startswith("win"):
        return None

    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        command = (
            "(Get-CimInstance Win32_Process "
            "-Filter \\\"Name='PEAK.exe'\\\" | "
            "Select-Object -First 1 -ExpandProperty ExecutablePath)"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=3.0,
            creationflags=flags,
        )
        raw = (result.stdout or "").strip()
        if raw:
            exe_path = Path(raw)
            if exe_path.exists():
                return exe_path.parent
    except Exception:
        pass

    return find_peak_game_dir_from_steam()



def validate_peak_game_dir(game_dir):
    if not game_dir:
        return (
            False,
            "未设置 PEAK 游戏目录",
            None,
        )

    path = Path(
        str(game_dir)
    )

    if not path.exists():
        return (
            False,
            f"目录不存在：{path}",
            path,
        )

    exe = (
        path
        / "PEAK.exe"
    )

    if not exe.exists():
        return (
            False,
            (
                "这个目录里没有找到 PEAK.exe："
                f"{path}"
            ),
            path,
        )

    return (
        True,
        "PEAK 目录有效",
        path,
    )


def get_bepinex_status(
    game_dir=None,
):
    if game_dir is None:
        game_dir = (
            get_peak_game_dir()
        )

    ok, message, path = (
        validate_peak_game_dir(
            game_dir
        )
    )

    if not ok:
        return {
            "valid_game": False,
            "installed": False,
            "complete": False,
            "message": message,
            "game_dir": (
                str(path)
                if path
                else ""
            ),
        }

    checks = {
        "BepInEx": (
            path
            / "BepInEx"
        ).is_dir(),

        "core": (
            path
            / "BepInEx"
            / "core"
            / "BepInEx.dll"
        ).exists(),

        "winhttp": (
            path
            / "winhttp.dll"
        ).exists(),

        "doorstop": (
            path
            / "doorstop_config.ini"
        ).exists(),
    }

    installed = (
        checks["BepInEx"]
        and checks["core"]
    )

    complete = all(
        checks.values()
    )

    plugin_dir = (
        path
        / "BepInEx"
        / "plugins"
    )

    canonical_plugin_path = (
        plugin_dir
        / COYOTE_PLUGIN_FILENAME
    )

    legacy_plugin_paths = [
        plugin_dir / name
        for name
        in COYOTE_PLUGIN_LEGACY_FILENAMES
    ]

    installed_plugin_path = None

    if canonical_plugin_path.exists():
        installed_plugin_path = (
            canonical_plugin_path
        )
    else:
        for legacy_path in legacy_plugin_paths:
            if legacy_path.exists():
                installed_plugin_path = (
                    legacy_path
                )
                break

    return {
        "valid_game": True,
        "installed": installed,
        "complete": complete,
        "checks": checks,
        "game_dir": str(path),
        "coyote_plugin_installed": (
            installed_plugin_path
            is not None
        ),
        "coyote_plugin_path": str(
            installed_plugin_path
            or canonical_plugin_path
        ),
        "coyote_plugin_filename": (
            installed_plugin_path.name
            if installed_plugin_path
            is not None
            else COYOTE_PLUGIN_FILENAME
        ),
        "coyote_plugin_legacy_name": (
            installed_plugin_path
            is not None
            and installed_plugin_path.name
            != COYOTE_PLUGIN_FILENAME
        ),
        "package_version": (
            BEPINEX_PEAK_PACKAGE_VERSION
        ),
        "core_version": (
            BEPINEX_CORE_VERSION
        ),
        "message": (
            "BepInEx 已安装且 PEAK 引导文件完整"
            if complete
            else "检测到 BepInEx，但部分引导文件缺失"
            if installed
            else "尚未安装 BepInEx"
        ),
    }


def _download_file(
    url,
    target,
    progress_callback=None,
):
    target = Path(target)

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Coyote-PEAK-Controller/1.0"
            ),
        },
    )

    if progress_callback:
        progress_callback(
            "正在连接 Thunderstore……"
        )

    with urllib.request.urlopen(
        request,
        timeout=30,
    ) as response:
        total = 0

        try:
            total = int(
                response.headers.get(
                    "Content-Length",
                    "0",
                )
                or 0
            )
        except Exception:
            total = 0

        received = 0

        with target.open(
            "wb"
        ) as f:
            while True:
                chunk = response.read(
                    64 * 1024
                )

                if not chunk:
                    break

                f.write(chunk)
                received += len(chunk)

                if progress_callback:
                    if total > 0:
                        percent = int(
                            received
                            / total
                            * 100
                        )

                        progress_callback(
                            (
                                "正在下载 PEAK "
                                "BepInExPack "
                                f"{percent}%"
                            )
                        )
                    else:
                        progress_callback(
                            (
                                "正在下载 PEAK "
                                "BepInExPack……"
                            )
                        )

    if (
        not target.exists()
        or target.stat().st_size
        <= 0
    ):
        raise RuntimeError(
            "下载完成后文件为空"
        )

    return target


def _find_bepinex_pack_root(
    extracted_root,
):
    extracted_root = Path(
        extracted_root
    )

    candidates = [
        extracted_root
    ]

    try:
        candidates.extend(
            [
                path
                for path
                in extracted_root.rglob("*")
                if path.is_dir()
            ]
        )
    except Exception:
        pass

    for candidate in candidates:
        if (
            (
                candidate
                / "BepInEx"
            ).is_dir()
            and (
                candidate
                / "winhttp.dll"
            ).exists()
        ):
            return candidate

    return None


def _copy_tree_with_backup(
    source_root,
    target_root,
    progress_callback=None,
):
    source_root = Path(
        source_root
    )
    target_root = Path(
        target_root
    )

    timestamp = time.strftime(
        "%Y%m%d-%H%M%S"
    )

    backup_root = (
        target_root
        / "CoyoteBackups"
        / f"BepInEx_before_{timestamp}"
    )

    files = [
        path
        for path
        in source_root.rglob("*")
        if path.is_file()
    ]

    backed_up = 0
    copied = 0

    for index, source in enumerate(
        files,
        start=1,
    ):
        relative = (
            source.relative_to(
                source_root
            )
        )

        target = (
            target_root
            / relative
        )

        if target.exists():
            backup = (
                backup_root
                / relative
            )

            backup.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            shutil.copy2(
                target,
                backup,
            )

            backed_up += 1

        target.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        shutil.copy2(
            source,
            target,
        )

        copied += 1

        if progress_callback and files:
            progress_callback(
                (
                    "正在安装 BepInEx "
                    f"{index}/{len(files)}"
                )
            )

    return {
        "copied": copied,
        "backed_up": backed_up,
        "backup_root": (
            str(backup_root)
            if backed_up
            else ""
        ),
    }


def install_bepinex_peak(
    game_dir=None,
    zip_path=None,
    progress_callback=None,
):
    """
    无 Mod Manager 安装 PEAK 专用 BepInExPack。

    zip_path=None:
        从 Thunderstore 下载官方 PEAK 专用包，并缓存到 assets/bepinex。

    zip_path!=None:
        使用用户选择的本地 ZIP。
    """
    if game_dir is None:
        game_dir = (
            get_peak_game_dir()
        )

    ok, message, game_path = (
        validate_peak_game_dir(
            game_dir
        )
    )

    if not ok:
        return (
            False,
            message,
            {},
        )

    # 避免游戏运行时替换 winhttp.dll / doorstop 等文件。
    try:
        if peak_process_running(
            force=True
        ):
            return (
                False,
                (
                    "PEAK 当前正在运行。"
                    "请完全退出 PEAK 后再安装/修复 BepInEx。"
                ),
                {},
            )
    except Exception:
        pass

    cache_dir = (
        ROOT
        / "assets"
        / "bepinex"
    )

    cache_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    downloaded = False

    try:
        if zip_path is None:
            archive = (
                cache_dir
                / (
                    "BepInExPack_PEAK-"
                    f"{BEPINEX_PEAK_PACKAGE_VERSION}"
                    ".zip"
                )
            )

            if (
                not archive.exists()
                or archive.stat().st_size
                < 10_000
            ):
                _download_file(
                    BEPINEX_PEAK_DOWNLOAD_URL,
                    archive,
                    progress_callback,
                )

                downloaded = True

        else:
            archive = Path(
                zip_path
            )

            if not archive.exists():
                return (
                    False,
                    f"找不到 ZIP：{archive}",
                    {},
                )

        if progress_callback:
            progress_callback(
                "正在校验并解压 BepInExPack……"
            )

        if not zipfile.is_zipfile(
            archive
        ):
            return (
                False,
                "选择/下载的文件不是有效 ZIP",
                {},
            )

        with tempfile.TemporaryDirectory(
            prefix="coyote_bepinex_"
        ) as temp_dir:
            temp_path = Path(
                temp_dir
            )

            with zipfile.ZipFile(
                archive,
                "r",
            ) as zf:
                zf.extractall(
                    temp_path
                )

            pack_root = (
                _find_bepinex_pack_root(
                    temp_path
                )
            )

            if pack_root is None:
                return (
                    False,
                    (
                        "ZIP 内没有找到 PEAK BepInExPack 结构。"
                        "需要包含 BepInEx 文件夹和 winhttp.dll。"
                    ),
                    {},
                )

            result = (
                _copy_tree_with_backup(
                    pack_root,
                    game_path,
                    progress_callback,
                )
            )

        network_settings[
            "peak_game_dir"
        ] = str(
            game_path.resolve()
        )

        status = (
            get_bepinex_status(
                game_path
            )
        )

        result.update({
            "downloaded": downloaded,
            "archive": str(
                archive
            ),
            "status": status,
        })

        if not status.get(
            "complete",
            False,
        ):
            return (
                False,
                (
                    "文件已经复制，但安装完整性检查未通过："
                    + status.get(
                        "message",
                        ""
                    )
                ),
                result,
            )

        add_log(
            "系统",
            "BepInEx 安装完成",
            (
                f"PEAK={game_path}; "
                f"包={BEPINEX_PEAK_PACKAGE_VERSION}; "
                f"复制={result['copied']}; "
                f"备份={result['backed_up']}"
            ),
        )

        return (
            True,
            (
                "PEAK BepInExPack "
                f"{BEPINEX_PEAK_PACKAGE_VERSION} "
                "安装完成"
            ),
            result,
        )

    except Exception as e:
        add_log(
            "错误",
            "BepInEx 安装失败",
            repr(e),
        )

        return (
            False,
            f"{type(e).__name__}: {e}",
            {},
        )


def find_built_coyote_dll():
    """
    查找可用于安装的 Coyote.dll。

    打包版优先使用随软件分发的：
        <Coyote.exe目录>/plugin/Coyote.dll

    源码版还会检查 dotnet 的 artifacts/bin 与 bin 输出。
    保留这个函数名以兼容现有 UI 调用。
    """
    packaged_candidates = []

    def add_packaged(path):
        try:
            path = Path(path)
        except Exception:
            return

        if (
            path.is_file()
            and path not in packaged_candidates
        ):
            packaged_candidates.append(
                path
            )

    # 便携目录：Coyote.exe 同级/plugin/Coyote.dll
    add_packaged(
        BUNDLED_COYOTE_DLL
    )

    # 兼容 PyInstaller --onefile / datas：
    # 解包资源通常位于 sys._MEIPASS。
    meipass = getattr(
        sys,
        "_MEIPASS",
        None,
    )

    if meipass:
        add_packaged(
            Path(meipass)
            / "plugin"
            / COYOTE_PLUGIN_FILENAME
        )

    # 打包版必须只依赖随程序分发的 DLL，
    # 不再依赖开发机 bin/artifacts。
    if getattr(
        sys,
        "frozen",
        False,
    ):
        if packaged_candidates:
            return packaged_candidates[0]

        return None

    project_dir = (
        Path(__file__)
        .resolve()
        .parent
    )

    build_candidates = []

    build_roots = [
        ROOT
        / "artifacts"
        / "bin"
        / "Coyote",
        project_dir
        / "bin",
    ]

    for build_root in build_roots:
        if not build_root.exists():
            continue

        build_candidates.extend(
            build_root.rglob(
                COYOTE_PLUGIN_FILENAME
            )
        )

    build_candidates = [
        p
        for p in build_candidates
        if p.is_file()
    ]

    build_candidates.sort(
        key=lambda p:
        p.stat().st_mtime,
        reverse=True,
    )

    if build_candidates:
        return build_candidates[0]

    if packaged_candidates:
        return packaged_candidates[0]

    return None


def install_coyote_plugin(
    game_dir=None,
    dll_path=None,
):
    if game_dir is None:
        game_dir = (
            get_peak_game_dir()
        )

    ok, message, game_path = (
        validate_peak_game_dir(
            game_dir
        )
    )

    if not ok:
        return (
            False,
            message,
        )

    status = (
        get_bepinex_status(
            game_path
        )
    )

    if not status.get(
        "installed",
        False,
    ):
        return (
            False,
            "请先安装 BepInEx",
        )

    # PEAK/BepInEx 运行时会把插件 DLL 映射到进程内存。
    # Windows 此时可能返回 WinError 1224/32，不能直接覆盖 DLL。
    if peak_process_running(force=True):
        return (
            False,
            (
                "请先关闭 PEAK 游戏进程后再安装 / 更新 Coyote.dll。\n"
                "请确认任务管理器中已没有 PEAK.exe，然后重新点击安装 / 更新。"
            ),
        )

    if dll_path is None:
        dll_path = (
            find_built_coyote_dll()
        )

    if dll_path is None:
        return (
            False,
            (
                "没有找到可安装的 Coyote.dll。"
                "打包版应包含 plugin/Coyote.dll；"
                "源码版可先执行 dotnet build -c Release。"
            ),
        )

    dll_path = Path(
        dll_path
    )

    if not dll_path.exists():
        return (
            False,
            f"找不到 DLL：{dll_path}",
        )

    target_dir = (
        game_path
        / "BepInEx"
        / "plugins"
    )

    target_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    target = (
        target_dir
        / COYOTE_PLUGIN_FILENAME
    )

    legacy_targets = [
        target_dir / name
        for name
        in COYOTE_PLUGIN_LEGACY_FILENAMES
        if name != COYOTE_PLUGIN_FILENAME
    ]

    backup_dir = (
        game_path
        / "CoyoteBackups"
        / "Plugin"
    )

    timestamp = time.strftime(
        "%Y%m%d-%H%M%S"
    )

    try:
        existing_targets = [
            path
            for path in [
                target,
                *legacy_targets,
            ]
            if path.exists()
        ]

        if existing_targets:
            backup_dir.mkdir(
                parents=True,
                exist_ok=True,
            )

        for existing in existing_targets:
            backup = (
                backup_dir
                / (
                    existing.stem
                    + "_"
                    + timestamp
                    + existing.suffix
                )
            )

            shutil.copy2(
                existing,
                backup,
            )

        # 必须复制而不是移动：
        # plugin/Coyote.dll 是软件自带的安装源，
        # 保留它才能让用户以后再次执行“安装 / 更新”。
        try:
            same_file = (
                dll_path.resolve()
                == target.resolve()
            )
        except Exception:
            same_file = False

        if not same_file:
            shutil.copy2(
                dll_path,
                target,
            )

        # 安装成功后清理旧 hot.Coyote.dll，
        # 防止 BepInEx 同时加载两个版本。
        for legacy_target in legacy_targets:
            if legacy_target.exists():
                legacy_target.unlink()

        add_log(
            "系统",
            "Coyote.dll 已安装",
            str(target),
        )

        return (
            True,
            str(target),
        )

    except PermissionError:
        return (
            False,
            (
                "请先关闭 PEAK 游戏进程后再安装 / 更新 Coyote.dll。\n"
                "目标 DLL 当前正在被游戏或 BepInEx 使用。"
            ),
        )

    except OSError as e:
        # 1224: 文件存在用户映射区域，典型情况是 DLL 已被 PEAK/BepInEx 加载。
        # 32: sharing violation，同样通常表示目标 DLL 正在被占用。
        if getattr(e, "winerror", None) in {32, 1224}:
            return (
                False,
                (
                    "请先关闭 PEAK 游戏进程后再安装 / 更新 Coyote.dll。\n"
                    "请确认任务管理器中已没有 PEAK.exe，然后重新点击安装 / 更新。"
                ),
            )

        return (
            False,
            str(e),
        )

    except Exception as e:
        return (
            False,
            str(e),
        )


def write_peak_plugin_network_config(game_dir=None, peak_port=None):
    if peak_port is None:
        peak_port = PEAK_PORT

    peak_port = validate_port(peak_port, DEFAULT_PEAK_PORT)

    if game_dir is None:
        game_dir = get_peak_game_dir()

    if game_dir is None:
        return False, "未找到 PEAK 游戏目录"

    game_dir = Path(game_dir)
    config_dir = game_dir / "BepInEx" / "config"

    try:
        config_dir.mkdir(parents=True, exist_ok=True)
        target = config_dir / "Coyote.Network.json"
        target.write_text(
            json.dumps(
                {
                    "pythonHost": PEAK_HOST,
                    "pythonPort": peak_port,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        network_settings["peak_game_dir"] = str(game_dir.resolve())
        return True, str(target)
    except Exception as e:
        return False, str(e)


def load_config():
    global rules
    global appearance

    if not CONFIG_FILE.exists():
        return

    try:
        data = json.loads(
            CONFIG_FILE.read_text(
                encoding="utf-8"
            )
        )
    except Exception as e:
        add_log(
            "系统",
            "配置读取失败",
            str(e),
        )
        return

    # -------------------- 网络端口 --------------------
    loaded_network = data.get("network")
    if isinstance(loaded_network, dict):
        network_settings["peak_port"] = validate_port(
            loaded_network.get("peak_port", DEFAULT_PEAK_PORT),
            DEFAULT_PEAK_PORT,
        )
        network_settings["dg_port"] = validate_port(
            loaded_network.get("dg_port", DEFAULT_DG_PORT),
            DEFAULT_DG_PORT,
        )
        network_settings["peak_game_dir"] = str(
            loaded_network.get("peak_game_dir", "") or ""
        )

    apply_network_settings()

    # -------------------- 自定义波形 --------------------
    install_custom_waveforms(
        data.get(
            "custom_waveforms",
            {},
        )
    )

    # -------------------- 规则 --------------------
    loaded_rules = data.get("rules")

    if isinstance(
        loaded_rules,
        dict,
    ):
        with rule_lock:
            for key, _, _, _ in RULE_META:
                incoming = loaded_rules.get(key)

                if not isinstance(
                    incoming,
                    dict,
                ):
                    continue

                cfg = rules[key]

                cfg["enabled"] = bool(
                    incoming.get(
                        "enabled",
                        cfg["enabled"],
                    )
                )

                cfg["intensity_a"] = clamp_int(
                    incoming.get(
                        "intensity_a",
                        cfg["intensity_a"],
                    )
                )

                cfg["intensity_b"] = clamp_int(
                    incoming.get(
                        "intensity_b",
                        cfg["intensity_b"],
                    )
                )

                cfg["play_time_a"] = clamp_duration(
                    incoming.get(
                        "play_time_a",
                        cfg["play_time_a"],
                    )
                )

                cfg["play_time_b"] = clamp_duration(
                    incoming.get(
                        "play_time_b",
                        cfg["play_time_b"],
                    )
                )

                wa = str(
                    incoming.get(
                        "waveform_a",
                        cfg["waveform_a"],
                    )
                )

                wb = str(
                    incoming.get(
                        "waveform_b",
                        cfg["waveform_b"],
                    )
                )

                cfg["waveform_a"] = (
                    wa
                    if wa in COYOTE_WAVEFORMS
                    else "脉冲"
                )

                cfg["waveform_b"] = (
                    wb
                    if wb in COYOTE_WAVEFORMS
                    else "脉冲"
                )

                cfg["cooldown"] = clamp_cooldown(
                    incoming.get(
                        "cooldown",
                        cfg["cooldown"],
                    )
                )

                mode = str(
                    incoming.get(
                        "trigger_mode",
                        cfg.get(
                            "trigger_mode",
                            "single",
                        ),
                    )
                    or "single"
                ).strip().lower()

                cfg["trigger_mode"] = (
                    "repeat"
                    if mode == "repeat"
                    else "single"
                )

                try:
                    cfg["trigger_delta"] = max(
                        0.1,
                        min(
                            100.0,
                            float(
                                incoming.get(
                                    "trigger_delta",
                                    cfg.get(
                                        "trigger_delta",
                                        1.0,
                                    ),
                                )
                            ),
                        ),
                    )
                except Exception:
                    cfg["trigger_delta"] = 1.0

                cfg["item_filter"] = str(
                    incoming.get(
                        "item_filter",
                        cfg.get("item_filter", ""),
                    )
                    or ""
                )[:500]

                try:
                    cfg["speed_threshold"] = max(
                        0.0,
                        min(
                            1000.0,
                            float(
                                incoming.get(
                                    "speed_threshold",
                                    cfg.get(
                                        "speed_threshold",
                                        1.0,
                                    ),
                                )
                            ),
                        ),
                    )
                except Exception:
                    cfg["speed_threshold"] = 1.0

                cfg["max_intensity_a"] = clamp_int(
                    incoming.get(
                        "max_intensity_a",
                        cfg["max_intensity_a"],
                    )
                )

                cfg["max_intensity_b"] = clamp_int(
                    incoming.get(
                        "max_intensity_b",
                        cfg["max_intensity_b"],
                    )
                )

                cfg["random_intensity"] = bool(incoming.get("random_intensity", cfg.get("random_intensity", False)))
                cfg["random_min_a"], cfg["random_max_a"] = normalize_intensity_range(
                    incoming.get("random_min_a", cfg.get("random_min_a", 1)),
                    incoming.get("random_max_a", cfg.get("random_max_a", 5)),
                    1, 5,
                )
                cfg["random_min_b"], cfg["random_max_b"] = normalize_intensity_range(
                    incoming.get("random_min_b", cfg.get("random_min_b", 1)),
                    incoming.get("random_max_b", cfg.get("random_max_b", 5)),
                    1, 5,
                )
                cfg["spike_enabled"] = bool(incoming.get("spike_enabled", cfg.get("spike_enabled", False)))
                try:
                    cfg["spike_delta"] = max(0.1, min(100.0, float(incoming.get("spike_delta", cfg.get("spike_delta", 50.0)))))
                except Exception:
                    cfg["spike_delta"] = 50.0
                cfg["spike_add_a"] = clamp_int(incoming.get("spike_add_a", cfg.get("spike_add_a", 5)))
                cfg["spike_add_b"] = clamp_int(incoming.get("spike_add_b", cfg.get("spike_add_b", 5)))
                cfg["spike_tiers"] = normalize_spike_tiers(
                    incoming.get("spike_tiers", cfg.get("spike_tiers", []))
                )

                if rule_supports_percentage_tiers(
                    key
                ):
                    cfg["thresholds"] = normalize_thresholds(
                        incoming.get(
                            "thresholds",
                            [],
                        )
                    )
                else:
                    cfg["thresholds"] = []

    # -------------------- 外观 --------------------
    loaded_appearance = data.get(
        "appearance"
    )

    if isinstance(
        loaded_appearance,
        dict,
    ):
        background_image = str(
            loaded_appearance.get(
                "background_image",
                "",
            )
            or ""
        )

        try:
            opacity = float(
                loaded_appearance.get(
                    "background_opacity",
                    0.32,
                )
            )
        except Exception:
            opacity = 0.32

        appearance[
            "background_image"
        ] = background_image

        appearance[
            "background_opacity"
        ] = max(
            0.0,
            min(
                1.0,
                opacity,
            ),
        )


def save_config():
    with rule_lock:
        saved_rules = json.loads(
            json.dumps(
                rules,
                ensure_ascii=False,
            )
        )

    data = {
        "rules": saved_rules,

        "network": {
            "peak_port": int(
                network_settings.get("peak_port", DEFAULT_PEAK_PORT)
            ),
            "dg_port": int(
                network_settings.get("dg_port", DEFAULT_DG_PORT)
            ),
            "peak_game_dir": str(
                network_settings.get("peak_game_dir", "") or ""
            ),
        },

        "custom_waveforms": json.loads(
            json.dumps(
                custom_waveforms,
                ensure_ascii=False,
            )
        ),

        "appearance": {
            "background_image": (
                appearance.get(
                    "background_image",
                    "",
                )
                or ""
            ),
            "background_opacity": max(
                0.0,
                min(
                    1.0,
                    float(
                        appearance.get(
                            "background_opacity",
                            0.32,
                        )
                    ),
                ),
            ),
        },
    }

    try:
        CONFIG_FILE.write_text(
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        add_log(
            "系统",
            "参数已保存",
            str(CONFIG_FILE),
        )

        return (
            True,
            "规则、阶梯和外观已保存",
        )

    except Exception as e:
        add_log(
            "错误",
            "参数保存失败",
            str(e),
        )

        return False, str(e)


# ============================================================
# 5. 日志
# ============================================================

def add_log(category, event, detail="", output=None):
    global log_revision

    item = {
        "time": time.strftime("%H:%M:%S"),
        "timestamp": time.time(),
        "category": str(category),
        "event": str(event),
        "detail": str(detail),
        "output": output or {},
    }

    with log_lock:
        event_logs.append(item)
        log_revision += 1

    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with EVENT_LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    except Exception:
        pass


def clear_event_logs(clear_disk=True):
    """清空内存事件日志，并可同时截断磁盘 JSONL。"""
    global log_revision

    with log_lock:
        event_logs.clear()
        log_revision += 1

    last_game_log_time.clear()

    if clear_disk:
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            EVENT_LOG_FILE.write_text("", encoding="utf-8")
        except Exception as e:
            return False, str(e)

    return True, "已清空"


def read_all_event_logs():
    """
    Read the full JSONL event history from disk.

    event_logs 保存本次运行的完整历史，
    coyote-events.log 保存磁盘历史。
    """
    result = []

    if EVENT_LOG_FILE.exists():
        try:
            with EVENT_LOG_FILE.open(
                "r",
                encoding="utf-8",
                errors="ignore",
            ) as handle:
                for line in handle:
                    line = line.strip()

                    if not line:
                        continue

                    try:
                        item = json.loads(
                            line
                        )
                    except Exception:
                        continue

                    if isinstance(
                        item,
                        dict,
                    ):
                        result.append(
                            item
                        )
        except Exception:
            result = []

    if result:
        return result

    with log_lock:
        return [
            dict(item)
            for item in event_logs
        ]


def throttled_game_log(key, event, detail, interval=0.8):
    now = time.time()
    last = last_game_log_time.get(key, 0.0)

    if now - last < interval:
        return

    last_game_log_time[key] = now
    add_log("游戏", event, detail)


# ============================================================
# 6. 局域网 IP / 配对
# ============================================================

def get_lan_ip():
    for target in [("8.8.8.8", 80), ("1.1.1.1", 80)]:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(target)
            ip = s.getsockname()[0]
            if not ip.startswith(("127.", "169.254.")):
                return ip
        except OSError:
            pass
        finally:
            s.close()

    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if not ip.startswith(("127.", "169.254.")):
                return ip
    except OSError:
        pass

    return "127.0.0.1"


LAN_IP = get_lan_ip()


def phone_ws_url(controller_id=None):
    if controller_id is None:
        with dg_lock:
            controller_id = dg["controller_id"]

    if not controller_id:
        return None

    return f"ws://{LAN_IP}:{DG_PORT}/?tid={controller_id}"


def pairing_url(controller_id=None):
    ws_url = phone_ws_url(controller_id)

    if not ws_url:
        return None

    return (
        "https://dungeon-lab.cn/s/?v=1&action=socket&url="
        + quote(ws_url, safe="")
    )


# ============================================================
# 7. DG-LAB Server
# ============================================================

def server_running():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.25)

    try:
        return s.connect_ex((DG_HOST, DG_PORT)) == 0
    finally:
        s.close()


def start_server():
    global dg_process, dg_log

    if server_running():
        with dg_lock:
            dg["server"] = "服务器已运行"
        return True

    if not BUN.exists() or not V4_SERVER.exists():
        with dg_lock:
            dg["server"] = "启动失败"
            dg["error"] = "缺少 bun.exe 或 v4-server.ts"

        add_log("错误", "DG Server 启动失败", "缺少 bun.exe 或 v4-server.ts")
        return False

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    dg_log = open(SERVER_LOG_FILE, "a", encoding="utf-8")

    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        server_env = os.environ.copy()
        server_env["PORT"] = str(DG_PORT)

        dg_process = subprocess.Popen(
            [str(BUN), "run", str(V4_SERVER)],
            cwd=str(SERVER_DIR),
            stdout=dg_log,
            stderr=subprocess.STDOUT,
            creationflags=flags,
            env=server_env,
        )
    except Exception as e:
        with dg_lock:
            dg["server"] = "启动失败"
            dg["error"] = str(e)

        add_log("错误", "DG Server 启动失败", str(e))
        return False

    for _ in range(30):
        if server_running():
            with dg_lock:
                dg["server"] = "服务器已运行"
                dg["error"] = ""

            add_log("连接", "DG Server", f"已监听 {DG_HOST}:{DG_PORT}")
            return True

        time.sleep(0.1)

    with dg_lock:
        dg["server"] = "启动失败"
        dg["error"] = f"端口 {DG_PORT} 未监听"

    add_log("错误", "DG Server", f"端口 {DG_PORT} 未监听")
    return False


# ============================================================
# 8. DG APP / 郊狼状态
# ============================================================

def handle_dg_data(data):
    if not isinstance(data, dict):
        return

    event = data.get("ev")

    if event == "devices.snapshot":
        devices = data.get("devices") or []

        if not devices:
            return

        device = devices[0]
        props = device.get("props") or {}
        slot_state = device.get("slotState") or {}

        with dg_lock:
            old_slot = dg["slot_id"]

            dg["slot_id"] = device.get("slotId")
            dg["device_name"] = device.get("name")
            dg["device_type"] = device.get("type")
            dg["has_device"] = slot_state.get("hasDevice")
            dg["connect_state"] = props.get("connectState")

            new_slot = dg["slot_id"]

        if new_slot and new_slot != old_slot:
            add_log(
                "连接",
                "检测到郊狼设备",
                f"slotId={new_slot}, type={device.get('type')}",
            )

    elif event == "slots.patch":
        for slot in data.get("slots") or []:
            slot_id = slot.get("slotId")
            slot_state = slot.get("slotState") or {}

            with dg_lock:
                if dg["slot_id"] in (None, slot_id):
                    dg["slot_id"] = slot_id

                    if "hasDevice" in slot_state:
                        dg["has_device"] = slot_state["hasDevice"]


# ============================================================
# 9. WebSocket
# ============================================================

def on_message(ws, message):
    try:
        data = json.loads(message)
    except Exception:
        return

    msg_type = data.get("type")

    if msg_type == "hello":
        with dg_lock:
            dg["controller_id"] = data.get("clientId")
            dg["server"] = "已连接"

        add_log("连接", "控制端已连接", f"controller={data.get('clientId')}")

    elif msg_type == "client_attached":
        with dg_lock:
            dg["app_id"] = data.get("clientId")

        add_log("连接", "DG-LAB APP 已接入", f"client={data.get('clientId')}")

    elif msg_type == "client_disconnected":
        client_id = data.get("clientId")

        with dg_lock:
            if dg["app_id"] == client_id:
                dg.update({
                    "app_id": None,
                    "slot_id": None,
                    "device_name": None,
                    "device_type": None,
                    "has_device": None,
                    "connect_state": None,
                })

        add_log("连接", "DG-LAB APP 已断开", f"client={client_id}")

    elif msg_type == "message":
        handle_dg_data(data.get("data"))

    elif msg_type == "error":
        error_text = f"{data.get('code')}: {data.get('message') or ''}"

        with dg_lock:
            dg["error"] = error_text

        add_log("错误", "DG-LAB", error_text)


def on_open(ws):
    with dg_lock:
        dg["server"] = "WebSocket 已连接"
        dg["error"] = ""

    add_log("连接", "WebSocket", "已连接")


def on_error(ws, error):
    with dg_lock:
        dg["server"] = "连接错误"
        dg["error"] = str(error)

    add_log("错误", "WebSocket", str(error))


def on_close(ws, code, reason):
    with dg_lock:
        dg["server"] = "已断开"
        dg["controller_id"] = None
        dg["app_id"] = None
        dg["slot_id"] = None

    if not stop_event.is_set():
        add_log("连接", "WebSocket 已断开", f"code={code}, reason={reason}")


def websocket_loop():
    global dg_ws

    while not stop_event.is_set():
        if not server_running():
            start_server()

        if not server_running():
            time.sleep(1)
            continue

        try:
            dg_ws = websocket.WebSocketApp(
                DG_URL,
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
            )

            dg_ws.run_forever(
                ping_interval=20,
                ping_timeout=10,
            )

        except Exception as e:
            with dg_lock:
                dg["server"] = "线程异常"
                dg["error"] = str(e)

            add_log("错误", "WebSocket 线程异常", str(e))

        finally:
            dg_ws = None

        if not stop_event.is_set():
            time.sleep(1)


# ============================================================
# 10. V4 发送接口
# ============================================================

def send_payload(payload):
    with dg_lock:
        ws = dg_ws
        app_id = dg["app_id"]

    if ws is None:
        return False, "WebSocket 未连接"

    if not app_id:
        return False, "DG-LAB APP 未接入"

    try:
        packet = json.dumps(
            {
                "type": "message",
                "clientId": app_id,
                "data": payload,
            },
            ensure_ascii=False,
        )

        with ws_send_lock:
            ws.send(packet)

        return True, "已发送"

    except Exception as e:
        add_log("错误", "发送失败", str(e))
        return False, str(e)


def send_rpc(method, data=None):
    payload = {
        "t": "req",
        "reqId": uuid.uuid4().hex,
        "m": method,
    }

    if data is not None:
        payload["data"] = data

    return send_payload(payload)


def get_slot_id():
    with dg_lock:
        return dg["slot_id"]


def clear_device_output(reason="手动停止"):
    # 任意全局清除动作都会终止手动 -1 持续会话。
    with manual_session_lock:
        manual_session["generation"] += 1
        manual_session["active"] = False
        manual_session["a"] = None
        manual_session["b"] = None

    slot_id = get_slot_id()

    if not slot_id:
        add_log("输出", "停止输出", "没有检测到郊狼设备")
        return False, "没有检测到郊狼设备"

    ok, message = send_rpc(
        "device.op.clear",
        {
            "s": slot_id
        }
    )

    add_log(
        "输出",
        "停止全部输出",
        f"{reason}; {message}",
    )

    return ok, message


# ============================================================
# 物品规则
# ============================================================

def normalize_item_names(value):
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item or "").strip()]


def item_rule_matches(cfg, item_name):
    item_name = str(item_name or "").strip()
    if not item_name:
        return False
    raw = str(cfg.get("item_filter", "") or "").strip()
    if not raw or raw == "*":
        return True
    tokens = [
        token.strip().lower()
        for token in re.split(r"[,;\n|]+", raw)
        if token.strip()
    ]
    if not tokens:
        return True
    lowered = item_name.lower()
    return any(token in lowered for token in tokens)


def list_added_items(old_items, new_items):
    counts = {}
    for name in normalize_item_names(old_items):
        counts[name] = counts.get(name, 0) + 1
    added = []
    for name in normalize_item_names(new_items):
        count = counts.get(name, 0)
        if count > 0:
            counts[name] = count - 1
        else:
            added.append(name)
    return added


def current_held_match(
    packet,
    cfg,
):
    held = (
        packet.get(
            "heldItem"
        )
        or {}
    )

    name = str(
        held.get(
            "name",
            "",
        )
        or ""
    ).strip()

    return (
        item_rule_matches(
            cfg,
            name,
        ),
        name,
    )


def current_backpack_matches(
    packet,
    cfg,
):
    inventory = (
        packet.get(
            "inventory"
        )
        or {}
    )

    names = normalize_item_names(
        inventory.get(
            "backpackItems",
            [],
        )
    )

    matched = [
        name
        for name in names
        if item_rule_matches(
            cfg,
            name,
        )
    ]

    return (
        bool(matched),
        matched,
    )


def handle_item_state_rules(
    current,
    previous,
):
    """
    状态型规则的单次边沿触发：

    当前手持匹配物品：
        不匹配 -> 匹配

    背包存在匹配物品：
        无匹配 -> 有匹配

    repeat / duration=-1 的持续续播由
    continuous_rule_condition() 统一处理。
    """
    if previous is None:
        return

    # -------------------- 当前手持匹配 --------------------
    held_cfg = get_rule_copy(
        "heldState"
    )

    old_held_active, _ = (
        current_held_match(
            previous,
            held_cfg,
        )
    )

    new_held_active, new_held_name = (
        current_held_match(
            current,
            held_cfg,
        )
    )

    if (
        new_held_active
        and not old_held_active
    ):
        send_rule_output(
            "heldState",
            "当前手持匹配物品",
            (
                "当前手持："
                + (
                    new_held_name
                    or "未知物品"
                )
            ),
            current_value_pct=None,
        )

    # -------------------- 背包存在匹配 --------------------
    backpack_cfg = get_rule_copy(
        "backpackState"
    )

    old_backpack_active, _ = (
        current_backpack_matches(
            previous,
            backpack_cfg,
        )
    )

    new_backpack_active, matched = (
        current_backpack_matches(
            current,
            backpack_cfg,
        )
    )

    if (
        new_backpack_active
        and not old_backpack_active
    ):
        send_rule_output(
            "backpackState",
            "背包存在匹配物品",
            (
                "背包匹配："
                + "、".join(
                    matched
                )
            ),
            current_value_pct=None,
        )


def handle_item_rules(current, previous):
    if previous is None:
        return

    old_held = previous.get("heldItem") or {}
    new_held = current.get("heldItem") or {}
    old_name = str(old_held.get("name", "") or "").strip()
    new_name = str(new_held.get("name", "") or "").strip()

    if new_name and new_name != old_name:
        cfg = get_rule_copy("heldItem")
        if item_rule_matches(cfg, new_name):
            send_rule_output(
                "heldItem",
                "拿起手持物",
                f"{old_name or '空手'} → {new_name}",
                current_value_pct=None,
            )

    old_inv = previous.get("inventory") or {}
    new_inv = current.get("inventory") or {}
    added = list_added_items(
        old_inv.get("backpackItems", []),
        new_inv.get("backpackItems", []),
    )
    if added:
        cfg = get_rule_copy("backpackItem")
        matched = [name for name in added if item_rule_matches(cfg, name)]
        if matched:
            send_rule_output(
                "backpackItem",
                "背包装入物品",
                "新增：" + "、".join(matched),
                current_value_pct=None,
            )


# ============================================================
# 手动设备控制
# ============================================================

def _manual_payload(
    channel,
    intensity,
    duration_ms,
    waveform_name,
):
    return {
        "channel": 0 if int(channel) == 0 else 1,
        "intensity": clamp_int(
            intensity
        ),
        "configured_duration": clamp_duration(
            duration_ms,
            allow_continuous=True,
        ),
        "waveform": str(
            waveform_name
            or "脉冲"
        ),
    }


def _send_manual_payload_once(
    payload,
):
    """
    真正发给 DG-LAB 的持续时间永远是有限值。
    即使 configured_duration == -1，也会被解析成有限片段。
    """
    if peak_is_incapacitated():
        return (False, "角色死亡/昏迷，安全锁已屏蔽全部电击")

    if not master_output_enabled:
        return (
            False,
            "总输出开关处于关闭状态",
        )

    slot_id = get_slot_id()

    if not slot_id:
        return (
            False,
            "没有检测到郊狼设备",
        )

    channel = (
        0
        if int(
            payload.get(
                "channel",
                0,
            )
        ) == 0
        else 1
    )

    intensity = clamp_int(
        payload.get(
            "intensity",
            0,
        )
    )

    configured_duration = (
        payload.get(
            "configured_duration",
            1000,
        )
    )

    actual_duration = (
        resolve_rule_duration_ms(
            configured_duration
        )
    )

    waveform_name = str(
        payload.get(
            "waveform",
            "脉冲",
        )
        or "脉冲"
    )

    waveform = COYOTE_WAVEFORMS.get(
        waveform_name
    )

    if waveform is None:
        return (
            False,
            f"波形不存在：{waveform_name}",
        )

    intensity_result = send_rpc(
        "device.op",
        {
            "s": slot_id,
            "c": channel,
            "t": 4,
            "v": intensity,
            "d": actual_duration,
            "im": True,
        },
    )

    if intensity <= 0:
        return intensity_result

    waveform_result = send_rpc(
        "device.op",
        {
            "s": slot_id,
            "c": channel,
            "t": 0,
            "d": actual_duration,
            "v": waveform,
            "im": True,
        },
    )

    ok = (
        intensity_result[0]
        and waveform_result[0]
    )

    message = (
        "已发送"
        if ok
        else (
            f"强度={intensity_result[1]}; "
            f"波形={waveform_result[1]}"
        )
    )

    add_log(
        "手动控制",
        (
            "A 通道"
            if channel == 0
            else "B 通道"
        ),
        (
            f"等级 {intensity} / "
            + (
                "持续(-1)"
                if is_continuous_duration(
                    configured_duration
                )
                else f"{actual_duration}ms"
            )
            + f" / {waveform_name} / "
            + message
        ),
    )

    return (
        ok,
        message,
    )


def stop_manual_continuous(
    *,
    clear_device=False,
):
    """
    结束手动持续会话。

    clear_device=True 时另外发送 device.op.clear。
    """
    with manual_session_lock:
        manual_session["generation"] += 1
        manual_session["active"] = False
        manual_session["a"] = None
        manual_session["b"] = None

    if clear_device:
        return clear_device_output(
            "停止手动持续会话"
        )

    return (
        True,
        "手动持续会话已停止",
    )


def manual_continuous_status():
    with manual_session_lock:
        return {
            "active": bool(
                manual_session[
                    "active"
                ]
            ),
            "a": (
                manual_session[
                    "a"
                ]
                is not None
            ),
            "b": (
                manual_session[
                    "b"
                ]
                is not None
            ),
        }


def _manual_continuous_worker(
    generation,
):
    """
    手动 -1 是 Coyote 内部的持续会话标记。

    每轮只向 DG-LAB 发送 CONTINUOUS_SEGMENT_MS
    的有限片段，再根据会话是否仍有效决定是否续播。
    """
    add_log(
        "手动控制",
        "持续会话启动",
        (
            f"有限片段="
            f"{CONTINUOUS_SEGMENT_MS}ms"
        ),
    )

    while True:
        with manual_session_lock:
            if (
                generation
                != manual_session[
                    "generation"
                ]
                or not manual_session[
                    "active"
                ]
            ):
                break

            a_payload = (
                manual_session[
                    "a"
                ]
            )
            b_payload = (
                manual_session[
                    "b"
                ]
            )

        # 总输出开关关闭或设备消失时自动退出。
        if not master_output_enabled:
            break

        if not get_slot_id():
            break

        sent_any = False

        for payload in (
            a_payload,
            b_payload,
        ):
            if not isinstance(
                payload,
                dict,
            ):
                continue

            if not is_continuous_duration(
                payload.get(
                    "configured_duration",
                    1000,
                )
            ):
                continue

            _send_manual_payload_once(
                payload
            )

            sent_any = True

        if not sent_any:
            break

        # 在片段结束前稍早续播，
        # 避免因线程调度造成明显间隙。
        wait_seconds = max(
            0.25,
            (
                CONTINUOUS_SEGMENT_MS
                / 1000.0
                * 0.85
            ),
        )

        deadline = (
            time.time()
            + wait_seconds
        )

        should_exit = False

        while (
            time.time()
            < deadline
        ):
            with manual_session_lock:
                if (
                    generation
                    != manual_session[
                        "generation"
                    ]
                    or not manual_session[
                        "active"
                    ]
                ):
                    should_exit = True
                    break

            if (
                not master_output_enabled
                or not get_slot_id()
            ):
                should_exit = True
                break

            time.sleep(
                0.05
            )

        if should_exit:
            break

    with manual_session_lock:
        if (
            generation
            == manual_session[
                "generation"
            ]
        ):
            manual_session[
                "active"
            ] = False

            manual_session[
                "a"
            ] = None

            manual_session[
                "b"
            ] = None

    # 如果是因为断连或总开关关闭退出，
    # 尝试清除设备任务；clear 内部也会再次取消 generation。
    try:
        clear_device_output(
            "手动持续会话结束"
        )
    except Exception:
        pass

    add_log(
        "手动控制",
        "持续会话结束",
        "",
    )


def start_manual_continuous(
    *,
    a_payload=None,
    b_payload=None,
):
    if not master_output_enabled:
        return (
            False,
            "请先开启“允许电击输出”",
        )

    if not get_slot_id():
        return (
            False,
            "没有检测到郊狼设备",
        )

    a_continuous = (
        isinstance(
            a_payload,
            dict,
        )
        and is_continuous_duration(
            a_payload.get(
                "configured_duration",
                1000,
            )
        )
    )

    b_continuous = (
        isinstance(
            b_payload,
            dict,
        )
        and is_continuous_duration(
            b_payload.get(
                "configured_duration",
                1000,
            )
        )
    )

    if (
        not a_continuous
        and not b_continuous
    ):
        return (
            False,
            "没有设置 -1 的持续通道",
        )

    # 新持续会话替代旧会话。
    stop_manual_continuous(
        clear_device=False
    )

    with manual_session_lock:
        generation = (
            manual_session[
                "generation"
            ]
            + 1
        )

        manual_session[
            "generation"
        ] = generation

        manual_session[
            "active"
        ] = True

        manual_session[
            "a"
        ] = (
            a_payload
            if a_continuous
            else None
        )

        manual_session[
            "b"
        ] = (
            b_payload
            if b_continuous
            else None
        )

    threading.Thread(
        target=_manual_continuous_worker,
        args=(
            generation,
        ),
        name="ManualContinuous",
        daemon=True,
    ).start()

    return (
        True,
        "手动持续会话已启动",
    )


def send_manual_channel(
    channel,
    intensity,
    duration_ms,
    waveform_name,
):
    """
    单通道设备控制。

    duration_ms == -1：
        启动该通道手动持续会话。

    其他值：
        正常有限播放。
    """
    payload = _manual_payload(
        channel,
        intensity,
        duration_ms,
        waveform_name,
    )

    if is_continuous_duration(
        payload[
            "configured_duration"
        ]
    ):
        if (
            payload[
                "channel"
            ]
            == 0
        ):
            return start_manual_continuous(
                a_payload=payload,
            )

        return start_manual_continuous(
            b_payload=payload,
        )

    return _send_manual_payload_once(
        payload
    )


def send_manual_dual(
    intensity_a,
    intensity_b,
    duration_a,
    duration_b,
    waveform_a,
    waveform_b,
):
    """
    双通道支持混合：
      A=-1, B=3000
      A=2000, B=-1
      A=-1, B=-1
    """
    a_payload = _manual_payload(
        0,
        intensity_a,
        duration_a,
        waveform_a,
    )

    b_payload = _manual_payload(
        1,
        intensity_b,
        duration_b,
        waveform_b,
    )

    a_continuous = (
        is_continuous_duration(
            a_payload[
                "configured_duration"
            ]
        )
    )

    b_continuous = (
        is_continuous_duration(
            b_payload[
                "configured_duration"
            ]
        )
    )

    result_a = (
        (
            True,
            "A 持续通道待启动",
        )
        if a_continuous
        else _send_manual_payload_once(
            a_payload
        )
    )

    result_b = (
        (
            True,
            "B 持续通道待启动",
        )
        if b_continuous
        else _send_manual_payload_once(
            b_payload
        )
    )

    continuous_result = (
        True,
        "没有持续通道",
    )

    if (
        a_continuous
        or b_continuous
    ):
        continuous_result = (
            start_manual_continuous(
                a_payload=a_payload,
                b_payload=b_payload,
            )
        )

    return {
        "a": result_a,
        "b": result_b,
        "continuous": (
            continuous_result
        ),
        "ok": (
            result_a[0]
            and result_b[0]
            and continuous_result[0]
        ),
    }


def stamina_percent(packet):
    try:
        current = float(
            packet.get(
                "staminaCurrent",
                0.0,
            )
        )
    except Exception:
        current = 0.0

    try:
        maximum = float(
            packet.get(
                "staminaMax",
                0.0,
            )
        )
    except Exception:
        maximum = 0.0

    if maximum > 0.0001:
        value = (
            current
            / maximum
            * 100.0
        )
    elif abs(current) <= 1.5:
        value = current * 100.0
    else:
        value = current

    return max(
        0.0,
        min(
            100.0,
            value,
        ),
    )


def velocity_y(packet):
    velocity = (
        packet.get(
            "velocity"
        )
        or {}
    )

    try:
        if isinstance(
            velocity,
            dict,
        ):
            return float(
                velocity.get(
                    "y",
                    0.0,
                )
            )
    except Exception:
        pass

    return 0.0


def packet_speed(packet):
    """
    优先使用 C# telemetry 已发送的 speed。
    若不存在，则根据 velocity 向量计算。
    """
    try:
        raw = packet.get(
            "speed",
            None,
        )

        if raw is not None:
            return max(
                0.0,
                float(raw),
            )
    except Exception:
        pass

    velocity = packet.get(
        "velocity"
    ) or {}

    if isinstance(
        velocity,
        dict,
    ):
        try:
            x = float(
                velocity.get("x", 0.0)
            )
            y = float(
                velocity.get("y", 0.0)
            )
            z = float(
                velocity.get("z", 0.0)
            )

            return (
                x * x
                + y * y
                + z * z
            ) ** 0.5
        except Exception:
            pass

    return 0.0


def handle_speed_rules(
    current,
    previous,
):
    if previous is None:
        return

    old_speed = packet_speed(
        previous
    )
    new_speed = packet_speed(
        current
    )

    # 速度低于阈值：
    # 只在从阈值外进入阈值内时触发一次。
    low_cfg = get_rule_copy(
        "speedBelow"
    )

    try:
        low_threshold = max(
            0.0,
            float(
                low_cfg.get(
                    "speed_threshold",
                    1.0,
                )
            ),
        )
    except Exception:
        low_threshold = 1.0

    if (
        old_speed >= low_threshold
        and new_speed < low_threshold
    ):
        send_rule_output(
            "speedBelow",
            "速度低于阈值",
            (
                f"{old_speed:.3f} → "
                f"{new_speed:.3f} "
                f"(阈值 {low_threshold:.3f})"
            ),
            current_value_pct=None,
        )

    # 速度高于阈值：
    # 同样只在跨越阈值时触发一次。
    high_cfg = get_rule_copy(
        "speedAbove"
    )

    try:
        high_threshold = max(
            0.0,
            float(
                high_cfg.get(
                    "speed_threshold",
                    5.0,
                )
            ),
        )
    except Exception:
        high_threshold = 5.0

    if (
        old_speed <= high_threshold
        and new_speed > high_threshold
    ):
        send_rule_output(
            "speedAbove",
            "速度高于阈值",
            (
                f"{old_speed:.3f} → "
                f"{new_speed:.3f} "
                f"(阈值 {high_threshold:.3f})"
            ),
            current_value_pct=None,
        )


# ============================================================
# 11. 游戏事件判断 + 统一输出
# ============================================================

def rule_trigger_mode(
    cfg,
):
    return (
        "repeat"
        if str(
            cfg.get(
                "trigger_mode",
                "single",
            )
            or "single"
        ).lower()
        == "repeat"
        else "single"
    )


def continuous_rule_condition(
    key,
    current,
):
    """
    返回：
        (active, detail, current_value_pct)

    只有“当前有明确持续状态”的规则才在 repeat 模式中持续调度。
    瞬时事件规则（hp下降 / staminaUse / jump / 背包装入）
    没有持续 condition，这里返回 False。
    """
    if not current:
        return (
            False,
            "",
            None,
        )

    if current.get(
        "hasCharacter",
        True,
    ) is False:
        return (
            False,
            "",
            None,
        )

    if key == "dead":
        active = bool(
            current.get(
                "dead",
                False,
            )
        )

        return (
            active,
            "死亡状态持续中",
            None,
        )

    if key == "passedOut":
        active = bool(
            current.get(
                "passedOut",
                False,
            )
        )

        return (
            active,
            "昏迷状态持续中",
            None,
        )

    if key == "climbStart":
        active = bool(
            current.get(
                "climbing",
                False,
            )
        )

        return (
            active,
            "攀爬状态持续中",
            None,
        )

    if key == "crouchStart":
        active = bool(
            current.get(
                "crouching",
                False,
            )
        )

        return (
            active,
            "蹲下状态持续中",
            None,
        )

    if key in (
        "speedBelow",
        "speedAbove",
    ):
        cfg = get_rule_copy(
            key
        )

        try:
            threshold = max(
                0.0,
                float(
                    cfg.get(
                        "speed_threshold",
                        (
                            1.0
                            if key == "speedBelow"
                            else 5.0
                        ),
                    )
                ),
            )
        except Exception:
            threshold = (
                1.0
                if key == "speedBelow"
                else 5.0
            )

        speed = packet_speed(
            current
        )

        if key == "speedBelow":
            active = (
                speed < threshold
            )

            detail = (
                f"速度 {speed:.3f} "
                f"< {threshold:.3f}"
            )

        else:
            active = (
                speed > threshold
            )

            detail = (
                f"速度 {speed:.3f} "
                f"> {threshold:.3f}"
            )

        return (
            active,
            detail,
            None,
        )

    if key in (
        "heldItem",
        "heldState",
    ):
        cfg = get_rule_copy(
            key
        )

        active, name = (
            current_held_match(
                current,
                cfg,
            )
        )

        return (
            active,
            (
                f"当前手持：{name}"
                if name
                else "当前空手"
            ),
            None,
        )

    if key in (
        "backpackItem",
        "backpackState",
    ):
        cfg = get_rule_copy(
            key
        )

        active, matched = (
            current_backpack_matches(
                current,
                cfg,
            )
        )

        return (
            active,
            (
                "背包匹配："
                + "、".join(
                    matched
                )
                if matched
                else "背包无匹配物品"
            ),
            None,
        )

    meta = RULE_META_BY_KEY.get(
        key
    )

    if (
        meta
        and meta.get(
            "index"
        )
        is not None
    ):
        value = status_percent_for_rule(
            current,
            key,
            meta.get("index"),
        )

        if value is None:
            return (
                False,
                "",
                None,
            )

        return (
            value > 0.0,
            (
                f"{meta['display']} "
                f"{value:.1f}% 持续中"
            ),
            value,
        )

    # 瞬时规则没有可靠的“持续成立”状态：
    # hp / staminaUse / jump / backpackItem新增事件等。
    return (
        False,
        "",
        None,
    )


def continuous_effective_cooldown(
    cfg,
):
    """
    持续模式不允许上一段有限输出尚未结束时立刻叠加下一段。

    所以实际重复间隔至少为：
      max(规则冷却, A持续时间, B持续时间, 1秒)
    """
    cooldown = clamp_cooldown(
        cfg.get(
            "cooldown",
            2.0,
        )
    )

    duration_a = (
        resolve_rule_duration_ms(
            cfg.get(
                "play_time_a",
                1000,
            )
        )
        / 1000.0
    )

    duration_b = (
        resolve_rule_duration_ms(
            cfg.get(
                "play_time_b",
                1000,
            )
        )
        / 1000.0
    )

    return max(
        1.0,
        cooldown,
        duration_a,
        duration_b,
    )


def handle_continuous_rules(
    current,
):
    """
    在每包 telemetry 后运行。

    持续规则天然低于单次规则：只要最近的单次输出仍在持续时间窗口内，
    本轮持续规则就不会发送，避免持续电击把单次事件效果立刻覆盖。
    同一时刻若多个持续条件成立，仅发送冷却已满足的第一个规则。
    """
    global single_output_guard_until

    if peak_is_incapacitated(current):
        return

    if time.time() < single_output_guard_until:
        return


    for key, display, _, _ in RULE_META:
        cfg = get_rule_copy(key)

        if not cfg.get("enabled", False):
            continue

        repeat_mode = rule_trigger_mode(cfg) == "repeat"
        duration_hold = rule_has_continuous_duration(cfg)

        if not repeat_mode and not duration_hold:
            continue

        active, detail, value_pct = continuous_rule_condition(key, current)
        if not active:
            continue

        sent = send_rule_output(
            key,
            display,
            detail,
            current_value_pct=value_pct,
            continuous=True,
        )
        if sent:
            # 避免同一遥测包内多个持续规则互相覆盖。
            return


def get_rule_copy(key):
    with rule_lock:
        return json.loads(
            json.dumps(
                rules[key],
                ensure_ascii=False,
            )
        )


def rule_can_trigger(key, cooldown):
    now = time.time()
    last = last_trigger_time.get(key, 0.0)

    if now - last < cooldown:
        return False

    last_trigger_time[key] = now
    return True


def select_threshold_tier(
    cfg,
    current_value_pct,
):
    """
    选择当前百分比命中的动态强度档位。

    规则：
      1. current_value_pct < below 才算命中。
      2. 若同时命中多个，不累加。
      3. 只采用阈值最小（状态更低）的那一档。

    示例：
      <80%  +1
      <50%  +3
      <20%  +5

      当前 35% -> 采用 <50% +3
      当前 10% -> 采用 <20% +5
    """
    if current_value_pct is None:
        return None

    try:
        value = float(
            current_value_pct
        )
    except Exception:
        return None

    candidates = []

    for tier in normalize_thresholds(
        cfg.get(
            "thresholds",
            [],
        )
    ):
        if value < tier["below"]:
            candidates.append(
                tier
            )

    if not candidates:
        return None

    return min(
        candidates,
        key=lambda x: x["below"],
    )


def calculate_rule_intensities(
    cfg,
    current_value_pct=None,
    change_delta_pct=None,
):
    configured_base_a = clamp_int(cfg.get("intensity_a", 0))
    configured_base_b = clamp_int(cfg.get("intensity_b", 0))

    random_enabled = bool(cfg.get("random_intensity", False))
    if random_enabled:
        min_a, max_rand_a = normalize_intensity_range(
            cfg.get("random_min_a", configured_base_a),
            cfg.get("random_max_a", configured_base_a),
            configured_base_a, configured_base_a,
        )
        min_b, max_rand_b = normalize_intensity_range(
            cfg.get("random_min_b", configured_base_b),
            cfg.get("random_max_b", configured_base_b),
            configured_base_b, configured_base_b,
        )
        base_a = random.randint(min_a, max_rand_a)
        base_b = random.randint(min_b, max_rand_b)
    else:
        min_a = max_rand_a = configured_base_a
        min_b = max_rand_b = configured_base_b
        base_a = configured_base_a
        base_b = configured_base_b

    max_a = clamp_int(cfg.get("max_intensity_a", GUI_INTENSITY_MAX))
    max_b = clamp_int(cfg.get("max_intensity_b", GUI_INTENSITY_MAX))

    tier = select_threshold_tier(cfg, current_value_pct)
    add_a = clamp_int(tier.get("add_a", 0)) if tier else 0
    add_b = clamp_int(tier.get("add_b", 0)) if tier else 0

    spike_applied = False
    spike_add_a = 0
    spike_add_b = 0
    spike_threshold = None
    spike_range_a = (0, 0)
    spike_range_b = (0, 0)
    try:
        delta = abs(float(change_delta_pct)) if change_delta_pct is not None else None
    except Exception:
        delta = None

    spike_tiers = spike_tiers_from_config(cfg) if bool(cfg.get("spike_enabled", False)) else []
    matched_spike_tier = None
    if delta is not None and spike_tiers:
        candidates = [tier for tier in spike_tiers if delta >= tier["delta"]]
        if candidates:
            # 只取已命中的最高阈值档，不累计。
            matched_spike_tier = max(candidates, key=lambda x: x["delta"])
            spike_applied = True
            spike_threshold = matched_spike_tier["delta"]
            spike_range_a = (matched_spike_tier["min_a"], matched_spike_tier["max_a"])
            spike_range_b = (matched_spike_tier["min_b"], matched_spike_tier["max_b"])
            spike_add_a = random.randint(*spike_range_a)
            spike_add_b = random.randint(*spike_range_b)

    final_a = min(GUI_INTENSITY_MAX, max_a, base_a + add_a + spike_add_a)
    final_b = min(GUI_INTENSITY_MAX, max_b, base_b + add_b + spike_add_b)

    return {
        "configured_base_a": configured_base_a,
        "configured_base_b": configured_base_b,
        "base_a": base_a,
        "base_b": base_b,
        "random_enabled": random_enabled,
        "random_min_a": min_a,
        "random_max_a": max_rand_a,
        "random_min_b": min_b,
        "random_max_b": max_rand_b,
        "max_a": max_a,
        "max_b": max_b,
        "add_a": add_a,
        "add_b": add_b,
        "spike_applied": spike_applied,
        "spike_delta": delta,
        "spike_threshold": spike_threshold,
        "spike_add_a": spike_add_a,
        "spike_add_b": spike_add_b,
        "spike_range_a": spike_range_a,
        "spike_range_b": spike_range_b,
        "spike_tier": matched_spike_tier,
        "final_a": final_a,
        "final_b": final_b,
        "tier": tier,
    }


def send_rule_output(
    rule_key,
    event_name,
    change_detail,
    current_value_pct=None,
    continuous=False,
    change_delta_pct=None,
):
    global output_count
    global last_output
    global single_output_guard_until

    cfg = get_rule_copy(
        rule_key
    )

    if peak_is_incapacitated():
        add_log(
            "游戏",
            event_name,
            f"{change_detail}；角色死亡/昏迷，安全锁屏蔽输出",
        )
        return False

    if not cfg["enabled"]:
        add_log(
            "游戏",
            event_name,
            f"{change_detail}；规则已关闭",
        )
        return False

    cooldown = clamp_cooldown(
        cfg["cooldown"]
    )

    if continuous:
        repeat_mode = (
            rule_trigger_mode(cfg)
            == "repeat"
        )
        duration_hold = (
            rule_has_continuous_duration(cfg)
        )

        if (
            not repeat_mode
            and not duration_hold
        ):
            return

        cooldown = (
            continuous_effective_cooldown(
                cfg
            )
        )

    if not rule_can_trigger(
        rule_key,
        cooldown,
    ):
        return False

    # 事件本身与设备是否开启无关：
    # 即使总输出关闭，日志仍记录游戏发生了什么。
    add_log(
        "游戏",
        (
            f"{event_name}（持续）"
            if continuous
            else event_name
        ),
        change_detail,
    )

    intensity_info = (
        calculate_rule_intensities(
            cfg,
            current_value_pct,
            change_delta_pct,
        )
    )

    intensity_a = intensity_info[
        "final_a"
    ]
    intensity_b = intensity_info[
        "final_b"
    ]

    tier = intensity_info["tier"]

    if tier is not None:
        tier_wave_a_text = tier.get(
            "waveform_a",
            TIER_WAVEFORM_INHERIT,
        )
        tier_wave_b_text = tier.get(
            "waveform_b",
            TIER_WAVEFORM_INHERIT,
        )

        tier_text = (
            f"当前值 {float(current_value_pct):.1f}% "
            f"< {tier['below']:.1f}%："
            f"A +{tier['add_a']} / "
            f"B +{tier['add_b']} / "
            f"A波形={tier_wave_a_text} / "
            f"B波形={tier_wave_b_text}"
        )
    elif current_value_pct is not None:
        tier_text = (
            f"当前值 {float(current_value_pct):.1f}%："
            "未命中自动强度档位"
        )
    else:
        tier_text = (
            "布尔事件：不使用百分比强度阶梯"
        )

    random_text = (
        f"随机基础 A={intensity_info['base_a']}[{intensity_info['random_min_a']}-{intensity_info['random_max_a']}] / "
        f"B={intensity_info['base_b']}[{intensity_info['random_min_b']}-{intensity_info['random_max_b']}]"
        if intensity_info.get("random_enabled")
        else "固定基础强度"
    )
    if intensity_info.get("spike_applied"):
        ra = intensity_info.get("spike_range_a", (0, 0))
        rb = intensity_info.get("spike_range_b", (0, 0))
        spike_text = (
            f"瞬时加强命中 Δ={intensity_info['spike_delta']:.1f}% >= {intensity_info['spike_threshold']:.1f}%："
            f"A +{intensity_info['spike_add_a']}（范围 {ra[0]}~{ra[1]}） / "
            f"B +{intensity_info['spike_add_b']}（范围 {rb[0]}~{rb[1]}）"
        )
    else:
        spike_text = "瞬时加强未命中"

    if not master_output_enabled:
        add_log(
            "输出",
            f"{event_name} 未输出",
            (
                "总输出开关处于关闭状态 | "
                + tier_text
            ),
        )
        return False

    slot_id = get_slot_id()

    if not slot_id:
        add_log(
            "输出",
            f"{event_name} 未输出",
            (
                "没有检测到郊狼设备 | "
                + tier_text
            ),
        )
        return False

    configured_duration_a = clamp_duration(
        cfg["play_time_a"]
    )
    configured_duration_b = clamp_duration(
        cfg["play_time_b"]
    )

    play_time_a = resolve_rule_duration_ms(
        configured_duration_a
    )
    play_time_b = resolve_rule_duration_ms(
        configured_duration_b
    )

    repeat_mode = (
        rule_trigger_mode(cfg)
        == "repeat"
    )

    # 如果只是 duration=-1 驱动持续续播，
    # 后续周期只续播设置为 -1 的那个通道。
    send_a_this_cycle = True
    send_b_this_cycle = True

    if (
        continuous
        and not repeat_mode
    ):
        send_a_this_cycle = (
            is_continuous_duration(
                configured_duration_a
            )
        )
        send_b_this_cycle = (
            is_continuous_duration(
                configured_duration_b
            )
        )

    # 默认使用该事件的基础波形。
    waveform_a_name = cfg[
        "waveform_a"
    ]
    waveform_b_name = cfg[
        "waveform_b"
    ]

    # 如果当前命中的强度档位指定了波形，则覆盖基础波形。
    # 选择“沿用基础波形”时不覆盖。
    if tier is not None:
        tier_waveform_a = tier.get(
            "waveform_a",
            TIER_WAVEFORM_INHERIT,
        )

        tier_waveform_b = tier.get(
            "waveform_b",
            TIER_WAVEFORM_INHERIT,
        )

        if (
            tier_waveform_a
            != TIER_WAVEFORM_INHERIT
            and tier_waveform_a in COYOTE_WAVEFORMS
        ):
            waveform_a_name = tier_waveform_a

        if (
            tier_waveform_b
            != TIER_WAVEFORM_INHERIT
            and tier_waveform_b in COYOTE_WAVEFORMS
        ):
            waveform_b_name = tier_waveform_b

    waveform_a = COYOTE_WAVEFORMS.get(
        waveform_a_name
    )
    waveform_b = COYOTE_WAVEFORMS.get(
        waveform_b_name
    )

    if (
        waveform_a is None
        or waveform_b is None
    ):
        add_log(
            "错误",
            "波形不存在",
            (
                f"A={waveform_a_name}, "
                f"B={waveform_b_name}"
            ),
        )
        return False

    results = []

    if (
        intensity_a > 0
        and send_a_this_cycle
    ):
        results.append(
            send_rpc(
                "device.op",
                {
                    "s": slot_id,
                    "c": 0,
                    "t": 4,
                    "v": intensity_a,
                    "d": play_time_a,
                    "im": True,
                },
            )
        )

    if (
        intensity_b > 0
        and send_b_this_cycle
    ):
        results.append(
            send_rpc(
                "device.op",
                {
                    "s": slot_id,
                    "c": 1,
                    "t": 4,
                    "v": intensity_b,
                    "d": play_time_b,
                    "im": True,
                },
            )
        )

    if (
        intensity_a > 0
        and send_a_this_cycle
    ):
        results.append(
            send_rpc(
                "device.op",
                {
                    "s": slot_id,
                    "c": 0,
                    "t": 0,
                    "d": play_time_a,
                    "im": True,
                    "v": waveform_a,
                },
            )
        )

    if (
        intensity_b > 0
        and send_b_this_cycle
    ):
        results.append(
            send_rpc(
                "device.op",
                {
                    "s": slot_id,
                    "c": 1,
                    "t": 0,
                    "d": play_time_b,
                    "im": True,
                    "v": waveform_b,
                },
            )
        )

    all_ok = (
        bool(results)
        and all(
            ok
            for ok, _ in results
        )
    )

    output_info = {
        "event": event_name,
        "change": change_detail,

        "a_base_intensity": (
            intensity_info["base_a"]
        ),
        "b_base_intensity": (
            intensity_info["base_b"]
        ),
        "random_intensity": intensity_info.get("random_enabled", False),
        "spike_applied": intensity_info.get("spike_applied", False),
        "spike_delta": intensity_info.get("spike_delta"),
        "spike_threshold": intensity_info.get("spike_threshold"),
        "spike_add_a": intensity_info.get("spike_add_a", 0),
        "spike_add_b": intensity_info.get("spike_add_b", 0),
        "spike_range_a": intensity_info.get("spike_range_a", (0, 0)),
        "spike_range_b": intensity_info.get("spike_range_b", (0, 0)),

        "a_bonus": (
            intensity_info["add_a"]
        ),
        "b_bonus": (
            intensity_info["add_b"]
        ),

        "a_intensity": intensity_a,
        "b_intensity": intensity_b,

        "a_max_intensity": (
            intensity_info["max_a"]
        ),
        "b_max_intensity": (
            intensity_info["max_b"]
        ),

        "a_duration": configured_duration_a,
        "b_duration": configured_duration_b,
        "a_segment_duration": play_time_a,
        "b_segment_duration": play_time_b,

        "a_waveform": waveform_a_name,
        "b_waveform": waveform_b_name,

        "current_value_pct": (
            current_value_pct
        ),

        "threshold": (
            tier["below"]
            if tier
            else None
        ),

        "tier_waveform_a": (
            tier.get(
                "waveform_a",
                TIER_WAVEFORM_INHERIT,
            )
            if tier
            else None
        ),

        "tier_waveform_b": (
            tier.get(
                "waveform_b",
                TIER_WAVEFORM_INHERIT,
            )
            if tier
            else None
        ),

        "success": all_ok,
    }

    with log_lock:
        output_count += 1
        last_output = output_info

    add_log(
        "输出",
        event_name,
        (
            f"{change_detail} | "
            f"{tier_text} | "
            f"{random_text} | {spike_text} | "
            f"A: 基础{intensity_info['base_a']}"
            f"+{intensity_info['add_a']}"
            f"→等级{intensity_a}"
            f" / 上限{intensity_info['max_a']}"
            f" / {play_time_a}ms"
            f" / {waveform_a_name} | "
            f"B: 基础{intensity_info['base_b']}"
            f"+{intensity_info['add_b']}"
            f"→等级{intensity_b}"
            f" / 上限{intensity_info['max_b']}"
            f" / {play_time_b}ms"
            f" / {waveform_b_name} | "
            f"{'发送成功' if all_ok else '部分/全部发送失败'}"
        ),
        output=output_info,
    )

    if not continuous and results:
        guard_seconds = max(
            play_time_a if send_a_this_cycle else 0,
            play_time_b if send_b_this_cycle else 0,
        ) / 1000.0
        single_output_guard_until = max(
            single_output_guard_until,
            time.time() + max(0.1, guard_seconds),
        )

    return all_ok


def handle_boolean_events(
    current,
    previous,
):
    """
    死亡 / 昏迷是独立事件源：
      - 不依赖 hp 是否变化。
      - 只记录 False -> True / True -> False 的边沿。
      - 进入死亡/昏迷后由 handle_game_rules 的安全锁立即清空输出，
        并阻断本包及后续所有自动、手动和自定义电击。
    """

    # -------------------- 死亡 --------------------
    old_dead = bool(
        previous.get(
            "dead",
            False,
        )
    )

    new_dead = bool(
        current.get(
            "dead",
            False,
        )
    )

    if (
        not old_dead
        and new_dead
    ):
        send_rule_output(
            "dead",
            "死亡",
            "否 → 是",
            current_value_pct=None,
        )

    elif (
        old_dead
        and not new_dead
    ):
        throttled_game_log(
            "bool:dead:False",
            "死亡状态解除",
            "是 → 否",
            interval=0.2,
        )

    # -------------------- 昏迷 --------------------
    old_passed = bool(
        previous.get(
            "passedOut",
            False,
        )
    )

    new_passed = bool(
        current.get(
            "passedOut",
            False,
        )
    )

    if (
        not old_passed
        and new_passed
    ):
        send_rule_output(
            "passedOut",
            "昏迷",
            "否 → 是",
            current_value_pct=None,
        )

    elif (
        old_passed
        and not new_passed
    ):
        throttled_game_log(
            "bool:passedOut:False",
            "昏迷状态解除",
            "是 → 否",
            interval=0.2,
        )

    # -------------------- 跳跃 --------------------
    try:
        old_jump_seq = int(
            previous.get(
                "jumpSeq",
                0,
            )
            or 0
        )
    except Exception:
        old_jump_seq = 0

    try:
        new_jump_seq = int(
            current.get(
                "jumpSeq",
                0,
            )
            or 0
        )
    except Exception:
        new_jump_seq = 0

    jump_triggered = (
        new_jump_seq > old_jump_seq
    )

    # 兼容旧 DLL：没有 jumpSeq 时，用接地 -> 离地 + 向上速度推断。
    if (
        "jumpSeq" not in current
        and bool(
            previous.get(
                "grounded",
                False,
            )
        )
        and not bool(
            current.get(
                "grounded",
                False,
            )
        )
        and velocity_y(current) > 0.35
    ):
        jump_triggered = True

    if jump_triggered:
        send_rule_output(
            "jump",
            "跳跃",
            (
                f"jumpSeq {old_jump_seq} → {new_jump_seq}"
                if "jumpSeq" in current
                else "接地 → 离地（推断）"
            ),
            current_value_pct=None,
        )

    # -------------------- 开始攀爬 --------------------
    if (
        not bool(
            previous.get(
                "climbing",
                False,
            )
        )
        and bool(
            current.get(
                "climbing",
                False,
            )
        )
    ):
        send_rule_output(
            "climbStart",
            "开始攀爬",
            "否 → 是",
            current_value_pct=None,
        )

    # -------------------- 蹲下 --------------------
    if (
        not bool(
            previous.get(
                "crouching",
                False,
            )
        )
        and bool(
            current.get(
                "crouching",
                False,
            )
        )
    ):
        send_rule_output(
            "crouchStart",
            "蹲下",
            "否 → 是",
            current_value_pct=None,
        )

    # -------------------- 其他布尔事件记日志 --------------------
    boolean_fields = [
        ("climbing", "攀爬"),
        ("grounded", "接地"),
        ("crouching", "蹲下"),
    ]

    for key, name in boolean_fields:
        old = bool(
            previous.get(
                key,
                False,
            )
        )

        new = bool(
            current.get(
                key,
                False,
            )
        )

        if old == new:
            continue

        throttled_game_log(
            f"bool:{key}:{new}",
            name,
            (
                f"{'是' if old else '否'} "
                f"→ "
                f"{'是' if new else '否'}"
            ),
            interval=0.2,
        )

def handle_game_rules(current, previous):
    if current.get("hasCharacter", True) is False:
        return

    if previous is None:
        return

    if previous.get("hasCharacter", True) is False:
        return

    # 先记录死亡 / 昏迷边沿事件。
    # 一旦当前处于死亡/昏迷，下面的安全锁会立即清输出并阻断其余规则。
    handle_boolean_events(
        current,
        previous,
    )

    # 死亡 / 昏迷安全锁：进入状态时立即清除正在播放的输出，
    # 并停止本包后续所有自动规则；自定义/手动发送入口也有同一层检查。
    if peak_is_incapacitated(current):
        was_incapacitated = peak_is_incapacitated(previous)
        if not was_incapacitated:
            clear_device_output("角色死亡/昏迷，安全锁立即停止全部电击")
            add_log("系统", "死亡/昏迷安全锁", "已屏蔽全部电击，恢复正常状态后自动解除")
        return

    handle_item_rules(
        current,
        previous,
    )

    handle_item_state_rules(
        current,
        previous,
    )

    handle_speed_rules(
        current,
        previous,
    )

    # -------------------- 体力消耗 --------------------
    current_stamina_pct = stamina_percent(
        current
    )
    previous_stamina_pct = stamina_percent(
        previous
    )

    stamina_drop = (
        previous_stamina_pct
        - current_stamina_pct
    )

    stamina_cfg = get_rule_copy(
        "staminaUse"
    )

    try:
        stamina_trigger_delta = max(
            0.1,
            float(
                stamina_cfg.get(
                    "trigger_delta",
                    1.0,
                )
            ),
        )
    except Exception:
        stamina_trigger_delta = 1.0

    if stamina_drop >= stamina_trigger_delta:
        send_rule_output(
            "staminaUse",
            "体力消耗",
            (
                f"{previous_stamina_pct:.1f}% "
                f"→ {current_stamina_pct:.1f}% "
                f"(下降 {stamina_drop:.1f}%)"
            ),
            current_value_pct=current_stamina_pct,
            change_delta_pct=stamina_drop,
        )

    elif (
        current_stamina_pct
        > previous_stamina_pct + 0.5
    ):
        throttled_game_log(
            "stamina:recover",
            "体力恢复",
            (
                f"{previous_stamina_pct:.1f}% "
                f"→ {current_stamina_pct:.1f}%"
            ),
        )

    current_hp = round(float(current.get("hp", 100.0)), 1)
    previous_hp = round(float(previous.get("hp", 100.0)), 1)

    # 记录恢复/回血事件，但不会处罚。
    if current_hp > previous_hp:
        throttled_game_log(
            "hp:recover",
            "血量恢复",
            f"{previous_hp:.1f}% → {current_hp:.1f}%",
        )

    # 状态读取优先按 statusNames 动态解析；旧 DLL 才回退固定下标。
    for key, name, fallback_index, _ in RULE_META:
        if fallback_index is None:
            continue
        old = status_percent_for_rule(previous, key, fallback_index)
        new = status_percent_for_rule(current, key, fallback_index)
        if old is None or new is None:
            continue
        if new < old:
            throttled_game_log(
                f"{key}:recover",
                f"{name}恢复",
                f"{old:.1f}% → {new:.1f}%",
            )

    if current_hp < previous_hp:
        hp_drop = previous_hp - current_hp
        send_rule_output(
            "hp",
            "血量下降",
            f"{previous_hp:.1f}% → {current_hp:.1f}%",
            current_value_pct=current_hp,
            change_delta_pct=hp_drop,
        )

    for key, name, fallback_index, _ in RULE_META:
        if fallback_index is None:
            continue
        old = status_percent_for_rule(previous, key, fallback_index)
        new = status_percent_for_rule(current, key, fallback_index)
        if old is None or new is None:
            continue
        if new > old:
            send_rule_output(
                key,
                name,
                f"{old:.1f}% → {new:.1f}%",
                current_value_pct=new,
                change_delta_pct=(new - old),
            )

    # 所有“持续模式”统一在事件判断之后调度。
    # 如果本包刚发生边沿触发，last_trigger_time 会阻止同一包重复输出。
    handle_continuous_rules(
        current
    )



# ============================================================
# 自定义 Python 编程规则
# ============================================================

CUSTOM_RULE_GUIDE_TEXT = r"""# Coyote 自定义 Python 规则开发指南

## 1. 这是什么

Coyote v13 支持从项目根目录的 `custom_rules` 文件夹加载自定义 Python 规则。

自定义规则的职责只有两件事：

1. 读取 PEAK 实时遥测，判断“当前是否应该触发”；
2. 返回希望使用的 A/B 通道参数。

真正向 DG-LAB 发送数据仍由 Coyote backend 完成，因此自定义规则仍受以下限制：

- 必须开启软件左侧的“允许电击输出”；
- 强度最终仍会经过 `GUI_INTENSITY_MAX` 硬限制；
- 普通持续时间仍会经过 `GUI_DURATION_MAX_MS` 限制；
- `-1` 不会作为无限设备指令发送，而是由 Coyote 拆成有限片段续播；
- 找不到设备、APP 断开或程序停止时不会绕过 Coyote 直接输出；
- 波形必须是软件当前已经存在的内置波形或自定义波形。

因此，自定义 Python 规则不是一个可以直接操作 DG-LAB WebSocket 的插件接口，而是一个“**安全受控的游戏条件判断接口**”。

---

## 2. 文件放在哪里

默认目录：

```text
Coyote/
└─ custom_rules/
   ├─ example_speed_climb.py
   ├─ my_rule.py
   └─ ...
```

软件内进入：

```text
自定义编程
```

可以：

- 导入 `.py`；
- 打开 `custom_rules` 文件夹；
- 创建示例；
- 重新加载全部脚本；
- 查看加载状态与错误。

修改脚本后不需要重启 PEAK，点击“重新加载脚本”即可。

---

## 3. 最小规则

```python
NAME = "高速移动"
DESCRIPTION = "速度高于 6 时触发"
ENABLED = True

MODE = "edge"
COOLDOWN = 2.0

OUTPUT = {
    "intensity_a": 5,
    "intensity_b": 0,
    "duration_a": 1200,
    "duration_b": 1200,
    "waveform_a": "脉冲",
    "waveform_b": "脉冲",
}

def condition():
    return get("speed", 0) > 6
```

### `NAME`

规则显示名称。

### `DESCRIPTION`

说明文本，可以为空。

### `ENABLED`

```python
ENABLED = True
```

才会参与判断。

示例文件默认是 `False`，避免刚导入脚本就产生自动输出。

### `MODE`

支持：

```python
MODE = "edge"
```

和：

```python
MODE = "while"
```

#### edge

条件：

```text
False → True
```

时触发一次。

例如：

```python
def condition():
    return get("speed", 0) > 6
```

速度第一次超过 6 时触发；一直保持 8 不会反复触发。速度先降到 6 以下，再次超过 6 时才会再次触发。

#### while

条件保持 `True` 时，按照 `COOLDOWN` 重复触发。

Coyote 会避免在上一个有限输出还没结束时无意义地高频叠加。

---

## 4. OUTPUT

静态输出：

```python
OUTPUT = {
    "intensity_a": 5,
    "intensity_b": 3,
    "duration_a": 1000,
    "duration_b": 1000,
    "waveform_a": "脉冲",
    "waveform_b": "气泡",
}
```

字段：

| 字段 | 含义 |
|---|---|
| `intensity_a` | A 通道协议强度等级 |
| `intensity_b` | B 通道协议强度等级 |
| `duration_a` | A 持续时间，毫秒 |
| `duration_b` | B 持续时间，毫秒 |
| `waveform_a` | A 波形名称 |
| `waveform_b` | B 波形名称 |

强度数值是 DG-LAB 协议等级，不代表实际 mA。

### `duration = -1`

自动规则允许：

```python
"duration_a": -1
```

含义是：

```text
只要 condition() 仍然为 True
→ Coyote 继续续播 A 通道
```

底层仍是有限片段，不会把 `-1` 直接发送给设备。

---

## 5. 动态输出

除了静态 `OUTPUT`，还可以写：

```python
def output():
    speed = get("speed", 0)

    return {
        "intensity_a": min(8, 3 + int(speed / 2)),
        "intensity_b": 0,
        "duration_a": 800,
        "duration_b": 800,
        "waveform_a": "脉冲",
        "waveform_b": "脉冲",
    }
```

`output()` 返回的字段会覆盖 `OUTPUT` 中对应字段。

即使动态计算得到的数值超过软件硬限制，backend 最终发送前仍会裁剪到允许范围。

---

## 6. condition()

每个脚本必须提供：

```python
def condition():
    ...
```

必须返回可以解释为 `True / False` 的值。

例如：

```python
def condition():
    return (
        get("speed", 0) > 5
        and get("climbing", False)
    )
```

---

## 7. detail()

可选：

```python
def detail():
    return (
        "速度="
        + str(round(get("speed", 0), 2))
    )
```

它只用于事件日志，方便判断是哪一个条件触发。

如果不写，Coyote 会自动生成普通说明。

---

# 8. 可用辅助函数

自定义规则不能 `import` 模块，也不能调用文件、网络、系统命令。

提供以下函数读取游戏状态。

## `get(path, default=None)`

读取当前遥测。

```python
get("hp", 100)
get("speed", 0)
get("climbing", False)
get("position.x", 0)
get("heldItem.name", "")
get("inventory.backpackItems", [])
```

支持点号路径。

---

## `prev(path, default=None)`

读取上一包遥测。

```python
prev("speed", 0)
prev("hp", 100)
```

---

## `changed(path)`

字段是否发生变化：

```python
changed("heldItem.name")
```

---

## `increased(path)`

当前数值是否大于上一包：

```python
increased("speed")
```

---

## `decreased(path)`

当前数值是否小于上一包：

```python
decreased("hp")
```

---

## `status(name)`

读取异常状态百分数，范围按 UI 习惯返回 `0 ~ 100`。

```python
status("Poison")
status("中毒")
status("Cold")
status("寒冷")
```

例如：

```python
def condition():
    return status("Poison") > 40
```

---

## `held(name=None)`

不传参数：

```python
held()
```

返回当前手持物名称。

传入名称：

```python
held("Lantern")
```

返回当前手持物名称是否包含 `Lantern`，忽略大小写。

---

## `backpack(name=None)`

不传参数返回背包物品列表：

```python
backpack()
```

传名称返回是否存在：

```python
backpack("Rope")
```

---

## `pocket(name=None)`

与 `backpack()` 类似，但读取普通口袋槽位。

---

# 9. 示例：速度 + 攀爬

```python
NAME = "高速攀爬"
DESCRIPTION = "正在攀爬并且速度高于 3"
ENABLED = False

MODE = "edge"
COOLDOWN = 3.0

OUTPUT = {
    "intensity_a": 4,
    "intensity_b": 4,
    "duration_a": 1000,
    "duration_b": 1000,
    "waveform_a": "脉冲",
    "waveform_b": "气泡",
}

def condition():
    return (
        get("climbing", False)
        and get("speed", 0) > 3
    )

def detail():
    return (
        "攀爬="
        + str(get("climbing", False))
        + "，速度="
        + str(round(get("speed", 0), 2))
    )
```

---

# 10. 示例：拿着特定物品时持续

```python
NAME = "手持灯时持续"
DESCRIPTION = "拿着名称包含 Lantern 的物品时，A 通道持续"
ENABLED = False

MODE = "edge"
COOLDOWN = 2.0

OUTPUT = {
    "intensity_a": 3,
    "intensity_b": 0,
    "duration_a": -1,
    "duration_b": 1000,
    "waveform_a": "气泡",
    "waveform_b": "脉冲",
}

def condition():
    return held("Lantern")
```

因为 A 的 `duration_a = -1`，只要 `condition()` 保持 `True`，Coyote 就会继续续播 A。

---

# 11. 示例：中毒与低血量组合

```python
NAME = "中毒低血量"
DESCRIPTION = "中毒超过 20%，并且 HP 低于 40"
ENABLED = False

MODE = "while"
COOLDOWN = 4.0

OUTPUT = {
    "intensity_a": 4,
    "intensity_b": 2,
    "duration_a": 800,
    "duration_b": 800,
    "waveform_a": "脉冲",
    "waveform_b": "气泡",
}

def condition():
    return (
        status("Poison") > 20
        and get("hp", 100) < 40
    )
```

---

# 12. 允许使用的基础 Python 函数

脚本可以使用：

```text
abs
min
max
round
len
sum
any
all
int
float
str
bool
list
tuple
```

也支持：

- 变量赋值；
- `if`；
- `return`；
- 数学运算；
- 比较；
- `and / or / not`；
- 列表、元组、字典；
- f-string。

---

# 13. 被禁止的内容

为了避免一个自定义规则直接获得电脑和设备控制权限，Coyote 自定义规则会拒绝：

```python
import os
import subprocess
open(...)
exec(...)
eval(...)
__import__(...)
```

还会拒绝：

- 类定义；
- `while` / `for` 循环；
- 文件操作；
- 网络操作；
- 属性链访问；
- `global / nonlocal`；
- `yield / await`；
- 生成器与推导式；
- 未列入允许清单的函数调用。

因此它是 Python 语法的受控子集，而不是完整的任意 Python 执行环境。

---

# 14. 常见错误

## “禁止使用 Import”

删除：

```python
import ...
```

只使用 Coyote 提供的辅助函数。

## “只允许定义 condition/detail/output”

顶层自定义函数只允许：

```python
condition
detail
output
```

## “缺少 condition()”

每个脚本必须有：

```python
def condition():
    return ...
```

## 波形不存在

确认软件“自定义波形”页面里已经存在该波形，并重新加载脚本。

---

# 15. 调试建议

建议最开始这样写：

```python
ENABLED = False
```

确认脚本在“自定义编程”页面显示：

```text
已加载
```

再改成：

```python
ENABLED = True
```

并先保持软件总输出关闭。

此时游戏事件日志仍可用于验证条件判断，但不会向设备输出。

确认逻辑正确后，再手动开启总输出。
"""
APP_INTRO_TEXT = r"""# Coyote / PEAK Controller 软件介绍

## Coyote 是什么

Coyote / PEAK Controller 是一个面向 **PEAK × DG-LAB** 联动场景的 Windows 桌面控制程序。

它将三个部分连接起来：

```text
PEAK
 ↓
BepInEx / Coyote.dll
 ↓ UDP 实时遥测
Coyote Controller
 ↓ DG-LAB V4 WebSocket
DG-LAB APP
 ↓
设备
```

软件的核心不是简单地“按一下按钮发送一次”，而是把 PEAK 游戏中的状态、动作和物品变化转换成可配置规则。

---

## 核心能力

### 1. PEAK 实时遥测

Coyote 可以读取并展示：

- 血量；
- 体力；
- 死亡 / 昏迷；
- 攀爬；
- 接地；
- 蹲下；
- 速度与三维坐标；
- 朝向与移动速度；
- 当前手持物；
- 普通口袋物品；
- 背包物品；
- 最近使用 / 食用物品；
- 15 种 PEAK 异常状态；
- CharacterData / ItemSystem 的运行时扩展字段；
- 原始 JSON 遥测。

PEAK 在大厅、加载和局内状态也会分别显示。

---

### 2. 游戏规则系统

内置规则覆盖：

- 血量下降；
- 死亡；
- 昏迷；
- 体力消耗；
- 速度低于阈值；
- 速度高于阈值；
- 跳跃；
- 开始攀爬；
- 蹲下；
- 拿起手持物；
- 背包装入物品；
- 15 种异常状态。

规则支持：

- 单独启用 / 禁用；
- 分组一键启用 / 禁用；
- A/B 通道独立参数；
- 独立波形；
- 独立持续时间；
- 冷却；
- 单次 / 持续触发；
- 百分比动态档位；
- 按当前状态自动改变波形与协议强度等级；
- `-1` 条件持续语义。

所有自动规则新建时默认关闭。

---

### 3. 自定义 Python 编程规则

v13 加入自定义规则系统。

玩家可以自己编写：

```python
def condition():
    return (
        get("speed", 0) > 5
        and get("climbing", False)
    )
```

也可以自己编程决定输出参数：

```python
def output():
    speed = get("speed", 0)

    return {
        "intensity_a": min(8, 2 + int(speed)),
        "duration_a": 800,
        "waveform_a": "脉冲",
    }
```

脚本不会直接获得 DG-LAB WebSocket。

所有输出仍通过 Coyote backend 的统一限制和总输出开关。

---

### 4. 自定义波形

软件支持：

- 创建波形；
- 编辑；
- 重命名；
- 删除；
- HEX 帧校验；
- JSON 导入 / 导出；
- 在规则和手动控制页面直接选择。

自定义 Python 脚本也可以引用已经加载的波形名称。

---

### 5. DG-LAB V4 配对与手动控制

Coyote 可以自动启动随软件分发的 DG-LAB V4 WebSocket 服务，并提供：

- APP 扫码配对；
- 设备状态；
- slotId；
- A/B 通道独立控制；
- 波形快捷选择；
- 临时播放；
- A+B 联动；
- 立即停止。

协议强度数字仅表示 DG-LAB 协议等级，不代表实际 mA。

---

### 6. BepInEx 管理

软件内置 PEAK BepInEx 管理页面，可以：

- 自动检测 PEAK 安装路径；
- 检测 BepInEx；
- 安装 / 修复 PEAK BepInExPack；
- 从本地 ZIP 安装；
- 打开 `BepInEx/plugins`；
- 安装 / 更新 Coyote.dll；
- 覆盖前备份旧文件。

因此不必为了安装基础 BepInEx 框架而依赖额外 Mod Manager。

---

### 7. PEAK 路径检测

自动检测支持：

- 已保存路径；
- 正在运行的 `PEAK.exe`；
- Steam 注册表；
- `libraryfolders.vdf`；
- `appmanifest_3527290.acf`；
- 常见 Steam Library 固定目录。

例如：

```text
D:\steam\steamapps\common\PEAK
```

属于直接检测目标。

---

### 8. 桌面界面

界面采用 PySide6 / Qt：

- QQ 风格侧边栏；
- 多页面工作区；
- 背景图片；
- 毛玻璃效果；
- 背景模糊；
- 亮度；
- 透明度；
- 自定义主题色；
- 可折叠侧栏；
- 小窗口自适应；
- 状态卡片；
- 实时日志。

背景处理采用缓存、后台线程和防抖，避免调节模糊或更换高分辨率图片时阻塞主界面。

---

## 安全设计

Coyote 的自动规则与自定义脚本不会默认开启输出。

程序保留：

- 总输出总开关；
- 自动规则默认关闭；
- backend 强度硬限制；
- 有限底层持续片段；
- 一键停止；
- 设备断开处理；
- 程序退出清除；
- 自定义 Python 受限执行环境。

自定义脚本负责“判断”，不能直接绕过 backend 使用设备协议。

---

## 项目目录示意

```text
Coyote/
├─ coyote_gui_config.json
├─ custom_rules/
│  ├─ example_speed_climb.py
│  └─ ...
├─ src/
│  └─ Coyote/
│     └─ md/
│        ├─ 自定义规则开发指南.md
│        └─ Coyote软件介绍.md
├─ dglab-websocket-server-main/
├─ logs/
├─ assets/
└─ src/
   └─ Coyote/
      ├─ main.py
      ├─ backend.py
      ├─ ui_qt.py
      └─ Plugin.cs
```

---

## 定位

Coyote 不是 PEAK、BepInEx 或 DG-LAB 的官方产品。

它是一个独立的第三方联动控制项目，目标是提供：

```text
游戏实时遥测
+
可视化规则
+
自定义 Python 条件
+
DG-LAB V4 控制
+
便携式安装管理
```

让用户能够在一个桌面程序中完成从游戏状态读取、规则编排、脚本扩展到设备控制的完整流程。
"""
CUSTOM_RULE_EXAMPLE_TEXT = r"""# Coyote 自定义规则示例
#
# 文件放入：
#   Coyote/custom_rules/
#
# 修改后，在软件“自定义编程”页面点击“重新加载脚本”。
#
# 示例默认关闭，确认逻辑后自行改成 True。

NAME = "高速攀爬示例"
DESCRIPTION = "正在攀爬并且速度超过 3 时触发"
ENABLED = False

# edge:
#   False -> True 时触发一次
#
# while:
#   条件保持 True 时按冷却重复
MODE = "edge"
COOLDOWN = 3.0

OUTPUT = {
    "intensity_a": 4,
    "intensity_b": 2,
    "duration_a": 1000,
    "duration_b": 1000,
    "waveform_a": "脉冲",
    "waveform_b": "气泡",
}


def condition():
    return (
        get("climbing", False)
        and get("speed", 0) > 3
    )


def detail():
    return (
        "高速攀爬：speed="
        + str(
            round(
                get("speed", 0),
                2,
            )
        )
    )


# 可选：根据当前游戏状态动态覆盖 OUTPUT。
#
# def output():
#     speed = get("speed", 0)
#
#     return {
#         "intensity_a": min(
#             8,
#             2 + int(speed / 2),
#         ),
#         "duration_a": 800,
#     }
"""

# 顶层只允许这些配置名称。
CUSTOM_RULE_META_NAMES = {
    "NAME",
    "DESCRIPTION",
    "ENABLED",
    "MODE",
    "COOLDOWN",
    "OUTPUT",
}

CUSTOM_RULE_FUNCTION_NAMES = {
    "condition",
    "detail",
    "output",
}

CUSTOM_RULE_SAFE_CALLS = {
    # Coyote helpers
    "get",
    "prev",
    "status",
    "held",
    "backpack",
    "pocket",
    "changed",
    "increased",
    "decreased",

    # safe builtins
    "abs",
    "min",
    "max",
    "round",
    "len",
    "sum",
    "any",
    "all",
    "int",
    "float",
    "str",
    "bool",
    "list",
    "tuple",
}

CUSTOM_RULE_SAFE_BUILTINS = {
    "abs": abs,
    "min": min,
    "max": max,
    "round": round,
    "len": len,
    "sum": sum,
    "any": any,
    "all": all,
    "int": int,
    "float": float,
    "str": str,
    "bool": bool,
    "list": list,
    "tuple": tuple,
}

_CUSTOM_FORBIDDEN_NODES = (
    ast.Import,
    ast.ImportFrom,
    ast.ClassDef,
    ast.While,
    ast.For,
    ast.AsyncFor,
    ast.AsyncFunctionDef,
    ast.With,
    ast.AsyncWith,
    ast.Try,
    ast.Raise,
    ast.Delete,
    ast.Global,
    ast.Nonlocal,
    ast.Yield,
    ast.YieldFrom,
    ast.Await,
    ast.Lambda,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
    ast.Attribute,
    ast.NamedExpr,
)


class CustomRuleValidationError(Exception):
    pass


class _CustomRuleValidator(ast.NodeVisitor):
    def visit(self, node):
        if isinstance(
            node,
            _CUSTOM_FORBIDDEN_NODES,
        ):
            raise CustomRuleValidationError(
                "禁止使用 Python 语法："
                + type(node).__name__
            )

        return super().visit(node)

    def visit_Name(self, node):
        if str(node.id).startswith("__"):
            raise CustomRuleValidationError(
                "禁止访问双下划线名称"
            )

        self.generic_visit(node)

    def visit_Call(self, node):
        if not isinstance(
            node.func,
            ast.Name,
        ):
            raise CustomRuleValidationError(
                "函数调用只能使用 Coyote 允许的函数名"
            )

        name = node.func.id

        if name not in CUSTOM_RULE_SAFE_CALLS:
            raise CustomRuleValidationError(
                f"禁止调用函数：{name}"
            )

        self.generic_visit(node)


def _validate_custom_rule_ast(
    tree,
):
    for statement in tree.body:
        # 顶层文档字符串允许。
        if (
            isinstance(
                statement,
                ast.Expr,
            )
            and isinstance(
                statement.value,
                ast.Constant,
            )
            and isinstance(
                statement.value.value,
                str,
            )
        ):
            continue

        if isinstance(
            statement,
            ast.Assign,
        ):
            for target in statement.targets:
                if not isinstance(
                    target,
                    ast.Name,
                ):
                    raise CustomRuleValidationError(
                        "顶层配置只能赋值给普通名称"
                    )

                if (
                    target.id
                    not in CUSTOM_RULE_META_NAMES
                ):
                    raise CustomRuleValidationError(
                        "不允许的顶层配置："
                        + target.id
                    )

            continue

        if isinstance(
            statement,
            ast.AnnAssign,
        ):
            if (
                not isinstance(
                    statement.target,
                    ast.Name,
                )
                or statement.target.id
                not in CUSTOM_RULE_META_NAMES
            ):
                raise CustomRuleValidationError(
                    "顶层类型标注只允许用于规则配置项"
                )

            continue

        if isinstance(
            statement,
            ast.FunctionDef,
        ):
            if (
                statement.name
                not in CUSTOM_RULE_FUNCTION_NAMES
            ):
                raise CustomRuleValidationError(
                    "顶层只允许定义 condition / detail / output"
                )

            if (
                statement.decorator_list
                or statement.args.args
                or statement.args.posonlyargs
                or statement.args.kwonlyargs
                or statement.args.vararg
                or statement.args.kwarg
            ):
                raise CustomRuleValidationError(
                    f"{statement.name}() 必须是不带参数的普通函数"
                )

            continue

        raise CustomRuleValidationError(
            "不允许的顶层语句："
            + type(statement).__name__
        )

    _CustomRuleValidator().visit(
        tree
    )


def _path_get(
    obj,
    path,
    default=None,
):
    if not isinstance(
        path,
        str,
    ):
        return default

    current = obj

    for part in path.split("."):
        if isinstance(
            current,
            dict,
        ):
            if part not in current:
                return default

            current = current[
                part
            ]

        elif isinstance(
            current,
            (list, tuple),
        ):
            try:
                index = int(
                    part
                )
            except Exception:
                return default

            if (
                index < 0
                or index >= len(current)
            ):
                return default

            current = current[
                index
            ]

        else:
            return default

    return current


_STATUS_NAME_TO_INDEX = {
    raw: i
    for i, (
        raw,
        _,
    ) in enumerate(
        STATUS_ORDER
    )
}

_STATUS_NAME_TO_INDEX.update({
    zh: i
    for i, (
        _,
        zh,
    ) in enumerate(
        STATUS_ORDER
    )
})


class _CustomRuleContext:
    def __init__(
        self,
        current,
        previous,
    ):
        self.current = (
            current
            if isinstance(
                current,
                dict,
            )
            else {}
        )

        self.previous = (
            previous
            if isinstance(
                previous,
                dict,
            )
            else {}
        )

    def get(
        self,
        path,
        default=None,
    ):
        return _path_get(
            self.current,
            path,
            default,
        )

    def prev(
        self,
        path,
        default=None,
    ):
        return _path_get(
            self.previous,
            path,
            default,
        )

    def status(
        self,
        name,
    ):
        raw_name = str(name)
        fallback_index = _STATUS_NAME_TO_INDEX.get(raw_name, None)

        # 中文名称先转换到英文规则键。
        rule_key = raw_name
        for raw, zh in STATUS_ORDER:
            if raw_name == zh:
                rule_key = raw
                break

        value = status_percent_for_rule(
            self.current,
            rule_key,
            fallback_index,
        )
        return 0.0 if value is None else value

    @staticmethod
    def _name_matches(
        actual,
        expected,
    ):
        actual = str(
            actual
            or ""
        ).strip()

        if expected is None:
            return actual

        expected = str(
            expected
            or ""
        ).strip()

        if not expected:
            return bool(
                actual
            )

        return (
            expected.lower()
            in actual.lower()
        )

    def held(
        self,
        name=None,
    ):
        held_item = (
            self.current.get(
                "heldItem"
            )
            or {}
        )

        actual = held_item.get(
            "name",
            "",
        )

        return self._name_matches(
            actual,
            name,
        )

    def _inventory_items(
        self,
        key,
    ):
        inventory = (
            self.current.get(
                "inventory"
            )
            or {}
        )

        values = inventory.get(
            key,
            [],
        )

        if not isinstance(
            values,
            list,
        ):
            return []

        return [
            str(value)
            for value in values
            if str(
                value
                or ""
            ).strip()
        ]

    def backpack(
        self,
        name=None,
    ):
        items = self._inventory_items(
            "backpackItems"
        )

        if name is None:
            return list(
                items
            )

        target = str(
            name
            or ""
        ).lower()

        return any(
            target in item.lower()
            for item in items
        )

    def pocket(
        self,
        name=None,
    ):
        items = self._inventory_items(
            "pocketItems"
        )

        if name is None:
            return list(
                items
            )

        target = str(
            name
            or ""
        ).lower()

        return any(
            target in item.lower()
            for item in items
        )

    def changed(
        self,
        path,
    ):
        sentinel = object()

        return (
            self.get(
                path,
                sentinel,
            )
            != self.prev(
                path,
                sentinel,
            )
        )

    def increased(
        self,
        path,
    ):
        try:
            return (
                float(
                    self.get(
                        path,
                        0,
                    )
                )
                > float(
                    self.prev(
                        path,
                        0,
                    )
                )
            )
        except Exception:
            return False

    def decreased(
        self,
        path,
    ):
        try:
            return (
                float(
                    self.get(
                        path,
                        0,
                    )
                )
                < float(
                    self.prev(
                        path,
                        0,
                    )
                )
            )
        except Exception:
            return False


def _install_custom_context_helpers(
    namespace,
):
    namespace[
        "_coyote_context"
    ] = _CustomRuleContext(
        {},
        {},
    )

    namespace["get"] = (
        lambda path,
        default=None:
        namespace[
            "_coyote_context"
        ].get(
            path,
            default,
        )
    )

    namespace["prev"] = (
        lambda path,
        default=None:
        namespace[
            "_coyote_context"
        ].prev(
            path,
            default,
        )
    )

    namespace["status"] = (
        lambda name:
        namespace[
            "_coyote_context"
        ].status(
            name
        )
    )

    namespace["held"] = (
        lambda name=None:
        namespace[
            "_coyote_context"
        ].held(
            name
        )
    )

    namespace["backpack"] = (
        lambda name=None:
        namespace[
            "_coyote_context"
        ].backpack(
            name
        )
    )

    namespace["pocket"] = (
        lambda name=None:
        namespace[
            "_coyote_context"
        ].pocket(
            name
        )
    )

    namespace["changed"] = (
        lambda path:
        namespace[
            "_coyote_context"
        ].changed(
            path
        )
    )

    namespace["increased"] = (
        lambda path:
        namespace[
            "_coyote_context"
        ].increased(
            path
        )
    )

    namespace["decreased"] = (
        lambda path:
        namespace[
            "_coyote_context"
        ].decreased(
            path
        )
    )


def _normalize_custom_mode(
    value,
):
    value = str(
        value
        or "edge"
    ).strip().lower()

    if value in {
        "while",
        "repeat",
        "continuous",
    }:
        return "while"

    return "edge"


def _normalize_custom_cooldown(
    value,
):
    try:
        return max(
            0.0,
            min(
                60.0,
                float(value),
            ),
        )
    except Exception:
        return 2.0


def _normalize_custom_output(
    value,
):
    if not isinstance(
        value,
        dict,
    ):
        value = {}

    intensity_a = clamp_int(
        value.get(
            "intensity_a",
            0,
        )
    )

    intensity_b = clamp_int(
        value.get(
            "intensity_b",
            0,
        )
    )

    duration_a = clamp_duration(
        value.get(
            "duration_a",
            1000,
        )
    )

    duration_b = clamp_duration(
        value.get(
            "duration_b",
            1000,
        )
    )

    waveform_a = str(
        value.get(
            "waveform_a",
            "脉冲",
        )
        or "脉冲"
    )

    waveform_b = str(
        value.get(
            "waveform_b",
            "脉冲",
        )
        or "脉冲"
    )

    if (
        waveform_a
        not in COYOTE_WAVEFORMS
    ):
        waveform_a = "脉冲"

    if (
        waveform_b
        not in COYOTE_WAVEFORMS
    ):
        waveform_b = "脉冲"

    return {
        "intensity_a": intensity_a,
        "intensity_b": intensity_b,
        "duration_a": duration_a,
        "duration_b": duration_b,
        "waveform_a": waveform_a,
        "waveform_b": waveform_b,
    }


def ensure_custom_rule_assets():
    CUSTOM_RULE_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    DOC_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    if not CUSTOM_RULE_DOC_FILE.exists():
        CUSTOM_RULE_DOC_FILE.write_text(
            CUSTOM_RULE_GUIDE_TEXT,
            encoding="utf-8",
        )

    if not APP_INTRO_DOC_FILE.exists():
        APP_INTRO_DOC_FILE.write_text(
            APP_INTRO_TEXT,
            encoding="utf-8",
        )

    if not CUSTOM_RULE_EXAMPLE_FILE.exists():
        CUSTOM_RULE_EXAMPLE_FILE.write_text(
            CUSTOM_RULE_EXAMPLE_TEXT,
            encoding="utf-8",
        )


def write_custom_rule_example(
    overwrite=False,
):
    ensure_custom_rule_assets()

    if (
        CUSTOM_RULE_EXAMPLE_FILE.exists()
        and not overwrite
    ):
        return (
            False,
            str(
                CUSTOM_RULE_EXAMPLE_FILE
            ),
        )

    CUSTOM_RULE_EXAMPLE_FILE.write_text(
        CUSTOM_RULE_EXAMPLE_TEXT,
        encoding="utf-8",
    )

    return (
        True,
        str(
            CUSTOM_RULE_EXAMPLE_FILE
        ),
    )


def _load_custom_rule_file(
    path,
):
    path = Path(
        path
    )

    base = {
        "file": path.name,
        "path": str(path),
        "name": path.stem,
        "description": "",
        "enabled": False,
        "mode": "edge",
        "cooldown": 2.0,
        "output_static": _normalize_custom_output(
            {}
        ),
        "namespace": None,
        "condition_func": None,
        "detail_func": None,
        "output_func": None,
        "error": "",
    }

    try:
        source = path.read_text(
            encoding="utf-8",
        )

        if len(source) > 100_000:
            raise CustomRuleValidationError(
                "脚本超过 100 KB"
            )

        tree = ast.parse(
            source,
            filename=str(path),
            mode="exec",
        )

        _validate_custom_rule_ast(
            tree
        )

        namespace = {
            "__builtins__": dict(
                CUSTOM_RULE_SAFE_BUILTINS
            )
        }

        namespace.update(
            CUSTOM_RULE_SAFE_BUILTINS
        )

        _install_custom_context_helpers(
            namespace
        )

        code = compile(
            tree,
            str(path),
            "exec",
        )

        exec(
            code,
            namespace,
            namespace,
        )

        condition_func = namespace.get(
            "condition"
        )

        if not callable(
            condition_func
        ):
            raise CustomRuleValidationError(
                "缺少 condition()"
            )

        detail_func = namespace.get(
            "detail"
        )

        output_func = namespace.get(
            "output"
        )

        if (
            detail_func is not None
            and not callable(
                detail_func
            )
        ):
            raise CustomRuleValidationError(
                "detail 必须是函数"
            )

        if (
            output_func is not None
            and not callable(
                output_func
            )
        ):
            raise CustomRuleValidationError(
                "output 必须是函数"
            )

        base.update({
            "name": str(
                namespace.get(
                    "NAME",
                    path.stem,
                )
                or path.stem
            )[:80],

            "description": str(
                namespace.get(
                    "DESCRIPTION",
                    "",
                )
                or ""
            )[:300],

            "enabled": bool(
                namespace.get(
                    "ENABLED",
                    False,
                )
            ),

            "mode": _normalize_custom_mode(
                namespace.get(
                    "MODE",
                    "edge",
                )
            ),

            "cooldown": _normalize_custom_cooldown(
                namespace.get(
                    "COOLDOWN",
                    2.0,
                )
            ),

            "output_static": _normalize_custom_output(
                namespace.get(
                    "OUTPUT",
                    {},
                )
            ),

            "namespace": namespace,
            "condition_func": condition_func,
            "detail_func": detail_func,
            "output_func": output_func,
        })

    except Exception as e:
        base[
            "error"
        ] = (
            f"{type(e).__name__}: {e}"
        )

        base[
            "enabled"
        ] = False

    return base


def load_custom_rules():
    ensure_custom_rule_assets()

    loaded = {}

    for path in sorted(
        CUSTOM_RULE_DIR.glob(
            "*.py"
        )
    ):
        if path.name.startswith(
            "_"
        ):
            continue

        item = _load_custom_rule_file(
            path
        )

        loaded[
            path.name
        ] = item

    with custom_rule_lock:
        custom_rules.clear()
        custom_rules.update(
            loaded
        )

        # 脚本重新加载后，边沿状态重新开始。
        custom_rule_runtime.clear()

    error_count = sum(
        1
        for item in loaded.values()
        if item.get(
            "error"
        )
    )

    add_log(
        "系统",
        "自定义规则已加载",
        (
            f"{len(loaded)} 个脚本，"
            f"{error_count} 个错误"
        ),
    )

    return (
        len(loaded),
        error_count,
    )


def custom_rule_statuses():
    with custom_rule_lock:
        items = list(
            custom_rules.values()
        )

    result = []

    for item in items:
        result.append({
            "file": item.get(
                "file",
                "",
            ),
            "path": item.get(
                "path",
                "",
            ),
            "name": item.get(
                "name",
                "",
            ),
            "description": item.get(
                "description",
                "",
            ),
            "enabled": bool(
                item.get(
                    "enabled",
                    False,
                )
            ),
            "mode": item.get(
                "mode",
                "edge",
            ),
            "cooldown": item.get(
                "cooldown",
                2.0,
            ),
            "error": item.get(
                "error",
                "",
            ),
        })

    return result


def _custom_rule_output_for_context(
    item,
    current,
    previous,
):
    namespace = item.get(
        "namespace"
    )

    if not isinstance(
        namespace,
        dict,
    ):
        return (
            _normalize_custom_output(
                {}
            ),
            "",
        )

    namespace[
        "_coyote_context"
    ] = _CustomRuleContext(
        current,
        previous,
    )

    output_value = dict(
        item.get(
            "output_static",
            {},
        )
    )

    output_func = item.get(
        "output_func"
    )

    if callable(
        output_func
    ):
        dynamic = output_func()

        if dynamic is not None:
            if not isinstance(
                dynamic,
                dict,
            ):
                raise ValueError(
                    "output() 必须返回 dict 或 None"
                )

            output_value.update(
                dynamic
            )

    detail = ""

    detail_func = item.get(
        "detail_func"
    )

    if callable(
        detail_func
    ):
        raw_detail = detail_func()

        if raw_detail is not None:
            detail = str(
                raw_detail
            )[:500]

    return (
        _normalize_custom_output(
            output_value
        ),
        detail,
    )


def _custom_rule_effective_interval(
    item,
    output_cfg,
):
    cooldown = _normalize_custom_cooldown(
        item.get(
            "cooldown",
            2.0,
        )
    )

    duration_a = (
        resolve_rule_duration_ms(
            output_cfg.get(
                "duration_a",
                1000,
            )
        )
        / 1000.0
    )

    duration_b = (
        resolve_rule_duration_ms(
            output_cfg.get(
                "duration_b",
                1000,
            )
        )
        / 1000.0
    )

    return max(
        1.0,
        cooldown,
        duration_a,
        duration_b,
    )


def _send_custom_channel(
    channel,
    intensity,
    configured_duration,
    waveform_name,
):
    if peak_is_incapacitated():
        return (False, "角色死亡/昏迷，安全锁已屏蔽全部电击")

    slot_id = get_slot_id()

    if not slot_id:
        return (
            False,
            "没有检测到郊狼设备",
        )

    intensity = clamp_int(
        intensity
    )

    actual_duration = (
        resolve_rule_duration_ms(
            configured_duration
        )
    )

    waveform_name = str(
        waveform_name
        or "脉冲"
    )

    waveform = COYOTE_WAVEFORMS.get(
        waveform_name
    )

    if waveform is None:
        return (
            False,
            f"波形不存在：{waveform_name}",
        )

    intensity_result = send_rpc(
        "device.op",
        {
            "s": slot_id,
            "c": (
                0
                if int(channel) == 0
                else 1
            ),
            "t": 4,
            "v": intensity,
            "d": actual_duration,
            "im": True,
        },
    )

    if intensity <= 0:
        return intensity_result

    waveform_result = send_rpc(
        "device.op",
        {
            "s": slot_id,
            "c": (
                0
                if int(channel) == 0
                else 1
            ),
            "t": 0,
            "d": actual_duration,
            "im": True,
            "v": waveform,
        },
    )

    ok = (
        intensity_result[0]
        and waveform_result[0]
    )

    return (
        ok,
        (
            "已发送"
            if ok
            else (
                f"强度={intensity_result[1]}; "
                f"波形={waveform_result[1]}"
            )
        ),
    )


def send_custom_rule_output(
    item,
    output_cfg,
    detail,
    *,
    continuation=False,
):
    global output_count
    global last_output

    name = str(
        item.get(
            "name",
            item.get(
                "file",
                "自定义规则",
            ),
        )
    )

    if not master_output_enabled:
        add_log(
            "自定义规则",
            f"{name} 未输出",
            (
                (detail or "条件成立")
                + "；总输出开关关闭"
            ),
        )

        return False

    if not get_slot_id():
        add_log(
            "自定义规则",
            f"{name} 未输出",
            (
                (detail or "条件成立")
                + "；没有检测到郊狼设备"
            ),
        )

        return False

    mode = item.get(
        "mode",
        "edge",
    )

    send_a = True
    send_b = True

    # edge + duration=-1 时，第一次 A/B 都执行；
    # 后续只续播配置为 -1 的通道。
    if (
        continuation
        and mode != "while"
    ):
        send_a = is_continuous_duration(
            output_cfg.get(
                "duration_a",
                1000,
            )
        )

        send_b = is_continuous_duration(
            output_cfg.get(
                "duration_b",
                1000,
            )
        )

    results = []

    if send_a:
        results.append(
            _send_custom_channel(
                0,
                output_cfg.get(
                    "intensity_a",
                    0,
                ),
                output_cfg.get(
                    "duration_a",
                    1000,
                ),
                output_cfg.get(
                    "waveform_a",
                    "脉冲",
                ),
            )
        )

    if send_b:
        results.append(
            _send_custom_channel(
                1,
                output_cfg.get(
                    "intensity_b",
                    0,
                ),
                output_cfg.get(
                    "duration_b",
                    1000,
                ),
                output_cfg.get(
                    "waveform_b",
                    "脉冲",
                ),
            )
        )

    results = [
        result
        for result in results
        if result is not None
    ]

    success = (
        bool(results)
        and all(
            result[0]
            for result in results
        )
    )

    with log_lock:
        output_count += 1

        last_output = {
            "event": name,
            "change": detail,
            "a_intensity": output_cfg.get(
                "intensity_a",
                0,
            ),
            "b_intensity": output_cfg.get(
                "intensity_b",
                0,
            ),
            "a_duration": output_cfg.get(
                "duration_a",
                1000,
            ),
            "b_duration": output_cfg.get(
                "duration_b",
                1000,
            ),
            "a_waveform": output_cfg.get(
                "waveform_a",
                "脉冲",
            ),
            "b_waveform": output_cfg.get(
                "waveform_b",
                "脉冲",
            ),
            "success": success,
            "custom": True,
        }

    add_log(
        "自定义规则",
        (
            f"{name}（续播）"
            if continuation
            else name
        ),
        (
            (detail or "条件成立")
            + " | "
            + (
                "发送成功"
                if success
                else "发送失败"
            )
        ),
    )

    return success


def _custom_rule_has_hold_duration(
    output_cfg,
):
    return (
        is_continuous_duration(
            output_cfg.get(
                "duration_a",
                1000,
            )
        )
        or is_continuous_duration(
            output_cfg.get(
                "duration_b",
                1000,
            )
        )
    )


def handle_custom_rules(
    current,
    previous,
):
    with custom_rule_lock:
        items = list(
            custom_rules.items()
        )

    now = time.time()

    for file_name, item in items:
        if (
            not item.get(
                "enabled",
                False,
            )
            or item.get(
                "error"
            )
        ):
            continue

        runtime = custom_rule_runtime.setdefault(
            file_name,
            {
                "active": False,
                "last_trigger": 0.0,
                "last_error": "",
                "last_error_time": 0.0,
            },
        )

        try:
            namespace = item.get(
                "namespace"
            )

            if not isinstance(
                namespace,
                dict,
            ):
                continue

            namespace[
                "_coyote_context"
            ] = _CustomRuleContext(
                current,
                previous,
            )

            condition_func = item.get(
                "condition_func"
            )

            active = bool(
                condition_func()
            )

            output_cfg, detail = (
                _custom_rule_output_for_context(
                    item,
                    current,
                    previous,
                )
            )

            previous_active = bool(
                runtime.get(
                    "active",
                    False,
                )
            )

            runtime[
                "active"
            ] = active

            if not active:
                continue

            mode = item.get(
                "mode",
                "edge",
            )

            hold_duration = (
                _custom_rule_has_hold_duration(
                    output_cfg
                )
            )

            interval = (
                _custom_rule_effective_interval(
                    item,
                    output_cfg,
                )
            )

            last_trigger = float(
                runtime.get(
                    "last_trigger",
                    0.0,
                )
                or 0.0
            )

            initial_edge = (
                active
                and not previous_active
            )

            should_trigger = False
            continuation = False

            if mode == "while":
                if (
                    initial_edge
                    or now - last_trigger
                    >= interval
                ):
                    should_trigger = True
                    continuation = (
                        not initial_edge
                    )

            else:
                if initial_edge:
                    should_trigger = True
                    continuation = False

                elif (
                    hold_duration
                    and now - last_trigger
                    >= interval
                ):
                    # edge 模式如果某通道 duration=-1，
                    # 条件继续成立时只续播 -1 通道。
                    should_trigger = True
                    continuation = True

            if not should_trigger:
                continue

            runtime[
                "last_trigger"
            ] = now

            add_log(
                "自定义规则",
                (
                    f"{item.get('name', file_name)} 条件成立"
                ),
                detail or item.get(
                    "description",
                    "",
                ),
            )

            send_custom_rule_output(
                item,
                output_cfg,
                (
                    detail
                    or item.get(
                        "description",
                        ""
                    )
                    or "条件成立"
                ),
                continuation=continuation,
            )

        except Exception as e:
            error_text = (
                f"{type(e).__name__}: {e}"
            )

            last_error = runtime.get(
                "last_error",
                "",
            )

            last_error_time = float(
                runtime.get(
                    "last_error_time",
                    0.0,
                )
                or 0.0
            )

            if (
                error_text != last_error
                or now - last_error_time
                >= 5.0
            ):
                add_log(
                    "错误",
                    (
                        "自定义规则运行失败："
                        + str(
                            item.get(
                                "name",
                                file_name,
                            )
                        )
                    ),
                    error_text,
                )

                runtime[
                    "last_error"
                ] = error_text

                runtime[
                    "last_error_time"
                ] = now


# ============================================================
# PEAK 进程 / 场景状态
# ============================================================

def peak_process_running(force=False):
    """
    检查 Windows 中 PEAK.exe 是否仍在运行。

    这一步和 UDP 遥测分开：
      - 游戏开着但处于大厅/加载时，PEAK.exe 仍存在；
      - 但 C# 插件可能暂时没有 Character 遥测。
    """
    now = time.time()

    if (
        not force
        and (
            now
            - peak_process_cache[
                "checked_at"
            ]
            < PEAK_PROCESS_CHECK_INTERVAL
        )
    ):
        return bool(
            peak_process_cache[
                "running"
            ]
        )

    running = False

    if sys.platform.startswith("win"):
        try:
            flags = getattr(
                subprocess,
                "CREATE_NO_WINDOW",
                0,
            )

            result = subprocess.run(
                [
                    "tasklist",
                    "/FI",
                    "IMAGENAME eq PEAK.exe",
                    "/NH",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                creationflags=flags,
                timeout=2.0,
            )

            running = (
                "PEAK.exe"
                in (
                    result.stdout
                    or ""
                )
            )

        except Exception:
            # tasklist 查询异常时，用最近遥测作为保底。
            running = (
                last_peak_time > 0
                and (
                    now
                    - last_peak_time
                    <= PEAK_OFFLINE
                )
            )

    else:
        running = (
            last_peak_time > 0
            and (
                now
                - last_peak_time
                <= PEAK_OFFLINE
            )
        )

    peak_process_cache[
        "running"
    ] = bool(running)

    peak_process_cache[
        "checked_at"
    ] = now

    return bool(running)


def get_peak_runtime_state():
    """
    新版 C# 插件在大厅/加载阶段也会发送 heartbeat：
      hasCharacter=True  -> 局内 / 遥测中
      hasCharacter=False -> 大厅 / 加载中

    旧 DLL 没有 hasCharacter 时，收到 UDP 仍按局内处理。
    """
    now = time.time()

    with peak_lock:
        packet = dict(latest_peak) if latest_peak else None
        packet_time = last_peak_time

    packet_age = (
        now - packet_time
        if packet_time > 0
        else None
    )

    packet_recent = (
        packet is not None
        and packet_age is not None
        and packet_age <= PEAK_OFFLINE
    )

    if packet_recent:
        if "hasCharacter" in packet:
            has_character = bool(packet.get("hasCharacter", False))
            if has_character:
                state = "in_game"
                label = "局内 / 遥测中"
            else:
                state = "lobby_or_loading"
                label = "大厅 / 加载中"

            return {
                "state": state,
                "label": label,
                "process_running": True,
                "telemetry_active": has_character,
                "plugin_active": True,
                "scene": str(packet.get("scene", "") or ""),
                "last_packet_age": packet_age,
            }

        return {
            "state": "in_game",
            "label": "局内 / 遥测中",
            "process_running": True,
            "telemetry_active": True,
            "plugin_active": True,
            "scene": "",
            "last_packet_age": packet_age,
        }

    process_running = peak_process_running()

    if process_running:
        state = "lobby_or_loading"
        label = "大厅 / 加载中"
    else:
        state = "not_running"
        label = "PEAK 未启动"

    return {
        "state": state,
        "label": label,
        "process_running": process_running,
        "telemetry_active": False,
        "plugin_active": False,
        "scene": "",
        "last_packet_age": packet_age,
    }


# ============================================================
# 12. PEAK UDP
# ============================================================


def handle_extended_telemetry_events(current, previous):
    if previous is None:
        return

    old_scene = str(previous.get("scene", "") or "")
    new_scene = str(current.get("scene", "") or "")
    if new_scene and new_scene != old_scene:
        add_log(
            "游戏",
            "场景变化",
            f"{old_scene or '-'} → {new_scene}",
        )

    old_item = previous.get("heldItem") or {}
    new_item = current.get("heldItem") or {}

    old_name = str(old_item.get("name", "") or "")
    new_name = str(new_item.get("name", "") or "")

    if old_name != new_name:
        add_log(
            "游戏",
            "手持物变化",
            f"{old_name or '空手'} → {new_name or '空手'}",
        )

    old_inventory = previous.get("inventory") or {}
    new_inventory = current.get("inventory") or {}
    added_backpack = list_added_items(
        old_inventory.get("backpackItems", []),
        new_inventory.get("backpackItems", []),
    )
    if added_backpack:
        add_log(
            "游戏",
            "背包装入",
            "、".join(added_backpack),
        )

    for field, title in (
        ("lastUsedItem", "物品使用"),
        ("lastConsumedItem", "物品食用/消耗"),
        ("lastItemEvent", "物品事件"),
    ):
        old_event = previous.get(field) or {}
        new_event = current.get(field) or {}

        old_id = str(old_event.get("id", "") or "")
        new_id = str(new_event.get("id", "") or "")

        if new_id and new_id != old_id:
            item_name = str(new_event.get("item", "") or "-")
            detail = str(new_event.get("detail", "") or "")
            inferred = bool(new_event.get("inferred", False))
            suffix = "（推断）" if inferred else ""
            add_log(
                "游戏",
                title + suffix,
                item_name + (f" | {detail}" if detail else ""),
            )


def peak_udp_loop():
    global latest_peak
    global previous_peak
    global last_peak_time
    global peak_was_online
    global udp_socket

    udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_socket.settimeout(0.1)

    try:
        udp_socket.bind((PEAK_HOST, PEAK_PORT))
    except OSError as e:
        add_log(
            "错误",
            "PEAK UDP 监听失败",
            f"{PEAK_HOST}:{PEAK_PORT} - {e}",
        )
        return

    add_log(
        "连接",
        "等待 PEAK",
        f"UDP {PEAK_HOST}:{PEAK_PORT}",
    )

    while not stop_event.is_set():
        try:
            raw, _ = udp_socket.recvfrom(16384)
            current = json.loads(raw.decode("utf-8"))

            if not isinstance(current, dict):
                continue

            with peak_lock:
                previous_peak = latest_peak
                latest_peak = current
                last_peak_time = time.time()

                current_copy = latest_peak
                previous_copy = previous_peak

            handle_extended_telemetry_events(
                current_copy,
                previous_copy,
            )

            if not peak_was_online:
                peak_was_online = True
                add_log(
                    "连接",
                    "PEAK 插件已连接",
                    f"UDP {PEAK_PORT}",
                )

            handle_game_rules(
                current_copy,
                previous_copy,
            )

            handle_custom_rules(
                current_copy,
                previous_copy,
            )

        except socket.timeout:
            if (
                peak_was_online
                and last_peak_time
                and time.time() - last_peak_time > PEAK_OFFLINE
            ):
                peak_was_online = False
                add_log(
                    "连接",
                    "PEAK 遥测暂停",
                    "PEAK 可能处于大厅 / 加载 / 切图",
                )

        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

        except Exception as e:
            add_log("错误", "PEAK 数据处理异常", str(e))
            time.sleep(0.1)