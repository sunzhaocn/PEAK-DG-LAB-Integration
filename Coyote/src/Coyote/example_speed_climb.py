# pyright: reportUndefinedVariable=false

NAME = "高速攀爬示例"
DESCRIPTION = "正在攀爬并且速度超过 3 时触发"
ENABLED = False

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