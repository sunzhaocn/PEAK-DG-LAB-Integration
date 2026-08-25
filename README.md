# Coyote — PEAK × DG-LAB Integration

> 面向 PEAK × DG-LAB 场景的桌面控制端、BepInEx 插件、规则系统与多人联动工具。

Coyote 将 **PEAK 游戏实时遥测、可视化规则、多人设备映射、DG-LAB WebSocket 控制以及公网中继能力**整合到同一套工作流中。

> 本项目为第三方社区项目，与 PEAK、DG-LAB、BepInEx 官方无隶属关系。

---

## 1. 项目组成

Coyote 主要由以下模块组成：

| 模块 | 主要文件 | 作用 |
| --- | --- | --- |
| PEAK 插件 | `src/Coyote/Plugin.cs` | 读取本地玩家状态并发送遥测 |
| 多人遥测 | `src/Coyote/MultiplayerTelemetry.cs` | 读取联机玩家、血量、状态、位置与距离 |
| 桌面后端 | `src/Coyote/backend.py` | 规则判断、配置、日志、DG-LAB 控制 |
| 扩展规则 | `src/Coyote/extended_features.py` | 恢复、区域、随机波形、HP 渐升等 |
| 多人系统 | `src/Coyote/multiplayer_features.py` | 多 APP、多设备、玩家绑定、远程输出 |
| 网络扩展 | `src/Coyote/network_features.py` | 直连、官方 WSS、自定义 WSS |
| 桌面界面 | `src/Coyote/ui_qt.py` | 参数配置、状态展示、日志与设备控制 |
| 自定义规则 | `custom_rules/` | 用户自定义 Python 游戏规则 |
| DG-LAB Server | `dglab-websocket-server-main/` | 本地 DG-LAB WebSocket 服务 |

整体链路：

```text
PEAK
  │
  │ BepInEx / Coyote.dll
  ▼
Coyote Desktop
  │
  ├─ 游戏状态
  ├─ 规则引擎
  ├─ 多人玩家管理
  ├─ 玩家 → 设备绑定
  ├─ 自定义规则
  └─ A/B 通道控制
  │
  ├───────────────┐
  ▼               ▼
本地直连       WSS 公网中继
  │               │
  └───────┬───────┘
          ▼
      DG-LAB App
          │
          ▼
     DG-LAB Device
```

---

# 2. 功能介绍

## 2.1 PEAK 游戏遥测

Coyote 可读取并处理包括但不限于：

- 血量
- 体力
- 死亡
- 昏迷
- 跳跃
- 攀爬
- 蹲下
- 移动速度
- 手持物
- 背包内容
- 场景
- 世界坐标
- 多种异常状态
- 联机玩家信息

多人模式还可以读取：

- 联机玩家列表
- 远程玩家血量
- 玩家状态
- 玩家位置
- 与本地玩家距离
- 本地/远程玩家标记

---

## 2.2 基础规则系统

内置规则包括：

| 类别 | 示例 |
| --- | --- |
| 生命状态 | 血量下降、死亡、昏迷 |
| 体力 | 体力消耗 |
| 动作 | 跳跃、开始攀爬、蹲下 |
| 速度 | 高于阈值、低于阈值 |
| 物品 | 手持变化、背包变化、物品匹配 |
| 异常状态 | 受伤、寒冷、中毒、诅咒、石化等 |

规则可以独立配置：

- A 通道强度
- B 通道强度
- A/B 通道持续时间
- A/B 通道波形
- 最大强度
- 冷却时间
- 随机强度
- 百分比阶梯
- 瞬时大变化增强

---

## 2.3 扩展规则

额外支持：

- 食用物品
- 血量恢复
- 体力恢复
- 异常状态恢复
- 进入指定区域
- 在指定区域停留
- HP 伤害渐升
- 随机波形池
- 持续输出

区域规则可以根据：

```text
场景名
+
世界坐标
+
半径
+
停留时间
```

组合成自定义触发区域。

---

## 2.4 DG-LAB A/B 双通道

支持：

- A 通道独立控制
- B 通道独立控制
- A+B 联动
- 不同通道使用不同波形
- 不同强度
- 不同持续时间
- 临时播放
- 持续播放
- 一键停止

持续输出并不会向设备发送不可控的无限任务，而是由 Coyote 通过有限片段续播，以便在关闭总开关、断连、死亡/昏迷或程序退出时及时停止。

---

## 2.5 波形系统

项目内置多种波形，例如：

- 气泡
- 挤压
- 攀登
- 树荫
- 律动
- 电波
- 舞步
- 呼吸
- 脉冲

同时支持：

- 用户自定义波形
- A/B 通道独立波形
- 规则独立波形
- 阶梯独立波形
- 随机波形池

---

# 3. 多人联机

## 3.1 多 APP / 多设备

同一个 Coyote Controller 可以连接多个 DG-LAB App。

设备身份由：

```text
client_id + slot_id
```

共同确定，避免因为设备断线、重连或列表顺序变化造成绑定错位。

示例：

```text
Controller
├─ App A
│  └─ Slot 1
├─ App B
│  └─ Slot 1
└─ App C
   └─ Slot 2
```

---

## 3.2 玩家 → DG-LAB 设备绑定

多人模式允许把远程 PEAK 玩家绑定到指定 DG-LAB 设备。

```text
Player A
   ↓
App A / Slot 1

Player B
   ↓
App B / Slot 1
```

远程玩家受伤时，可以按照该玩家绑定关系，将对应规则输出到指定设备。

远程输出必须同时满足：

1. 总输出开关已开启；
2. 多人远程输出已开启；
3. 玩家已绑定设备；
4. 单个玩家输出已开启；
5. 目标设备在线。

远程绑定默认是**会话级绑定**：

- 不永久写入配置；
- 玩家离开后自动解绑；
- 设备断线不会自动切换到其他人的设备。

---

# 4. 网络模式

Coyote 提供三种主要连接模式。

## 4.1 直连

默认模式。

```text
Coyote
  ↓
本地 Bun WebSocket Server
  ↓
局域网 / IPv4 / IPv6 / VPN
  ↓
DG-LAB App
```

可用于：

- 同一局域网
- Tailscale
- ZeroTier
- WireGuard
- VPN
- 可直接路由的 IPv4
- IPv6
- 手动指定域名或 IP

---

## 4.2 官方 WSS 中继

当电脑与手机无法直接互访时，可以选择官方 WSS 中继。

```text
Coyote PC
   │
   │ WSS / TLS
   ▼
Coyote Relay
   ▲
   │ WSS / TLS
   │
DG-LAB App
```

公网中继使用加密的 `wss://`。

---

## 4.3 自定义 WSS 中继

也可以部署自己的 `PEAK_Coyote_Relay`。

在 Coyote 中选择：

```text
自定义中继
```

填写：

```text
wss://your-domain.example
```

公网自定义 Relay 必须使用 `wss://`。

---

# 5. 安全设计

Coyote 默认采用较保守的安全策略：

- 自动规则默认关闭；
- 多人远程输出默认关闭；
- 总输出有独立安全总开关；
- 玩家绑定默认不持久化；
- 设备断开后不自动切换到其他设备；
- 角色死亡或昏迷时阻断后续输出；
- 场景切换、复活和角色重建期间进行同步保护；
- 持续模式使用有限片段续播；
- 程序退出时清理设备任务；
- 自定义规则不能直接绕过后端操作 DG-LAB；
- 强度经过全局硬上限限制。

建议第一次使用时先从低强度、短持续时间和较长冷却开始测试。

---

# 6. 普通玩家安装

## 6.1 推荐方式：使用 Release 便携版

普通用户**不需要安装 Python，也不需要自行编译 C#**。

从 GitHub Releases 下载：

```text
Coyote_Windows_x64_Portable.zip
```

然后：

```text
1. 解压 ZIP
2. 双击 Coyote.exe
3. 让软件自动检测 PEAK
4. 检查 / 安装 / 修复 BepInEx
5. 安装或更新 Coyote.dll
6. 启动 PEAK
7. 打开 Coyote
8. 连接 DG-LAB App
9. 先使用手动控制测试
10. 再开启需要的自动规则
```

Portable 包会包含运行所需的桌面程序、插件和 WebSocket Server 相关资源。

---

# 7. BepInEx 与插件管理

Coyote 内置 PEAK / BepInEx 管理功能，可用于：

- 自动检测 PEAK 安装目录；
- 检测 BepInEx；
- 安装或修复 `BepInExPack_PEAK`；
- 从本地 ZIP 安装 BepInEx；
- 打开 `BepInEx/plugins`；
- 安装 / 更新 `Coyote.dll`；
- 覆盖旧插件前自动备份。

PEAK 路径检测可以使用：

- 已保存路径
- 正在运行的 `PEAK.exe`
- Steam 注册表
- `libraryfolders.vdf`
- `appmanifest_3527290.acf`
- 常见 Steam Library 路径

因此普通用户一般不需要手动查找游戏插件目录。

---

# 8. 推荐首次使用流程

```text
启动 Coyote
   ↓
确认 PEAK 路径
   ↓
安装 / 修复 BepInEx
   ↓
安装 / 更新 Coyote.dll
   ↓
启动 PEAK
   ↓
确认游戏遥测已连接
   ↓
连接 DG-LAB App
   ↓
确认设备在线
   ↓
低强度手动测试
   ↓
开启总输出
   ↓
逐个启用自动规则
```

---

# 9. 多人模式使用

## 9.1 连接多个 App

打开 Coyote 的多人页面，让需要参与的 DG-LAB App 扫描当前二维码。

每个 App 会作为独立客户端出现。

---

## 9.2 检查设备

确认：

```text
App
Slot
设备名称
在线状态
```

均正常。

---

## 9.3 设置本地主设备

为本地 PEAK 玩家选择主要使用的设备。

---

## 9.4 绑定远程玩家

选择远程玩家，再选择目标：

```text
App / Slot
```

执行绑定。

---

## 9.5 开启远程输出

确认：

```text
允许电击输出      ON
多人远程输出      ON
玩家输出          ON
玩家已绑定设备
设备在线
```

之后，远程玩家的受伤事件即可按照其绑定设备发送。

---

# 10. 自定义 Python 规则

自定义规则目录：

```text
Coyote/
└─ custom_rules/
```

示例：

```python
NAME = "高速攀爬"
DESCRIPTION = "攀爬且速度超过 3 时触发"
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
```

常用读取接口包括：

```text
get()
prev()
status()
held()
backpack()
pocket()
changed()
increased()
decreased()
```

修改脚本后，可以在软件中重新加载，不需要重新启动 PEAK。

自定义规则运行在受限环境中，不能直接访问任意 Python API，也不能绕过 Coyote 后端直接发送设备指令。

---

# 11. 开发环境

以下内容仅面向源码开发者。

推荐环境：

```text
Windows 10 / 11 x64
Git
Python 3.12+
.NET SDK
Bun
PEAK
```

项目 `global.json` 当前要求：

```json
{
  "sdk": {
    "rollForward": "latestMajor",
    "version": "10.0.100"
  }
}
```

Python 主要依赖：

```text
PySide6
Pillow
qrcode
websocket-client
```

---

# 12. 获取源码

```powershell
git clone https://github.com/sunzhaocn/PEAK-DG-LAB-Integration.git
cd PEAK-DG-LAB-Integration\Coyote
```

---

# 13. 安装 Python 依赖

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

---

# 14. 从源码启动桌面端

```powershell
cd src\Coyote
python main.py
```

---

# 15. 编译 PEAK 插件

插件项目：

```text
src/Coyote/Coyote.csproj
```

目标框架：

```text
netstandard2.1
```

生成程序集：

```text
Coyote.dll
```

普通编译：

```powershell
dotnet restore Coyote.slnx
dotnet build Coyote.slnx -p:DeployModFiles=false
```

---

# 16. 指定 PEAK 安装目录

如果 PEAK 不在默认 Steam 路径，例如：

```text
D:\steam\steamapps\common\PEAK
```

可以执行：

```powershell
dotnet build src\Coyote\Coyote.csproj `
  -c Release `
  -p:PEAKGameRootDir="D:\steam\steamapps\common\PEAK" `
  -p:DeployModFiles=false `
  -p:RunThunderPipePackAfterBuild=false
```

构建系统会从：

```text
PEAK_Data\Managed\
```

读取需要的游戏程序集。

---

# 17. 编译并自动部署插件

已经正确安装 BepInEx 时，可以：

```powershell
dotnet build src\Coyote\Coyote.csproj `
  -c Debug `
  -p:PEAKGameRootDir="D:\steam\steamapps\common\PEAK" `
  -p:PEAKBepInExDir="D:\steam\steamapps\common\PEAK\BepInEx" `
  -p:DeployModFiles=true
```

构建后会自动把 `Coyote.dll` 部署到：

```text
PEAK\BepInEx\plugins\
```

---

# 18. 手动安装插件

如果不使用自动部署，将生成的：

```text
Coyote.dll
```

复制到：

```text
PEAK\
└─ BepInEx\
   └─ plugins\
      └─ Coyote.dll
```

然后重新启动 PEAK。

---

# 19. 编译完整 Windows Portable 版本

项目提供：

```text
build_exe_selfcontained.bat
build_exe_selfcontained.ps1
```

推荐：

```powershell
.\build_exe_selfcontained.bat
```

或：

```powershell
powershell -ExecutionPolicy Bypass `
  -File .\build_exe_selfcontained.ps1
```

构建脚本会自动完成：

```text
检查 Python
   ↓
检查项目结构
   ↓
检查 / 准备 bun.exe
   ↓
编译 Coyote.dll
   ↓
安装打包依赖
   ↓
Python 语法检查
   ↓
运行可用的回归测试
   ↓
PyInstaller 打包
   ↓
生成 Coyote.exe
   ↓
复制 Server / assets / custom_rules / language / docs
   ↓
复制 Coyote.dll
   ↓
创建 Portable ZIP
```

最终输出：

```text
dist/
└─ Coyote/
   ├─ Coyote.exe
   ├─ plugin/
   │  └─ Coyote.dll
   ├─ dglab-websocket-server-main/
   ├─ custom_rules/
   ├─ assets/
   ├─ docs/
   └─ ...

release/
└─ Coyote_Windows_x64_Portable.zip
```

`Coyote_Windows_x64_Portable.zip` 即推荐发布到 GitHub Releases 的用户版本。

---

# 20. 目录结构

典型目录：

```text
Coyote/
├─ Coyote.slnx
├─ global.json
├─ requirements.txt
├─ build_exe_selfcontained.bat
├─ build_exe_selfcontained.ps1
├─ custom_rules/
├─ dglab-websocket-server-main/
├─ assets/
├─ docs/
└─ src/
   └─ Coyote/
      ├─ Plugin.cs
      ├─ MultiplayerTelemetry.cs
      ├─ main.py
      ├─ backend.py
      ├─ ui_qt.py
      ├─ extended_features.py
      ├─ multiplayer_features.py
      ├─ network_features.py
      ├─ remote_reporting.py
      ├─ update_checker.py
      └─ language/
```

---

# 21. 故障排查

## PEAK 未连接

检查：

1. PEAK 是否已经安装 BepInEx；
2. `Coyote.dll` 是否在 `BepInEx/plugins/`；
3. PEAK 是否已经启动并进入可读取玩家状态的场景；
4. 本地 UDP 端口是否被占用；
5. 防火墙是否拦截相关进程。

---

## DG-LAB App 无法连接

直连模式下检查：

- 手机和电脑是否可以互访；
- Windows 防火墙；
- 当前 IP 是否正确；
- WebSocket Server 是否已运行；
- 指定端口是否被占用。

公网模式下检查：

- Relay 地址是否使用 `wss://`；
- 域名 DNS 是否正确；
- Relay 是否健康；
- TLS 证书是否有效。

---

## 插件更新后没有生效

确认：

```text
PEAK\BepInEx\plugins\Coyote.dll
```

确实已经被新版本覆盖，然后完全退出并重新启动 PEAK。

---

# 22. 使用建议

- 第一次测试从较低强度开始；
- 优先测试手动输出，再启用自动规则；
- 不建议一次开启所有规则；
- 多人绑定后先确认目标设备再开启远程输出；
- 使用公网 Relay 时优先使用 WSS；
- 不熟悉自定义规则时先基于示例文件修改。

---

# 23. 相关项目

公网 WSS Relay：

```text
https://github.com/sunzhaocn/PEAK_Coyote_Relay
```

---

## License

请以仓库当前 `LICENSE` 文件为准。
