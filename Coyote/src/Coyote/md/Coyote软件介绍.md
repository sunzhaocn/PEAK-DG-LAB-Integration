# Coyote 软件介绍

## PEAK × DG-LAB 智能联动工具

Coyote 是一个连接 **PEAK 游戏遥测系统与 DG-LAB 设备** 的扩展工具。

通过读取游戏中的角色状态、事件变化和多人联机信息，Coyote 可以将游戏体验实时转换为 DG-LAB 设备反馈，实现更加沉浸式的互动体验。

---

# 功能特点

## 1. 实时游戏状态同步

Coyote 通过 BepInEx 插件获取 PEAK 游戏数据，包括：

- 玩家生命状态
- 受伤事件
- 体力变化
- 攀爬状态
- 死亡与昏迷状态
- 物品交互事件
- 玩家位置数据

并实时发送至 Coyote 后端进行处理。

---

# 2. DG-LAB 智能控制

Coyote 支持：

- DG-LAB APP 连接
- 多设备管理
- 自定义强度
- 多种波形控制
- 持续输出
- 渐变输出

用户可以根据游戏事件自由设计反馈方式。

---

# 3. 多人联机支持

Coyote 支持 PEAK 多人游戏环境。

多人模式下可以：

- 获取队友状态
- 查看玩家信息
- 分配不同 DG-LAB 设备
- 设置独立玩家反馈

所有多人绑定均为当前会话管理，不会永久保存用户设备信息。

---

# 4. 扩展规则系统

Coyote 提供 Python 扩展机制。

用户可以创建自己的规则，例如：

- 受伤触发
- 跳跃触发
- 攀爬触发
- 物品使用触发
- 区域进入触发
- 自定义事件触发

无需修改核心程序即可扩展功能。

---

# 5. 软件结构

主要组件：

```text
Coyote
│
├─ Coyote.dll
│  └─ PEAK 游戏数据采集插件
│
├─ backend.py
│  └─ 核心逻辑处理
│
├─ ui_qt.py
│  └─ 用户界面
│
├─ extended_features.py
│  └─ 扩展规则支持
│
├─ multiplayer_features.py
│  └─ 多人联机支持
│
└─ dglab-websocket-server
   └─ DG-LAB 通信服务
```

---

# 安装流程

## 1. 安装 BepInEx

将 BepInEx 安装到 PEAK 游戏目录。

---

## 2. 安装 Coyote.dll

使用 Coyote 软件：

```
安装 / 更新 Coyote.dll
```

插件会自动复制到：

```text
PEAK/BepInEx/plugins/
```

---

## 3. 启动 Coyote

启动软件后：

1. 连接 DG-LAB
2. 启动 PEAK
3. 根据需求开启规则

---

# 配置说明

Coyote 支持：

- 规则开关
- 强度设置
- 波形选择
- 多人绑定
- 输出保护

所有配置均可通过软件界面调整。

---

# 开源与扩展

Coyote 采用模块化设计。

开发者可以：

- 添加新的游戏事件
- 编写 Python 规则
- 扩展多人功能
- 改进 DG-LAB 通信逻辑

欢迎社区开发者参与改进。

---

# 注意事项

使用 Coyote 时：

- 请确保 DG-LAB 设备正常连接
- 请合理设置输出参数
- 请勿在不适合的情况下使用设备反馈功能
- 第三方扩展规则需自行确认安全性

---

感谢使用 Coyote。

Enjoy PEAK × DG-LAB Interactive Experience.