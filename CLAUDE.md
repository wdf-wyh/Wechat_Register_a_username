# CLAUDE.md — 微信养号自动化系统

> 本文件供上层调度 Agent（Claude Code / Cursor / 自定义脚本）阅读，
> 帮助 Agent 理解项目架构、正确调用命令、处理常见故障。

---

## 一、项目概述

### 1.1 这是什么

一个基于 **uiautomator2 + Python + EasyOCR** 的微信自动化养号系统，运行在 Windows/Linux 服务器上，通过 USB 连接多台 Android 真机，模拟真人日常使用微信，批量养出 L1~L4 级别的深度使用微信号，用于 Moto 手机的性能 benchmark 测试。

### 1.2 核心原理

- 每台手机 = 1 个微信号 = 1 张独立 4G SIM 卡，物理隔离
- uiautomator2 通过 ADB 操控手机，执行点击、滑动、输入等操作
- **微信屏蔽了 Android 无障碍控件树（FLAG_SECURE）**，所以用 EasyOCR 文字识别替代控件定位
- 所有操作通过**拟人化引擎**加入随机性（Bézier 滑动、正态偏移、对数间隔），防止被微信风控识别

### 1.3 技术栈

| 层 | 技术 |
|----|------|
| 设备 | Android 11+ 真机（Moto 测试机） |
| 通信 | ADB（USB 连接） |
| 控制 | uiautomator2 |
| 定位 | EasyOCR + OpenCV + 百分比坐标 fallback |
| 调度 | Python asyncio / Cron |
| 数据 | SQLite |
| LLM | DeepSeek API（文案话术 + **AI 上帝视角编排当日剧本**） |

---

## 二、项目结构

```
wechat_farm/
├── main.py                  # CLI 主入口
├── CLAUDE.md                # 本文件
├── config/                  # 配置: 全局参数、阶段规则、元素定位字典
├── core/                    # 核心模块 (15 个)
│   ├── wechat_control.py    # ← 统一调用入口 (WeChatControl)
│   ├── moments_interact.py  # 朋友圈点赞/评论 (OCR 方案)
│   ├── moment_poster.py     # 发朋友圈 (6 阶段自动)
│   ├── message_sender.py    # 发送文字消息
│   ├── image_sender.py      # 发送图片消息
│   ├── channels_browser.py  # 刷视频号
│   ├── public_account_browser.py  # 浏览公众号
│   ├── favorites_browser.py # 浏览收藏夹
│   ├── search_helper.py     # 全局搜索
│   ├── social_actions.py    # 关注公众号/加好友/群聊深聊/小程序/官方小游戏
│   ├── humanizer.py         # 拟人化引擎
│   ├── device.py            # 设备管理
│   └── element_locator.py   # 元素定位辅助
├── scripts/                 # 行为剧本
│   ├── base_script.py       # 基类 (ActionType 枚举 + handler 映射 + AI 编排入口)
│   ├── cold_start_templates.py  # 新号 14 天冷启动规则模板
│   ├── trust_building.py    # 信任积累期 (第 1-2 周，按注册日分相位)
│   ├── light_interact.py    # 轻度互动期 (第 3-4 周)
│   ├── normal_use.py        # 正常使用期 (第 2-3 月)
│   ├── mature.py            # 成熟期 (3 个月后)
│   └── fast_test.py         # 快速调试 (约 10 分钟)
├── scheduler/               # 调度层
├── monitor/                 # 健康检查 (18 项)
├── storage/                 # SQLite 数据库
├── content/                 # 文案模板 + LLM 客户端 + AI 上帝视角
│   ├── llm_client.py        # 文案/评论/深聊/当日剧本 JSON
│   ├── ai_god_planner.py    # ← 上帝视角编排器（上下文→LLM→安全 clamp）
│   └── personas.py          # 人设（含 seed_friends/groups/public_accounts）
└── utils/                   # 日志、ADB 工具
```

---

## 三、CLI 命令参考

### 3.1 日常运维

| 命令 | 用途 | 何时使用 |
|------|------|---------|
| `python main.py init` | 初始化数据库，检测设备 | 首次部署 |
| `python main.py run` | 全部账号执行当日养号剧本 | **每日定时执行** |
| `python main.py debug` | 单设备执行一次当日剧本 | 测试/排查 |
| `python main.py fast-debug` | 一键跑通全部核心功能 (~10min) | 验证环境是否正常 |
| `python main.py status` | 查看所有设备在线状态 | 检查设备连接 |
| `python main.py accounts` | 查看所有账号列表 | 检查账号状态 |
| `python main.py report` | 生成当日运行报告 | 每日总结 |
| `python main.py advance` | 推进所有账号的养号阶段 | 每周执行 |
| `python main.py health-check <id>` | 对指定账号执行健康检查 | 排查异常 |

### 3.2 直接调用核心功能（调试用）

```python
from core.wechat_control import WeChatControl
from core.humanizer import Humanizer
import uiautomator2 as u2

d = u2.connect()
wc = WeChatControl(d, Humanizer())

wc.open_moments()                              # 进入朋友圈
wc.scroll_moments(5)                           # 刷 5 次
wc.like_moment(0)                              # 点赞第 1 条
wc.comment_moment("说得好", post_index=1)       # 评论第 2 条
wc.browse_moments_interact(300, "哈哈", 0.55)   # 浏览 5min 随机互动
wc.post_moment("文案", image_count=3)           # 发朋友圈 3 张图
wc.send_message("你好", contact="张三")         # 发文字
wc.global_search("天气")                       # 全局搜索
wc.scroll_channels(duration_seconds=600, finish_watch=True, comment_rate=0.18)  # 视频号约10分钟完播+评论
wc.browse_favorites(120)                       # 浏览收藏夹 2min
wc.play_mini_game("跳一跳", duration_seconds=180)  # 官方小游戏约 3 分钟
```

---

## 四、调度配置

### 4.1 Cron 推荐方案

```bash
# 每天早上 7:00 执行当日剧本
0 7 * * * cd /path/to/wechat_farm && ./wechat_env/bin/python main.py run >> logs/cron.log 2>&1

# 每天晚上 23:00 生成日报
0 23 * * * cd /path/to/wechat_farm && ./wechat_env/bin/python main.py report >> logs/cron.log 2>&1

# 每周一早上 8:00 检查阶段推进
0 8 * * 1 cd /path/to/wechat_farm && ./wechat_env/bin/python main.py advance >> logs/cron.log 2>&1
```

### 4.2 Agent 启动后需确认的定时监控（Claude Code 用 /cron 设置）

Agent 启动后应提醒用户执行以下命令，激活 24h 监控：

```
/cron 每天 7:05 检查昨晚日志，有失败动作就通知我
/cron 每天 22:00 生成今日报告总结
/cron 每 4 小时 检查 adb devices，设备离线通知我
```

> 注意：`/cron` 是 Claude Code 特有命令。如果用其他 Agent，改用 Cron 定期调用 Agent API。

### 4.3 Agent 的职责

Cron 负责**准时执行**，Agent 负责**智能决策和故障处理**：

- 每天查看 `python main.py report` 的输出，判断是否全部成功
- 发现失败日志时，分析原因并尝试补救（如重启设备、重试命令）
- 监控 `logs/error_*.log`，发现异常时通知
- 手机 USB 断连时提醒人工介入
- 微信版本更新后，提醒检查定位是否失效

### 4.4 文件修改权限

| 目录 | 权限 | 说明 |
|------|------|------|
| `core/` | **只读** | 除非用户明确要求 fix bug，否则不得修改 |
| `config/` | **只读** | 定位字典和参数，改动可能影响全部账号 |
| `config/device_profiles/` | **可新增** | 新机型只加新文件并注册，禁止改旧机型文件覆盖坐标 |
| `scripts/` | 可改 | 行为剧本，用户要求时可调整时间和频率 |
| `storage/schema.sql` | **禁止** | 改表结构会导致数据丢失 |
| `CLAUDE.md` | 可改 | 本文件，用户要求时可更新 |
| 其他 | 可改 | `main.py`、`README.md`、测试脚本等 |

### 4.4.1 多机型坐标适配（重要）

不同分辨率手机的百分比坐标不同。**禁止**为适配新机型直接改全局坐标，覆盖旧机型。

正确做法：
1. 在 `config/device_profiles/` 新增 `<机型>.py`（复制已有机型文件改坐标）
2. 在 `config/device_profiles/__init__.py` 的 `PROFILES` 列表注册
3. 运行时按分辨率/型号自动匹配（`resolve_profile(d)`）

已内置：
- `moto_x70_air_pro` — 1264×2780（默认）
- `redmi_k30_pro` — 1080×2400

```python
from config.device_profiles import resolve_profile, list_profiles
print(list_profiles())
print(resolve_profile(d).display_name)
```


### 4.5 Agent 日常监控流程

每天自动执行以下检查（无需用户交互）：

```
1. 检查 logs/error_*.log — 是否有新的错误
2. 检查 logs/wechat_farm_*.log — 今日动作执行情况
3. sqlite3 查询 — 今日失败动作
4. adb devices — 设备是否在线
```

**处理规则：**

| 情况 | 处理 |
|------|------|
| 0 个失败 | 无需操作，静默通过 |
| 1~2 个失败 | 记录，连续 3 天同一动作失败则通知用户 |
| 3+ 个失败 | 通知用户，建议运行 `health-check` |
| 设备掉线 | 通知用户检查 USB |
| 全部失败 | 通知用户，可能是微信版本更新 |

### 4.6 需要用户确认的事项（Agent 不可自行决定）

- 推进账号阶段前
- 修改剧本参数前
- 重试失败操作超过 3 次
- 账号状态从 normal 改为 cooldown/suspended
- 删除日志或数据库记录

---

## 五、养号四阶段

| 阶段 | 时长 | 核心行为 | 剧本文件 |
|------|------|---------|---------|
| 信任积累期 | 第 1-2 周 | **14 天冷启动分相位**（见下） | `trust_building.py` + `cold_start_templates.py` |
| 轻度互动期 | 第 3-4 周 | 开始聊天、点赞、发圈、深聊、小游戏 | `light_interact.py` |
| 正常使用期 | 第 2-3 月 | 正常社交频率，全面互动 | `normal_use.py` |
| 成熟期 | 3 个月后 | 自然维持，可投入测试 | `mature.py` |

### 5.1 新号 14 天冷启动相位（trust_building）

| 天 | 相位 | 自动化内容 | 跳过（人工） |
|----|------|-----------|-------------|
| 1 | 社交种子·Day1 | 加 1 好友、关注 2 个行业公众号、读文 10 分钟并留言 | 头像/昵称/绑卡/红包/线下消费 |
| 2-3 | 社交种子 | 添加种子好友（2/2）、关注 2 个行业公众号、读文、搜索、视频号、小程序、打开支付页 | 同上 |
| 4-7 | 内容生态 | 发圈、群发言、轻点赞、**跳一跳等官方小游戏** | — |
| 8-10 | 深度互动 | 限量加好友、深聊、朋友圈高频互动 | 需预填 `seed_friends` |
| 11-14 | 场景渗透 | 视频号加长+评论、小游戏、继续互动 | 真实电商下单 |

人设种子字段（`content/personas.py`）:
- `public_accounts` — 关注目标
- `seed_friends` — 可搜索添加的微信号/昵称（空则跳过加好友；申请发出即写 DB，对方未必已通过）
- `chat_friends` — 已互为好友、供深聊的备注名/昵称（与 seed_friends 分开；空则仅用 DB 中非 seed 好友）
- `seed_groups` — 群名（也可把群写入 DB `friends.source='group'`）

### 5.2 AI 上帝视角编排

默认开启（`settings.USE_AI_GOD_PLANNER=True`，可用环境变量关闭）。

流程：
1. `BaseScript.run_daily` → `AiGodPlanner.plan_day`
2. 汇总注册天数 / 健康状态 / 今日统计 / 近期失败 / 硬限
3. LLM 输出当日 `actions` JSON
4. **安全 clamp**：剔除人工动作、行为禁忌（群发/自动回复）、夜间操作、超阶段上限；首周加好友≤3（相位常为 0）；`consume_only` 只留浏览类
5. LLM 不可用或解析失败 → 回退 `cold_start_templates`

```bash
# 关闭 AI 编排，只用规则模板
set USE_AI_GOD_PLANNER=0
```

阶段推进逻辑：`python main.py advance` 根据 `registration_date` 计算天数：
- `0~14 天` → trust_building
- `15~28 天` → light_interact
- `29~88 天` → normal_use
- `88 天以上` → mature

---

## 六、数据库

### 6.1 关键表

- `accounts` — 账号信息（id, wechat_id, stage, persona_id, state, mode）
- `devices` — 设备绑定（serial ↔ account_id）
- `action_logs` — 每次操作的日志（account_id, action_type, success, error_msg, screenshot_path）
- `friends` — 好友列表
- `health_checks` — 健康检查结果

### 6.2 常用查询

```bash
# 查看所有账号状态
sqlite3 wechat_farm.db "SELECT id, stage, state, mode FROM accounts;"

# 查看今日失败操作
sqlite3 wechat_farm.db "SELECT account_id, action_type, error_msg FROM action_logs WHERE success=0 AND date(executed_at)=date('now');"

# 查看今日操作统计
sqlite3 wechat_farm.db "SELECT account_id, action_type, COUNT(*) FROM action_logs WHERE date(executed_at)=date('now') GROUP BY account_id, action_type;"
```

---

## 七、故障排查

### 7.1 常见问题

| 症状 | 可能原因 | 解决 |
|------|---------|------|
| `ModuleNotFoundError: No module named 'uiautomator2'` | 没激活虚拟环境 | 用 `wechat_env/bin/python` 或 `wechat_env\Scripts\python.exe` |
| 手机锁屏后自动化卡住 | 没开"不锁定屏幕" | 去开发者选项开启，代码已有息屏唤醒但不可靠 |
| `adb devices` 显示 offline | USB 线或端口问题 | 重插 USB，换线，检查 Hub 供电 |
| EasyOCR 首次运行慢 | 下载模型 ~100MB | 仅首次，后续使用缓存 |
| 微信更新后定位失败 | resourceId 变化 | 用 weditor 重新抓取，更新 `config/wechat_elements.py` |
| 操作成功率突然下降 | 微信版本更新或账号被限制 | 运行 `health-check` 排查 |
| OCR 找不到联系人 | 用户改了字体 | 这是已知限制，MSG_SEND 可能失败但 IMG_SEND 有 fallback |

### 7.2 崩溃恢复流程

1. 检查 `adb devices` — 设备是否在线
2. 检查 `python main.py status` — 账号状态
3. 查看 `logs/error_*.log` — 最近的错误
4. 运行 `python main.py health-check <id>` — 针对性排查
5. 运行 `python main.py fast-debug` — 验证基础功能是否正常
6. 如果基础功能正常，重跑 `python main.py run`

### 7.3 自动恢复策略（来自设计文档）

系统内置了分级恢复策略，Agent 应根据以下规则自动处理：

| 检测到的问题 | 恢复操作 | 恢复条件 |
|-------------|---------|---------|
| 朋友圈不可见 | 停止发朋友圈 ≥ 7 天 | 连续 3 天正常后恢复 |
| 加好友需验证 | 停止加好友 ≥ 7 天 | 连续 3 天正常后恢复 |
| 消息延迟 > 5s | 减少操作 48-72h | 延迟 < 1s 后恢复 |
| 滑块验证 > 3 次/天 | 停止所有操作 24h | 24h 内 0 次后恢复 |
| 功能受限 | 切换 `mode="consume_only"` | 人工介入判断 |

**重要**: `consume_only` 模式下脚本仅执行浏览类动作（刷朋友圈、看视频号、读文章），不进行任何互动（点赞、评论、聊天）。

### 7.4 账号状态管理

| 状态 | 含义 | Agent 应如何处理 |
|------|------|-----------------|
| `normal` | 正常 | 照常执行剧本 |
| `warning` | 有风险信号 | 减少互动频率，增加监控 |
| `cooldown` | 需要冷静期 | 仅执行消费类动作 |
| `suspended` | 已暂停 | 跳过该账号，等待人工处理 |

可通过 SQL 切换状态：
```sql
UPDATE accounts SET state='cooldown', mode='consume_only' WHERE id='acc_xxx';
```

### 7.5 紧急情况

- **设备掉线**：检查 USB 连接，重新插拔后运行 `python main.py init` 重检
- **批量失败**：检查是否微信版本更新导致定位全部失效
- **账号被风控**：立即停止该账号的所有操作

---

## 八、环境变量

优先级：系统/当前进程已设置的环境变量 > `.env` 文件（项目根目录）。

如果你不想每次都在终端里手动导出，可以在项目根目录放一个 `.env` 文件（或设置 `WECHAT_FARM_ENV` 来加载 `.env.<环境名>`），使用 `KEY=VALUE` 格式，例如：

```bash
# 项目根目录：Wechat_farm/.env
LLM_API_KEY=sk-xxxxxxxx
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=doubao-seed-2-1-pro-260628
USE_AI_GOD_PLANNER=1
```

或者继续使用终端环境变量方式（示例）：

```bash
# LLM API（可选，不设置则用本地模板库 + 规则剧本）
export LLM_API_KEY=sk-xxxxxxxx
export LLM_BASE_URL=https://api.deepseek.com

# AI 上帝视角编排（默认开启；0/false 关闭）
export USE_AI_GOD_PLANNER=1

# 钉钉告警（可选）
export DINGTALK_WEBHOOK=https://oapi.dingtalk.com/robot/send?access_token=xxx
```

---

## 九、红线（绝对禁止）

- ❌ 使用模拟器或云手机（3 分钟内被识别封号）
- ❌ 使用微信多开/外挂/Xposed 框架
- ❌ 同一 IP 批量操作多个微信号
- ❌ 养的号互相加好友/进同一群
- ❌ 使用 Hook/内存注入方式操控微信
- ❌ 批量同时加好友（单日超阈值即功能限制）

---

## 十、测试环境

| 项目 | 值 |
|------|-----|
| 测试设备 | Moto X70 Air Pro (1264×2780, Android 14) |
| 测试账号 | 稀有气体 |
| Python | 3.14.4 |
| 虚拟环境 | `wechat_env/` |

---

> **项目版本**: v2.5 | **更新**: 2026-08-05
> **详细方案**: 参见 `../具体执行方案.md`
> **本版新增**: 官方小游戏（发现→游戏→跳一跳等）、14 天冷启动分相位、AI 上帝视角编排器
