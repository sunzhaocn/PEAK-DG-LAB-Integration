# Coyote — PEAK × DG-LAB Integration

> PEAK 游戏遥测、规则系统与 DG-LAB 控制的非官方社区集成项目。

Coyote 由 **PEAK/BepInEx 遥测插件**和**Windows 桌面控制端**组成，可把游戏状态转换为经过配置和安全限制的 DG-LAB A/B 通道输出，并支持多人设备绑定、规则扩展、直连以及公网 WSS 中继。

> [!IMPORTANT]
> 本项目与 PEAK、DG-LAB、BepInEx 官方均无隶属或背书关系。文档中的“项目预设中继”（内部历史标识 `official_relay`）指 **Coyote 项目预配置的 Relay 地址**，不是 DG-LAB 官方运营服务。许可证、上游来源和第三方边界见 [NOTICE.md](NOTICE.md)。

## 项目结构

从**仓库根目录**看，主要文件位于：

| 模块 | 实际路径 | 作用 |
| --- | --- | --- |
| PEAK 插件 | `Coyote/src/Coyote/Plugin.cs` | 读取本地玩家状态并发送遥测 |
| 多人遥测 | `Coyote/src/Coyote/MultiplayerTelemetry.cs` | 读取联机玩家、血量、状态、位置与距离 |
| 桌面入口 | `Coyote/src/Coyote/main.py` | 按固定顺序安装后端/UI 扩展并启动程序 |
| 桌面后端 | `Coyote/src/Coyote/backend.py` | 规则、配置、日志、DG-LAB 操作与安全限制 |
| 扩展规则 | `Coyote/src/Coyote/extended_features.py` | 恢复、区域、随机波形、HP 渐升等 |
| 多人系统 | `Coyote/src/Coyote/multiplayer_features.py` | 多 APP/设备、玩家绑定、远程输出 |
| 网络扩展 | `Coyote/src/Coyote/network_features.py` | 直连、项目预设 WSS、自定义 WSS |
| Relay 诊断 | `Coyote/src/Coyote/remote_reporting.py` | 可选的状态/日志上报与隐私控制 |
| 桌面 UI | `Coyote/src/Coyote/ui_qt.py` | 参数配置、状态展示、日志和设备管理 |
| 自动更新 | `Coyote/src/Coyote/update_checker.py` | GitHub Release 检查与便携版更新 |
| 自定义规则 | `Coyote/custom_rules/` | 用户自定义 Python 规则 |
| 本地 V4 Server | `Coyote/dglab-websocket-server-main/` | 上游派生/兼容的 DG-LAB WebSocket 服务 |

更详细的代码边界和扩展安装顺序见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 数据链路

```text
PEAK
  │
  │ BepInEx / Coyote.dll
  │ 本地遥测
  ▼
Coyote Desktop
  ├─ 状态归一化
  ├─ 规则与冷却
  ├─ 安全总开关 / 强度限制
  ├─ 玩家 → 设备绑定
  └─ A/B 波形与强度控制
  │
  ├───────────────┐
  ▼               ▼
本地直连       WSS Relay
  │               │
  └───────┬───────┘
          ▼
      DG-LAB App
          │
          ▼
     DG-LAB Device
```

## 主要功能

### PEAK 遥测

可处理的状态包括血量、体力、死亡、昏迷、跳跃、攀爬、蹲下、移动速度、物品、背包、场景、位置以及多种异常状态。多人扩展还可读取联机玩家、远程玩家血量/状态/位置和距离。

### 规则系统

规则可配置：

- A/B 通道强度；
- A/B 持续时间；
- 通道波形；
- 最大强度；
- 冷却时间；
- 随机强度；
- 百分比阶梯；
- 瞬时变化增强；
- 持续输出；
- 区域、恢复、物品和状态类扩展条件。

持续模式由桌面端以有限片段续播，不把“无限任务”直接交给设备，以便在关闭输出、断连、角色状态变化或程序退出时停止。

### 多人设备映射

同一个 Controller 可管理多个 DG-LAB App/设备。设备身份按 `client_id + slot_id` 区分；远程 PEAK 玩家可在当前会话中绑定指定设备。

远程自动输出必须同时满足总输出开关、多人远程输出开关、玩家输出开关、有效绑定以及目标设备在线等条件。

### 波形

项目包含气泡、挤压、攀登、树荫、律动、电波、舞步、呼吸、脉冲等预设，并支持自定义波形和 A/B 独立选择。

## 网络模式

### 1. 直连

默认模式。Coyote Controller 连接本机 Bun WebSocket Server，DG-LAB App 使用可到达该电脑的局域网、VPN、IPv4、IPv6 或手动域名/IP 地址连接。

```text
Coyote -> 127.0.0.1:Relay
Phone  -> ws://可到达的电脑地址:Relay/?tid=...
```

### 2. 项目预设 WSS 中继

当电脑和手机不能直接互访时，可显式选择 Coyote 项目预配置的公网 WSS Relay。

内部配置/历史代码标识为 `official_relay`，这里的 “official” **仅表示 Coyote 项目内置预设**，不表示 DG-LAB 官方服务。

### 3. 自定义 WSS 中继

可使用独立部署的 [`PEAK_Coyote_Relay`](https://github.com/sunzhaocn/PEAK_Coyote_Relay) 或其他兼容服务。公网自定义中继必须使用 `wss://`。

网络模式不会在直连失败后静默切换到公网中继；公网 Relay 由用户主动选择。

## 普通用户安装

推荐从 GitHub Releases 下载 Windows 便携包，例如：

```text
Coyote_Windows_x64_Portable.zip
```

典型流程：

1. 解压便携包并启动 `Coyote.exe`；
2. 让程序检测 PEAK 安装路径；
3. 检查/安装/修复 BepInEx；
4. 安装或更新 `Coyote.dll`；
5. 启动 PEAK；
6. 确认游戏遥测已连接；
7. 连接 DG-LAB App；
8. 先用低强度手动测试；
9. 再开启总输出和所需自动规则。

首次使用不要直接启用高强度、长持续时间或多人远程自动输出。

## 源码运行与构建

Python 依赖位于：

```text
Coyote/requirements.txt
```

源码入口：

```text
Coyote/src/Coyote/main.py
```

Windows 便携版构建入口：

```text
Coyote/build_exe_selfcontained.bat
Coyote/build_exe_selfcontained.ps1
```

构建脚本会编译当前 `Coyote.csproj`、运行 Python 语法检查、执行 PyInstaller，并复制运行资源和当前生成的 `Coyote.dll`。

Thunderstore 打包在仓库源码中默认关闭；维护者需要发布时应在本机 `Coyote/Config.Build.user.props` 配置维护者/团队信息，并显式启用打包。该本机配置已被 `.gitignore` 排除。

## 版本号

仓库中存在多个独立版本域，**不要因为数字不同就直接同步覆盖**：

- 桌面程序 / GitHub Release：`Coyote/src/Coyote/app_version.py`
- BepInEx 插件：`Coyote/src/Coyote/Coyote.csproj`
- 网络/多人模块中的 `V2.6.x`：模块实现修订标识
- `PEAK_Coyote_Relay`：独立仓库、独立版本生命周期

详见 [docs/VERSIONING.md](docs/VERSIONING.md)。

## 自定义规则

用户规则放在：

```text
Coyote/custom_rules/
```

规则通过后端提供的受限接口读取状态并描述输出。加载器会进行 AST 校验并限制内置能力，但这属于**应用级约束，不是操作系统沙箱**。只加载可信的规则文件。

开发规则前请参考 `Coyote/src/Coyote/md/自定义规则开发指南.md`。

## 安全设计

Coyote 的默认设计包括：

- 自动规则默认关闭；
- 多人远程输出默认关闭；
- 独立总输出开关；
- 全局强度上限；
- 设备断线不自动切换到其他设备；
- 玩家绑定默认是会话级；
- 死亡/昏迷、场景切换等状态下进行输出保护；
- 持续模式使用有限片段续播；
- 程序退出时清理任务；
- 公网/自定义 Relay 要求 `wss://`。

软件保护不能替代设备本身的安全限制或物理断开方式。安全与漏洞报告说明见 [SECURITY.md](SECURITY.md)。

## 上游和许可证

本项目以 **GPL-3.0** 发布。

`Coyote/dglab-websocket-server-main/` 是独立的上游派生/兼容源码子树，保留其 GPL 许可证。当前仓库不宣称该目录与 DG-LAB 上游仓库 `main` 分支逐字同步；更新该目录时必须保留许可证并记录选择的上游版本/修订。

详见 [NOTICE.md](NOTICE.md)。

## 开发检查

仓库 CI 会执行：

- Python 源码编译检查；
- 语言/Relay 配置 JSON 解析；
- MSBuild XML 元数据解析；
- 模板 TODO 残留检查；
- 根目录与 `Coyote/` GPL 许可证一致性检查；
- 上游 DG-LAB 子树许可证存在性检查。

贡献前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。
