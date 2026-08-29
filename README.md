# Coyote — PEAK × DG-LAB Integration

> PEAK 游戏遥测、图形化规则与 DG-LAB 控制的非官方社区集成项目。

Coyote 由 **PEAK/BepInEx 遥测插件**和 **Windows 桌面控制端**组成。插件读取本地/多人游戏状态，桌面端负责规则判断、设备路由、A/B 通道输出、网络模式和图形化自定义规则。

> [!IMPORTANT]
> 本项目与 PEAK、DG-LAB、BepInEx 官方均无隶属或背书关系。代码中的 `official_relay` 只是 Coyote 项目预配置的 Relay 标识，不代表 DG-LAB 官方运营服务。第三方边界见 [NOTICE.md](NOTICE.md)。

## 代码结构

重构后的源码按职责分层：

```text
Coyote/src/Coyote/
├─ main.py                         # 极薄启动入口
├─ backend.py                      # 稳定核心：状态/规则/设备/配置/路径
├─ i18n.py                         # 稳定语言资源定位
├─ update_checker.py               # 稳定更新器/安装目录定位
├─ app_version.py                  # 桌面版本号
├─ relay_config.py                 # 项目预设 Relay 身份
├─ Plugin/
│  └─ CoyotePlugin.cs              # PEAK/BepInEx 主插件
├─ Telemetry/
│  └─ MultiplayerTelemetry.cs      # 多人遥测
├─ coyote_app/
│  ├─ bootstrap.py                 # 唯一应用组合根/安装顺序
│  ├─ features/
│  │  ├─ extended.py               # 恢复/区域/随机波形/渐升
│  │  ├─ multiplayer.py            # 多人/多设备路由
│  │  ├─ network.py                # 直连/WSS 网络模式
│  │  └─ reporting.py              # 可选加密诊断上报
│  ├─ ui/
│  │  └─ qt.py                     # PySide6 桌面 UI
│  └─ visual_rules/
│     ├─ engine.py                 # 节点图存储/执行/编辑器
│     ├─ integration.py            # 现有检测器与输出能力适配
│     └─ policy.py                 # 官方规则/自定义规则分域策略
├─ language/                       # 运行时语言资源
└─ md/                             # 随便携包发布的用户文档
```

历史模块名 `ui_qt.py`、`extended_features.py`、`visual_rules.py` 等仍保留，但现在只是**兼容别名**；实现只维护在 `coyote_app/` 中。`backend.py`、`i18n.py`、`update_checker.py` 等依赖自身文件位置计算资源/安装路径，因此刻意保留稳定位置，避免重构破坏现有便携版和用户配置。

完整边界与依赖方向见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 数据链路

```text
PEAK
  │  Coyote.dll / BepInEx
  ▼
Coyote Desktop
  ├─ 遥测归一化
  ├─ 官方规则域
  ├─ 图形化自定义规则域
  ├─ 多人玩家 → 设备绑定
  ├─ 总输出/强度硬上限/停止控制
  └─ A/B 强度、持续时间与波形
  │
  ├───────────────┐
  ▼               ▼
本机直连       WSS Relay
  │               │
  └───────┬───────┘
          ▼
      DG-LAB App
          │
          ▼
     DG-LAB Device
```

## 图形化自定义规则

用户不再编写或加载 Python 自定义规则。自定义规则由软件中的 **模块 + 端口 + 连线** 编辑器创建，持久化为：

```text
Coyote/visual_rules.json
```

节点库包含当前软件已注册的规则事件、遥测字段、比较/逻辑、边沿、冷却、强度、持续时间、波形、档位、随机波形、输出以及自定义死亡/昏迷保护模块。

`禁用软件内置规则` 是整个官方自动规则域的接管开关。它生效后，官方普通规则、官方死亡/昏迷输出以及官方死亡/昏迷规则级保护都不再参与；自定义规则需要什么死亡/昏迷保护，由自定义节点图自己显式连接对应保护模块。

详细使用方法见 `Coyote/src/Coyote/md/图形化规则使用说明.md`。

## 官方规则与自定义规则

两套规则系统逻辑上独立：

- **官方规则域**：官方规则页面中的规则与其官方保护逻辑；
- **自定义规则域**：`visual_rules.json` 中的图形规则；
- 两者共享 DG-LAB 连接、设备 slot、总输出开关、强度硬上限、主动停止/断开等底层设施；
- 自定义死亡/昏迷图不会自动覆盖官方死亡/昏迷规则；
- 禁用官方规则后，不会残留官方死亡/昏迷保护替自定义规则做决定。

## 多人与设备路由

同一个 Controller 可以识别多个 DG-LAB App/Slot。设备身份按 `client_id + slot_id` 区分，远程 PEAK 玩家可在当前会话中绑定指定设备。远程自动输出有独立开关，且绑定默认不永久保存。

## 网络模式

桌面端提供三种明确模式：

1. **直连**：Coyote Controller 使用本机 Bun WebSocket Server，手机通过局域网/VPN/IPv4/IPv6/手动地址访问电脑；
2. **项目预设 WSS Relay**：用户主动选择 Coyote 项目预配置的加密中继；
3. **自定义 WSS Relay**：使用独立部署的兼容 Relay。

直连失败不会静默切换到公网 Relay。公网/自定义 Relay 必须使用 `wss://`。

## 普通用户安装

推荐直接从 GitHub Releases 下载：

```text
Coyote_Windows_x64_Portable.zip
```

典型流程：解压 → 启动 `Coyote.exe` → 检测/安装 PEAK BepInEx → 安装 `Coyote.dll` → 启动 PEAK → 连接 DG-LAB App → 先低强度测试 → 再开启所需规则。

## 源码运行与构建

Python 依赖：`Coyote/requirements.txt`

源码入口：

```text
Coyote/src/Coyote/main.py
```

Windows 便携构建：

```text
Coyote/build_exe_selfcontained.bat
Coyote/build_exe_selfcontained.ps1
```

构建脚本会：

1. 校验仓库结构；
2. 编译当前 `Coyote.csproj`；
3. 递归编译 Python 源码；
4. 运行测试（若存在）；
5. 构建 PyInstaller 桌面程序；
6. 复制语言、文档、Relay Server、项目预设 Relay 配置和最新 `Coyote.dll`；
7. 生成 Windows x64 Portable ZIP。

## 版本域

以下版本彼此独立，不要因为数字不同就机械同步：

- 桌面程序 / GitHub Release：`Coyote/src/Coyote/app_version.py`
- BepInEx 插件：`Coyote/src/Coyote/Coyote.csproj`
- 网络/多人模块中的实现修订号
- 独立 Relay 项目版本

详见 [docs/VERSIONING.md](docs/VERSIONING.md)。

## 开发检查

CI 会检查：

- 新目录结构和兼容层；
- 不允许重新出现 Python 自定义规则文件；
- 全部 Python 源码语法；
- JSON/XML 元数据；
- 发布模板残留；
- GPL 许可证一致性；
- vendored DG-LAB Server 许可证保留。

本地可先运行：

```bash
python Coyote/tools/validate_structure.py
```

贡献说明见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可证

项目以 **GPL-3.0** 发布。`Coyote/dglab-websocket-server-main/` 是独立的上游派生/兼容源码子树，保留其许可证和第三方边界。
