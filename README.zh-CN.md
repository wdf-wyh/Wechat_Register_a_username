# wechat-farm（微信养号自动化）

> 在真实 Android 手机上，用 **uiautomator2 + EasyOCR** 模拟真人使用微信，养出可用于手机性能 **benchmark** 的深度账号。不用模拟器、不用 Hook。

[![CI](https://github.com/wdf-wyh/Wechat_Register_a_username/actions/workflows/ci.yml/badge.svg)](https://github.com/wdf-wyh/Wechat_Register_a_username/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Platform](https://img.shields.io/badge/Android-11%2B%20%E7%9C%9F%E6%9C%BA-green.svg)](#系统要求)

**English:** [README.md](README.md)

> **警告**：与腾讯/微信无关。自动化可能违反微信用户协议并导致限制/封号。仅限自有真机与授权实验室用途 — 详见 [DISCLAIMER.md](DISCLAIMER.md)。

---

## 为什么做这个

手机厂商做微信场景性能 benchmark，需要 **L1–L4 深度使用号**。模拟器/云手机很快被识别。本项目通过 USB/ADB 驱动**物理真机**；因微信常屏蔽无障碍控件树（`FLAG_SECURE`），定位以坐标 + OpenCV + EasyOCR 为主。

## 演示

![架构](docs/assets/architecture.png)

![日循环](docs/assets/demo-flow.png)

## 功能

| 类别 | 能力 |
|------|------|
| 朋友圈 | 浏览、发图文、点赞、评论 |
| 聊天 | 文字/图片、多轮深聊 |
| 视频号 | 完播浏览、点赞、评论 |
| 公众号 | 关注、读文、留言 |
| 社交扩展 | 加好友、群聊、小程序、官方小游戏 |
| 系统 | 四阶段剧本、AI 上帝视角编排、健康检查、SQLite 日志、L1–L4 评分 |

**设计红线：** 仅真机 · 一机一号一卡 · 禁用多开/Xposed/内存注入 · 养的号互不加好友。

## 系统要求

- PC：Windows / macOS / Linux + ADB
- 手机：Android 11+ **真机**，每台 1 个微信号
- SIM：每台独立 4G 卡
- Python 3.10+

## 快速上手

```bash
git clone https://github.com/wdf-wyh/Wechat_Register_a_username.git
cd Wechat_Register_a_username
python -m venv wechat_env

# Windows
wechat_env\Scripts\activate
# macOS / Linux
# source wechat_env/bin/activate

pip install -r requirements.txt
python -m uiautomator2 init
python main.py init
adb devices
python main.py fast-debug          # 约 10–20 分钟单机冒烟
```

无真机单测：

```bash
pip install pytest
pytest scripts/ -q -k "unit"
```

服务器上已有其他设备时，不要随意 `init`，步骤见 [docs/使用手册.md](docs/使用手册.md)。

## 常用命令

| 命令 | 说明 |
|------|------|
| `python main.py init` | 初始化数据库，检测设备 |
| `python main.py run` | 全部账号执行当日剧本 |
| `python main.py debug` | 单设备跑当日剧本 |
| `python main.py fast-debug` | 一键核心功能冒烟 |
| `python main.py status` / `accounts` / `report` | 状态 / 账号 / 日报 |
| `python main.py advance` | 推进阶段并刷新 L1–L4 |
| `python main.py health-check <id>` | 健康检查 |

## 养号四阶段

| 阶段 | 时长 | 核心行为 |
|------|------|---------|
| 信任积累期 | 第 1–2 周 | **14 天冷启动分相位**（种子好友、公众号、读文、发圈、小游戏、深聊等） |
| 轻度互动期 | 第 3–4 周 | 聊天、点赞、发圈 |
| 正常使用期 | 第 2–3 月 | 正常社交频率 |
| 成熟期 | 3 个月后 | 维持，可投入测试 |

深度等级 **L1–L4** 由行为日志与好友数据评分。

## 环境变量

复制 [`.env.example`](.env.example) 为 `.env`：

```bash
LLM_API_KEY=sk-xxxxxxxx
LLM_BASE_URL=https://api.deepseek.com
USE_AI_GOD_PLANNER=1    # 设为 0 关闭 AI 编排，只用静态剧本
DINGTALK_WEBHOOK=https://oapi.dingtalk.com/robot/send?access_token=xxx
```

## 项目结构

```
wechat-farm/
├── main.py                 # CLI 入口
├── config/                 # 参数、阶段、机型坐标
├── core/                   # WeChatControl + OCR / 拟人化
├── scripts/                # 阶段剧本 + 无真机单测
├── content/                # 人设、模板、AI 编排
├── scheduler/ monitor/ storage/ utils/
└── docs/                   # 手册、方案、演示图
```

Agent 运维说明：[CLAUDE.md](CLAUDE.md) · 单例跑法：[docs/单例运行.md](docs/单例运行.md) · 方案：[docs/具体执行方案.md](docs/具体执行方案.md)

## 参与贡献

见 [CONTRIBUTING.md](CONTRIBUTING.md)。提 Issue 时请脱敏日志与个人信息。

## License 与免责

[MIT](LICENSE) — 风险自负。[DISCLAIMER.md](DISCLAIMER.md) · [SECURITY.md](SECURITY.md)
