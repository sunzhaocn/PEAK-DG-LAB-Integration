# Coyote 自定义规则示例
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
