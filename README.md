# Coyote 使用与多人联机指南

> 面向 PEAK 与 DG-LAB 联动场景的桌面控制端、BepInEx 插件与多人扩展说明。

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![.NET](https://img.shields.io/badge/.NET-netstandard2.1-512BD4?style=flat-square&logo=dotnet&logoColor=white)](https://dotnet.microsoft.com/)
[![License](https://img.shields.io/badge/License-GPL--3.0-blue?style=flat-square)](LICENSE)

## 目录

- [项目概览](#项目概览)
- [功能亮点](#功能亮点)
- [快速启动](#快速启动)
- [多人联机工作流](#多人联机工作流)
- [规则系统](#规则系统)
- [安全默认](#安全默认)
- [源码构建](#源码构建)
- [常见问题](#常见问题)
- [文件结构](#文件结构)

## 项目概览

Coyote 将 PEAK 游戏内遥测、桌面规则引擎与 DG-LAB WebSocket 控制链路组合在一起：

| 模块 | 位置 | 作用 |
| --- | --- | --- |
| PEAK 插件 | `Coyote/src/Coyote/Plugin.cs` | 采集本地玩家状态并通过 UDP 发送给桌面端 |
| 多人遥测扩展 | `Coyote/src/Coyote/MultiplayerTelemetry.cs` | 采集联机玩家列表、血量、状态、位置与距离 |
| 桌面后端 | `Coyote/src/Coyote/backend.py` | 规则判断、日志、DG-LAB RPC 与配置管理 |
| 扩展规则层 | `Coyote/src/Coyote/extended_features.py` | 增加恢复、区域、随机波形、HP 渐升等规则 |
| 多人设备层 | `Coyote/src/Coyote/multiplayer_features.py` | 多 APP、多设备、玩家绑定与远程伤害输出 |
| Qt 界面 | `Coyote/src/Coyote/ui_qt.py` | 可视化配置、状态展示、日志与设备控制 |

## 功能亮点

### 本地联动

- 监听 PEAK 本地玩家血量、体力、异常状态、手持物、背包、位置与场景。
- 按规则触发 DG-LAB A/B 通道输出。
- 支持固定强度、随机强度、百分比阶梯、瞬时大变化加强和持续输出。

### 扩展规则

- HP 伤害渐升，避免瞬间拉满。
- 随机波形池，每次触发可从用户指定波形中抽取。
- 食用物品、恢复事件、状态恢复、区域进入、区域停留等新增触发器。
- 自定义世界区域：场景名、坐标、半径、停留时间。

### 多人模式

- 实时显示联机玩家列表。
- 展示远程玩家血量、状态、位置、距离、本地/远程标记。
- 支持多台 DG-LAB APP 同时连接。
- 可将远程玩家绑定到指定 APP 与 Slot。
- 远程玩家受伤时，可输出到该玩家绑定的设备。

## 快速启动

### 1. 安装 Python 依赖

```powershell
cd Coyote
python -m pip install -r requirements.txt
```

### 2. 启动桌面端

```powershell
cd Coyote\src\Coyote
python main.py
```

### 3. 安装 PEAK 插件

将构建出的 `Coyote.dll` 放入：

```text
PEAK\BepInEx\plugins\Coyote.dll
```

首次启动后会生成网络配置：

```text
PEAK\BepInEx\config\Coyote.Network.json
```

默认桌面端监听：

```json
{
  "pythonHost": "127.0.0.1",
  "pythonPort": 8765
}
```

### 4. 准备 DG-LAB WebSocket 服务

项目内服务目录：

```text
Coyote\dglab-websocket-server-main
```

通常需要确保 `bun.exe` 位于：

```text
Coyote\dglab-websocket-server-main\bun.exe
```

桌面端可自动或手动启动对应 WebSocket 服务。

## 多人联机工作流

### 1. 连接多个 APP

打开 Coyote 后，在多人页面扫描同一个二维码。每台手机或控制端会作为独立 APP 客户端出现。

设备标识由两部分组成：

```text
client_id + slot_id
```

这样可以避免设备离线、重连或列表顺序变化时绑定错位。

### 2. 设置本地主设备

本地玩家仍使用原有自动规则路径。建议先在多人页面选择一台在线设备作为本地主设备。

> 设备断开后，Coyote 不会自动切换到其他人的设备。原绑定会保留，等待同一 APP 与 Slot 恢复。

### 3. 绑定远程玩家

在多人玩家列表中选择一个非本地玩家，然后选择目标 DG-LAB 设备并绑定。

绑定是会话级的：

- 不写入配置文件。
- 玩家离开后自动解绑。
- 关闭远程输出总开关时会清空当前远程输出。

### 4. 开启远程伤害输出

远程输出默认关闭，需要同时满足：

| 条件 | 说明 |
| --- | --- |
| 总输出开关已开启 | 全局安全闸门 |
| 多人远程输出已开启 | 多人页面独立闸门 |
| 玩家已绑定设备 | player -> client/slot |
| 玩家输出开关已开启 | 单个玩家独立闸门 |
| 设备在线 | 绑定设备必须可用 |

远程伤害输出复用 `血量下降` 规则的强度、波形、随机强度、阶梯、持续时间、冷却和 HP 渐升参数。

## 规则系统

### 基础规则

| 类型 | 示例 |
| --- | --- |
| 生命状态 | 血量下降、死亡、昏迷 |
| 动作状态 | 体力消耗、速度高于/低于阈值、跳跃、攀爬、蹲下 |
| 物品状态 | 拿起手持物、背包装入、当前手持匹配、背包存在匹配 |
| 异常状态 | 受伤、寒冷、中毒、诅咒、石化等 |

### 扩展规则

| 规则 | 用途 |
| --- | --- |
| 食用物品 | 检测明确或推断的食用/消耗事件 |
| 血量恢复 | 连续回血达到设定幅度后触发 |
| 体力恢复 | 连续恢复体力达到设定幅度后触发 |
| 状态恢复 | 任一异常状态下降达到设定幅度后触发 |
| 进入区域 | 从区域外进入自定义球形区域时触发 |
| 区域停留 | 在区域内连续停留达到设定时间后触发 |

### 强度计算顺序

1. 读取基础强度或随机强度。
2. 应用百分比阶梯。
3. 应用瞬时变化加强。
4. 限制到规则最大强度。
5. 限制到 GUI 全局硬上限。
6. 如果启用 HP 渐升，则从 0 逐步提升到最终强度。

## 安全默认

Coyote 的默认策略偏保守：

- 自动规则默认关闭。
- 多人远程输出默认关闭。
- 玩家绑定不持久化。
- 角色死亡或昏迷时阻断自动输出。
- 场景切换、复活、角色重建时进入短暂同步保护。
- 设备断线后不自动切换到其他设备。
- 远程绑定设备恢复后会先清理旧任务。

> 建议先使用低强度、短时长和较长冷却进行测试，确认游戏遥测、规则和设备绑定都符合预期后再逐步调整。

## 源码构建

### Python 检查

```powershell
cd Coyote\src\Coyote
python -m py_compile main.py extended_features.py multiplayer_features.py
python -c "import main; print('main import ok')"
```

### C# 构建

如果 PEAK 安装在默认 Steam 路径：

```powershell
cd Coyote
dotnet build Coyote.slnx -p:DeployModFiles=false
```

如果 PEAK 安装在其他目录：

```powershell
dotnet build Coyote.slnx `
  -p:DeployModFiles=false `
  -p:PEAKGameRootDir="D:\SteamLibrary\steamapps\common\PEAK\"
```

构建成功后会生成：

```text
Coyote\artifacts\bin\Coyote\debug\Coyote.dll
```

## 常见问题

### 缺少 PySide6

现象：

```text
缺少 PySide6，请先执行：pip install PySide6
```

处理：

```powershell
python -m pip install -r Coyote\requirements.txt
```

### C# 构建找不到 Character

现象：

```text
CS0246: 未能找到类型或命名空间名 Character
```

原因通常是构建机找不到 PEAK 游戏程序集 `Assembly-CSharp.dll`。

处理：

```powershell
dotnet build Coyote.slnx -p:PEAKGameRootDir="你的 PEAK 安装目录\"
```

### 多人列表为空

检查项：

1. PEAK 是否已经进入联机场景。
2. BepInEx 插件是否已加载。
3. `Coyote.Network.json` 的端口是否与桌面端监听端口一致。
4. 桌面端日志是否显示收到 PEAK UDP。
5. 当前角色是否已经生成，加载界面和大厅可能只发送心跳。

### 设备在线但没有输出

检查项：

1. 总输出开关是否开启。
2. 对应规则是否启用。
3. 强度与持续时间是否大于 0。
4. 本地主设备或玩家绑定是否在线。
5. 冷却时间是否尚未结束。
6. 角色是否处于死亡/昏迷安全锁状态。

## 文件结构

```text
PEAK-DG-LAB-Integration
├─ README.md
├─ Coyote-使用与多人联机指南.md
└─ Coyote
   ├─ requirements.txt
   ├─ Coyote.slnx
   ├─ Directory.Build.props
   ├─ dglab-websocket-server-main
   │  ├─ v3-server.ts
   │  └─ v4-server.ts
   └─ src
      └─ Coyote
         ├─ main.py
         ├─ backend.py
         ├─ ui_qt.py
         ├─ extended_features.py
         ├─ multiplayer_features.py
         ├─ Plugin.cs
         └─ MultiplayerTelemetry.cs
```

## 建议配置流程

1. 先运行桌面端并确认无 Python 依赖错误。
2. 启动 DG-LAB WebSocket 服务并连接设备。
3. 在 PEAK 中确认 Coyote 插件发送遥测。
4. 只开启一个低强度本地规则进行测试。
5. 再启用多人页面，确认玩家列表与设备列表稳定。
6. 逐个绑定远程玩家，逐个开启玩家输出开关。
7. 保存规则配置，开始正式使用。

---

本指南关注“如何把项目跑起来并安全使用”。更详细的规则字段、波形协议和源码实现可继续查看 `Coyote/src/Coyote` 下的 Python 与 C# 文件。
