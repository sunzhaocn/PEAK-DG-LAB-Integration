Coyote / PEAK Controller 软件介绍

Coyote 是什么

Coyote / PEAK Controller 是一个面向 PEAK × DG-LAB 联动场景的 Windows 桌面控制程序。

它将三个部分连接起来：

PEAK
 ↓
BepInEx / Coyote.dll
 ↓ UDP 实时遥测
Coyote Controller
 ↓ DG-LAB V4 WebSocket
DG-LAB APP
 ↓
设备

软件的核心不是简单地“按一下按钮发送一次”，而是把 PEAK 游戏中的状态、动作和物品变化转换成可配置规则。

核心能力

1. PEAK 实时遥测

Coyote 可以读取并展示：

血量；

体力；

死亡 / 昏迷；

攀爬；

接地；

蹲下；

速度与三维坐标；

朝向与移动速度；

当前手持物；

普通口袋物品；

背包物品；

最近使用 / 食用物品；

15 种 PEAK 异常状态；

CharacterData / ItemSystem 的运行时扩展字段；

原始 JSON 遥测。

PEAK 在大厅、加载和局内状态也会分别显示。

2. 游戏规则系统

内置规则覆盖：

血量下降；

死亡；

昏迷；

体力消耗；

速度低于阈值；

速度高于阈值；

跳跃；

开始攀爬；

蹲下；

拿起手持物；

背包装入物品；

15 种异常状态。

规则支持：

单独启用 / 禁用；

分组一键启用 / 禁用；

A/B 通道独立参数；

独立波形；

独立持续时间；

冷却；

单次 / 持续触发；

百分比动态档位；

按当前状态自动改变波形与协议强度等级；

-1 条件持续语义。

所有自动规则新建时默认关闭。

3. 自定义 Python 编程规则

v13 加入自定义规则系统。

玩家可以自己编写：

def condition():
    return (
        get("speed", 0) > 5
        and get("climbing", False)
    )

也可以自己编程决定输出参数：

def output():
    speed = get("speed", 0)

    return {
        "intensity_a": min(8, 2 + int(speed)),
        "duration_a": 800,
        "waveform_a": "脉冲",
    }

脚本不会直接获得 DG-LAB WebSocket。

所有输出仍通过 Coyote backend 的统一限制和总输出开关。

4. 自定义波形

软件支持：

创建波形；

编辑；

重命名；

删除；

HEX 帧校验；

JSON 导入 / 导出；

在规则和手动控制页面直接选择。

自定义 Python 脚本也可以引用已经加载的波形名称。

5. DG-LAB V4 配对与手动控制

Coyote 可以自动启动随软件分发的 DG-LAB V4 WebSocket 服务，并提供：

APP 扫码配对；

设备状态；

slotId；

A/B 通道独立控制；

波形快捷选择；

临时播放；

A+B 联动；

立即停止。

协议强度数字仅表示 DG-LAB 协议等级，不代表实际 mA。

6. BepInEx 管理

软件内置 PEAK BepInEx 管理页面，可以：

自动检测 PEAK 安装路径；

检测 BepInEx；

安装 / 修复 PEAK BepInExPack；

从本地 ZIP 安装；

打开 BepInEx/plugins；

安装 / 更新 Coyote.dll；

覆盖前备份旧文件。

因此不必为了安装基础 BepInEx 框架而依赖额外 Mod Manager。

7. PEAK 路径检测

自动检测支持：

已保存路径；

正在运行的 PEAK.exe；

Steam 注册表；

libraryfolders.vdf；

appmanifest_3527290.acf；

常见 Steam Library 固定目录。

例如：

D:\steam\steamapps\common\PEAK

属于直接检测目标。

8. 桌面界面

界面采用 PySide6 / Qt：

QQ 风格侧边栏；

多页面工作区；

背景图片；

毛玻璃效果；

背景模糊；

亮度；

透明度；

自定义主题色；

可折叠侧栏；

小窗口自适应；

状态卡片；

实时日志。

背景处理采用缓存、后台线程和防抖，避免调节模糊或更换高分辨率图片时阻塞主界面。

安全设计

Coyote 的自动规则与自定义脚本不会默认开启输出。

程序保留：

总输出总开关；

自动规则默认关闭；

backend 强度硬限制；

有限底层持续片段；

一键停止；

设备断开处理；

程序退出清除；

自定义 Python 受限执行环境。

自定义脚本负责“判断”，不能直接绕过 backend 使用设备协议。

项目目录示意

Coyote/
├─ coyote_gui_config.json
├─ custom_rules/
│  ├─ example_speed_climb.py
│  └─ ...
├─ docs/
│  ├─ 自定义规则开发指南.md
│  └─ Coyote软件介绍.md
├─ dglab-websocket-server-main/
├─ logs/
├─ assets/
└─ src/
   └─ Coyote/
      ├─ main.py
      ├─ backend.py
      ├─ ui_qt.py
      └─ Plugin.cs

定位

Coyote 不是 PEAK、BepInEx 或 DG-LAB 的官方产品。

它是一个独立的第三方联动控制项目，目标是提供：

游戏实时遥测
+
可视化规则
+
自定义 Python 条件
+
DG-LAB V4 控制
+
便携式安装管理

让用户能够在一个桌面程序中完成从游戏状态读取、规则编排、脚本扩展到设备控制的完整流程。