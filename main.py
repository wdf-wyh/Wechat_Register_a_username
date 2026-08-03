#!/usr/bin/env python3
"""
微信养号自动化系统 — 主入口。

用法:
    # 环境初始化（首次运行）
    python main.py init

    # 调试模式（单设备，仅执行一次剧本）
    python main.py debug

    # 生产运行（全部设备，全天候调度）
    python main.py run

    # 查看设备状态
    python main.py status

    # 查看账号列表
    python main.py accounts

    # 生成日报
    python main.py report

    # 阶段推进检查
    python main.py advance

    # 单次健康检查
    python main.py health-check <account_id>
"""

import argparse
import asyncio
import sys
import time
from datetime import datetime, date

from config.settings import settings
from config.account_stages import get_stage_for_account
from core.device import DeviceManager
from core.humanizer import Humanizer
from core.wechat_control import WeChatControl
from scripts.trust_building import TrustBuildingScript
from scripts.light_interact import LightInteractScript
from scripts.normal_use import NormalUseScript
from scripts.mature import MatureScript
from scripts.fast_test import FastTestScript
from content.personas import random_persona
from scheduler.cron_schedule import CronScheduler
from scheduler.batch_manager import BatchManager
from monitor.health_check import HealthChecker, AccountState
from monitor.alert import AlertManager
from storage.db import Database
from utils.logger import setup_logger, get_logger

# 初始化日志
setup_logger(level="INFO")
logger = get_logger("main")

# 全局实例
db = Database(settings.DB_PATH)
device_manager = DeviceManager()
batch_manager = BatchManager(db)
alert_manager = AlertManager()
cron_scheduler = CronScheduler()


# ================================================================
# 脚本工厂
# ================================================================

def get_script_for_stage(
    stage: str,
    wechat: WeChatControl,
    persona: dict,
) -> TrustBuildingScript | LightInteractScript | NormalUseScript | MatureScript:
    """根据阶段返回对应的脚本实例"""
    script_map = {
        "trust_building": TrustBuildingScript,
        "light_interact": LightInteractScript,
        "normal_use": NormalUseScript,
        "mature": MatureScript,
    }
    script_cls = script_map.get(stage, TrustBuildingScript)
    return script_cls(wechat, persona, db)


# ================================================================
# 命令：init — 环境初始化
# ================================================================

def cmd_init(args):
    """初始化数据库和设备"""
    print("=" * 50)
    print("  微信养号自动化系统 — 环境初始化")
    print("=" * 50)

    # 1. 初始化数据库
    print("\n[1/3] 初始化数据库...")
    db.init_db()
    print(f"  [OK] 数据库已创建: {settings.DB_PATH}")

    # 2. 检测设备
    print("\n[2/3] 检测 USB 设备...")
    devices = device_manager.discover_devices()
    if devices:
        print(f"  [OK] 发现 {len(devices)} 台设备: {devices}")
        for serial in devices:
            device_manager.connect_device(serial)
            info = device_manager.get_device_info(serial)
            print(f"    - {serial}: {info.get('info', {}).get('productName', 'Unknown')}")
    else:
        print("  [WARN] 未发现设备，请检查 USB 连接")

    # 3. 验证微信
    print("\n[3/3] 验证微信...")
    for serial in device_manager.get_all_serials():
        ok = device_manager.ensure_wechat_foreground(serial)
        status = "[OK] 微信已启动" if ok else "[FAIL] 微信启动失败"
        print(f"  {serial}: {status}")

    print("\n初始化完成！下一步: python main.py debug")


# ================================================================
# 命令：debug — 单设备调试
# ================================================================

async def cmd_debug(args):
    """调试模式：连接单台设备，执行一次当天剧本"""
    print("=" * 50)
    print("  调试模式 — 单设备单次执行")
    print("=" * 50)

    # 发现设备
    devices = device_manager.discover_and_connect_all()
    if not devices:
        print("[FAIL] 未发现设备，请检查 USB 连接")
        return

    # 选择设备
    serials = list(devices.keys())
    if len(serials) == 1:
        serial = serials[0]
    else:
        print(f"发现 {len(serials)} 台设备:")
        for i, s in enumerate(serials):
            print(f"  [{i}] {s}")
        idx = int(input("请选择设备编号: ") or "0")
        serial = serials[idx]

    print(f"\n使用设备: {serial}")

    # 确保微信在前台
    device_manager.ensure_wechat_foreground(serial)

    # 尝试从数据库获取账号，否则使用临时账号
    account_id = device_manager.get_bound_account(serial) or f"debug_{serial[:6]}"
    account = db.get_account(account_id)

    if account:
        stage = account.get("stage", "trust_building")
        persona_id = account.get("persona_id", "")
        print(f"账号: {account_id} | 阶段: {stage} | 人格: {persona_id}")
    else:
        stage = "trust_building"
        # 为临时账号创建数据库记录，否则外键约束会导致日志写入失败
        db.insert_account(id=account_id, device_serial=serial, stage=stage)
        db.bind_device(serial=serial, account_id=account_id)
        print(f"临时账号: {account_id} | 阶段: {stage}")

    # 创建脚本实例
    persona = random_persona()
    d = device_manager.get_device(serial)
    h = Humanizer()
    wc = WeChatControl(d, h, account_id=account_id)
    script = get_script_for_stage(stage, wc, persona)

    # 执行
    print(f"\n开始执行 {stage} 剧本...")
    result = await script.run_daily()
    print(f"\n执行完成: {result}")


# ================================================================
# 命令：fast-debug — 快速调试（3分钟浓缩版）
# ================================================================

async def cmd_fast_debug(args):
    """快速调试模式：3 分钟内跑完所有动作类型"""
    print("=" * 55)
    print("  快速调试模式 — 3分钟浓缩剧本")
    print("=" * 55)

    # 发现设备
    devices = device_manager.discover_and_connect_all()
    if not devices:
        print("[FAIL] 未发现设备，请检查 USB 连接")
        return

    # 选择设备
    serials = list(devices.keys())
    if len(serials) == 1:
        serial = serials[0]
    else:
        print(f"发现 {len(serials)} 台设备:")
        for i, s in enumerate(serials):
            print(f"  [{i}] {s}")
        idx = int(input("请选择设备编号: ") or "0")
        serial = serials[idx]

    print(f"\n使用设备: {serial}")

    # 确保微信在前台
    device_manager.ensure_wechat_foreground(serial)

    # 尝试从数据库获取账号，否则使用临时账号
    account_id = device_manager.get_bound_account(serial) or f"fast_{serial[:6]}"
    account = db.get_account(account_id)

    if account:
        persona_id = account.get("persona_id", "")
        print(f"账号: {account_id} | 人格: {persona_id}")
    else:
        # 为临时账号创建数据库记录，否则外键约束会导致日志写入失败
        db.insert_account(id=account_id, device_serial=serial, stage="fast_test")
        db.bind_device(serial=serial, account_id=account_id)
        print(f"临时账号: {account_id}")

    # 创建快速测试脚本实例（不依赖阶段，直接用 FastTestScript）
    persona = random_persona()
    d = device_manager.get_device(serial)
    h = Humanizer()
    wc = WeChatControl(d, h, account_id=account_id)
    script = FastTestScript(wc, persona, db)

    # 执行
    result = await script.run_daily()
    print(f"\n最终结果: {result}")


# ================================================================
# 命令：enterprise-smoke — 企业级真机冒烟
# ================================================================

async def cmd_enterprise_smoke(args):
    """企业级真机冒烟：冷启动编排 + 新社交动作 + 机型匹配"""
    from scripts.enterprise_smoke import run_enterprise_smoke

    serial = getattr(args, "serial", None)
    result = await run_enterprise_smoke(serial=serial)
    if not result.get("ok"):
        raise SystemExit(1)


# ================================================================
# 命令：run — 生产运行
# ================================================================

async def cmd_run(args):
    """生产模式：连接全部设备，启动定时调度"""
    print("=" * 50)
    print("  微信养号自动化系统 — 生产模式")
    print("=" * 50)

    # 发现并连接设备
    devices = device_manager.discover_and_connect_all()
    if not devices:
        print("[FAIL] 未发现设备")
        return

    print(f"已连接 {len(devices)} 台设备")

    # 获取所有活跃账号
    accounts = db.get_active_accounts()
    if not accounts:
        print("[WARN] 数据库中没有账号，请先录入账号")
        print("  使用 python main.py init 初始化后，在数据库中录入账号信息")
        return

    # 为每个账号-设备绑定创建运行任务
    async def run_account(account: dict):
        """为单个账号执行一天的剧本"""
        serial = account.get("device_serial")
        if not serial or serial not in devices:
            logger.warning(f"账号 {account['id']} 绑定的设备 {serial} 不在线")
            return

        account_id = account["id"]
        stage = account.get("stage", "trust_building")
        persona_id = account.get("persona_id", "")

        # 检查状态：cooldown 模式只执行消费类动作
        mode = account.get("mode", "full")
        state = account.get("state", "normal")

        if state in ("suspended",):
            logger.info(f"账号 {account_id} 已暂停，跳过")
            return

        logger.info(f"启动账号 {account_id}: stage={stage}, mode={mode}")

        try:
            device_manager.ensure_wechat_foreground(serial)
            d = device_manager.get_device(serial)
            h = Humanizer(seed=hash(account_id) % 10000)
            wc = WeChatControl(d, h, account_id=account_id)

            # 人格
            from content.personas import get_persona
            persona = get_persona(persona_id) or random_persona()

            script = get_script_for_stage(stage, wc, persona)
            await script.run_daily()

        except Exception as e:
            logger.error(f"账号 {account_id} 执行异常: {e}")
            alert_manager.alert_connection_lost(serial)

    # 并发执行所有账号
    tasks = [run_account(acc) for acc in accounts]
    await asyncio.gather(*tasks, return_exceptions=True)

    logger.info("本轮所有账号执行完成")


# ================================================================
# 命令：status — 设备状态
# ================================================================

def cmd_status(args):
    """查看设备状态"""
    print("=" * 40)
    print("  设备状态")
    print("=" * 40)

    devices = device_manager.discover_and_connect_all()
    if not devices:
        print("无在线设备")
        return

    for serial in device_manager.get_all_serials():
        healthy = device_manager.health_check(serial)
        info = device_manager.get_device_info(serial)
        account_id = device_manager.get_bound_account(serial) or "-"

        status_icon = "[ON]" if healthy else "[OFF]"
        model = info.get("info", {}).get("productName", "Unknown") if info else "?"
        print(f"  {status_icon} {serial} | {model} | 账号: {account_id}")


# ================================================================
# 命令：accounts — 账号列表
# ================================================================

def cmd_accounts(args):
    """查看账号列表"""
    accounts = db.get_all_accounts()
    if not accounts:
        print("数据库中没有账号")
        return

    print(f"{'ID':<12} {'阶段':<16} {'状态':<12} {'等级':<6} {'批次':<12}")
    print("-" * 60)
    for acc in accounts:
        stage_cn = {
            "trust_building": "信任积累期",
            "light_interact": "轻度互动期",
            "normal_use": "正常使用期",
            "mature": "成熟期",
        }.get(acc.get("stage", ""), acc.get("stage", ""))
        print(
            f"{acc['id']:<12} {stage_cn:<16} "
            f"{acc.get('state', 'normal'):<12} "
            f"{acc.get('level', 'L1'):<6} "
            f"{acc.get('batch_name', '-'):<12}"
        )

    # 阶段分布
    summary = batch_manager.get_stage_summary()
    print(f"\n阶段分布: {summary}")


# ================================================================
# 命令：report — 日报
# ================================================================

def cmd_report(args):
    """生成日报"""
    print("=" * 40)
    print(f"  养号日报 — {datetime.now().strftime('%Y-%m-%d')}")
    print("=" * 40)

    report = batch_manager.get_batch_report()
    print(f"\n总计账号: {report['total']}")
    print(f"阶段分布: {report['by_stage']}")
    print(f"状态分布: {report['by_state']}")

    # 今日操作统计
    accounts = db.get_active_accounts()
    total_actions = 0
    total_success = 0
    today = date.today().isoformat()

    for acc in accounts:
        stats = db.get_daily_stats(acc["id"], today)
        total_actions += stats.get("total", 0)
        total_success += stats.get("success_count", 0)

    if total_actions > 0:
        rate = total_success / total_actions * 100
        print(f"\n今日操作: {total_actions} 次 | 成功率: {rate:.1f}%")
    else:
        print("\n今日尚无操作记录")


# ================================================================
# 命令：advance — 阶段推进
# ================================================================

def cmd_advance(args):
    """检查并推进所有账号的阶段"""
    print("检查阶段推进...")
    batch_manager.advance_all_stages()

    summary = batch_manager.get_stage_summary()
    print(f"当前阶段分布: {summary}")

    mature = batch_manager.get_mature_accounts()
    if mature:
        print(f"\n成熟期账号 ({len(mature)} 个):")
        for acc in mature:
            print(f"  - {acc['id']} (注册: {acc.get('registration_date', '?')})")


# ================================================================
# 命令：health-check — 单次健康检查
# ================================================================

def cmd_health_check(args):
    """对指定账号执行健康检查"""
    account_id = args.account_id
    account = db.get_account(account_id)
    if not account:
        print(f"[FAIL] 账号不存在: {account_id}")
        return

    serial = account.get("device_serial")
    if not serial:
        print(f"[FAIL] 账号 {account_id} 未绑定设备")
        return

    d = device_manager.get_device(serial)
    if not d:
        print(f"[FAIL] 设备 {serial} 不在线")
        return

    h = Humanizer()
    wc = WeChatControl(d, h, account_id=account_id)
    checker = HealthChecker(wc, db)

    result = checker.check_all()
    print(f"\n账号 {account_id} 健康检查结果:")
    print(f"  朋友圈可见: {result['moments_visible']}")
    print(f"  消息延迟: {result['message_delay_ms']:.0f}ms" if result['message_delay_ms'] else "  消息延迟: 无法检测")
    print(f"  滑块验证: {result['captcha_count']} 次/天")
    print(f"  风险评分: {result['risk_score']:.2f}")
    print(f"  状态: {result['state']}")
    if result['suggestions']:
        print(f"  建议: {'; '.join(result['suggestions'])}")


# ================================================================
# CLI 入口
# ================================================================

def main():
    parser = argparse.ArgumentParser(
        description="微信养号自动化系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py init                  # 初始化环境
  python main.py debug                 # 单设备调试
  python main.py run                   # 全部设备运行
  python main.py status                # 查看设备状态
  python main.py accounts              # 查看账号列表
  python main.py report                # 生成日报
  python main.py advance               # 检查阶段推进
  python main.py health-check acc_001  # 账号健康检查
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # init
    p_init = subparsers.add_parser("init", help="初始化环境和数据库")

    # debug
    p_debug = subparsers.add_parser("debug", help="调试模式（单设备单次执行）")

    # fast-debug
    p_fast = subparsers.add_parser("fast-debug", help="快速调试模式（3分钟浓缩剧本）")

    # enterprise-smoke
    p_ent = subparsers.add_parser(
        "enterprise-smoke",
        help="企业级真机冒烟（冷启动/新动作/机型匹配）",
    )
    p_ent.add_argument("--serial", default=None, help="指定设备序列号")

    # run
    p_run = subparsers.add_parser("run", help="生产模式（全部设备）")

    # status
    p_status = subparsers.add_parser("status", help="查看设备状态")

    # accounts
    p_accounts = subparsers.add_parser("accounts", help="查看账号列表")

    # report
    p_report = subparsers.add_parser("report", help="生成日报")

    # advance
    p_advance = subparsers.add_parser("advance", help="检查阶段推进")

    # health-check
    p_health = subparsers.add_parser("health-check", help="账号健康检查")
    p_health.add_argument("account_id", help="账号 ID")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    # 路由
    if args.command == "init":
        cmd_init(args)
    elif args.command == "debug":
        asyncio.run(cmd_debug(args))
    elif args.command == "fast-debug":
        asyncio.run(cmd_fast_debug(args))
    elif args.command == "enterprise-smoke":
        asyncio.run(cmd_enterprise_smoke(args))
    elif args.command == "run":
        asyncio.run(cmd_run(args))
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "accounts":
        cmd_accounts(args)
    elif args.command == "report":
        cmd_report(args)
    elif args.command == "advance":
        cmd_advance(args)
    elif args.command == "health-check":
        cmd_health_check(args)


if __name__ == "__main__":
    main()
