"""
数据库操作模块 — SQLite 数据库的初始化、CRUD 操作封装。

使用方式:
    db = Database("wechat_farm.db")
    db.init_db()
    db.insert_account(account_id="acc_001", wechat_id="wxid_xxx", ...)
    accounts = db.get_all_accounts()
"""

import json
import sqlite3
from pathlib import Path
from typing import Optional

from utils.logger import get_logger

logger = get_logger("database")


class Database:
    """SQLite 数据库操作封装"""

    def __init__(self, db_path: str = "wechat_farm.db"):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    @property
    def conn(self) -> sqlite3.Connection:
        """获取数据库连接（懒初始化）"""
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def init_db(self):
        """
        初始化数据库（建表）。

        从 schema.sql 读取建表语句并执行。
        """
        schema_path = Path(__file__).parent / "schema.sql"
        if not schema_path.exists():
            logger.error(f"schema 文件不存在: {schema_path}")
            raise FileNotFoundError(f"schema.sql not found at {schema_path}")

        schema_sql = schema_path.read_text(encoding="utf-8")
        try:
            self.conn.executescript(schema_sql)
            self.conn.commit()
            logger.info("数据库初始化完成")
        except Exception as e:
            logger.error(f"数据库初始化失败: {e}")
            raise

    def close(self):
        """关闭数据库连接"""
        if self._conn:
            self._conn.close()
            self._conn = None

    # ================================================================
    # 账号 CRUD
    # ================================================================

    def insert_account(self, **kwargs) -> str:
        """
        插入新账号。

        必填: id
        可选: wechat_id, phone, device_serial, imei, sim_number,
              registration_date, batch_name, stage, persona_id, level, notes
        """
        fields = list(kwargs.keys())
        placeholders = ", ".join([f":{f}" for f in fields])
        columns = ", ".join(fields)
        sql = f"INSERT OR REPLACE INTO accounts ({columns}) VALUES ({placeholders})"
        self.conn.execute(sql, kwargs)
        self.conn.commit()
        logger.info(f"账号已录入: {kwargs.get('id')}")
        return kwargs.get("id", "")

    def get_account(self, account_id: str) -> Optional[dict]:
        """获取单个账号信息"""
        row = self.conn.execute(
            "SELECT * FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_all_accounts(self) -> list[dict]:
        """获取所有账号"""
        rows = self.conn.execute("SELECT * FROM accounts ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]

    def get_accounts_by_stage(self, stage: str) -> list[dict]:
        """按阶段获取账号列表"""
        rows = self.conn.execute(
            "SELECT * FROM accounts WHERE stage = ?", (stage,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_active_accounts(self) -> list[dict]:
        """获取所有活跃账号（非暂停）"""
        rows = self.conn.execute(
            "SELECT * FROM accounts WHERE mode != 'paused' AND state != 'suspended'"
        ).fetchall()
        return [dict(r) for r in rows]

    def update_account(self, account_id: str, **kwargs):
        """更新账号字段"""
        if not kwargs:
            return
        sets = ", ".join([f"{k} = :{k}" for k in kwargs])
        kwargs["account_id"] = account_id
        self.conn.execute(
            f"UPDATE accounts SET {sets}, updated_at = datetime('now', 'localtime') "
            f"WHERE id = :account_id",
            kwargs,
        )
        self.conn.commit()

    def set_account_state(self, account_id: str, state: str):
        """更改账号状态"""
        self.update_account(account_id, state=state)

    def set_account_mode(self, account_id: str, mode: str):
        """更改账号行为模式"""
        self.update_account(account_id, mode=mode)

    # ================================================================
    # 行为日志
    # ================================================================

    def log_action(
        self,
        account_id: str,
        action_type: str,
        success: bool = True,
        error_msg: str = "",
        action_params: dict = None,
        screenshot_path: str = "",
    ) -> int:
        """记录一条操作日志"""
        rowid = self.conn.execute(
            """INSERT INTO action_logs (account_id, action_type, action_params,
               executed_at, success, error_msg, screenshot_path)
               VALUES (?, ?, ?, datetime('now', 'localtime'), ?, ?, ?)""",
            (
                account_id,
                action_type,
                json.dumps(action_params, ensure_ascii=False) if action_params else None,
                1 if success else 0,
                error_msg or None,
                screenshot_path or None,
            ),
        ).lastrowid
        self.conn.commit()
        return rowid

    def get_action_logs(
        self,
        account_id: str,
        limit: int = 50,
        date: str = None,
    ) -> list[dict]:
        """获取指定账号的操作日志"""
        if date:
            rows = self.conn.execute(
                """SELECT * FROM action_logs
                   WHERE account_id = ? AND date(executed_at) = ?
                   ORDER BY executed_at DESC LIMIT ?""",
                (account_id, date, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM action_logs WHERE account_id = ? "
                "ORDER BY executed_at DESC LIMIT ?",
                (account_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_daily_stats(self, account_id: str, date: str) -> dict:
        """获取某账号当天的操作统计"""
        row = self.conn.execute(
            """SELECT COUNT(*) as total,
                      SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as success_count,
                      SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as fail_count
               FROM action_logs
               WHERE account_id = ? AND date(executed_at) = ?""",
            (account_id, date),
        ).fetchone()
        return dict(row) if row else {}

    # ================================================================
    # 好友管理
    # ================================================================

    def add_friend(self, account_id: str, friend_name: str, **kwargs):
        """记录好友"""
        self.conn.execute(
            """INSERT INTO friends (account_id, friend_name, friend_wechat_id, source, added_date)
               VALUES (?, ?, ?, ?, date('now', 'localtime'))""",
            (account_id, friend_name, kwargs.get("friend_wechat_id"),
             kwargs.get("source")),
        )
        self.conn.commit()

    def get_friends(self, account_id: str, source: str = None) -> list[dict]:
        """获取账号好友列表；source 可选过滤（如 group）。"""
        if source:
            rows = self.conn.execute(
                "SELECT * FROM friends WHERE account_id = ? AND source = ?",
                (account_id, source),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM friends WHERE account_id = ?", (account_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_random_friend(
        self,
        account_id: str,
        exclude_groups: bool = False,
    ) -> Optional[dict]:
        """随机获取一个好友"""
        if exclude_groups:
            row = self.conn.execute(
                """SELECT * FROM friends
                   WHERE account_id = ? AND IFNULL(source, '') != 'group'
                   ORDER BY RANDOM() LIMIT 1""",
                (account_id,),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM friends WHERE account_id = ? ORDER BY RANDOM() LIMIT 1",
                (account_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_today_stats(self, account_id: str) -> dict:
        """今日动作成功/失败统计。"""
        rows = self.conn.execute(
            """SELECT action_type, success, COUNT(*) AS cnt
               FROM action_logs
               WHERE account_id = ? AND date(executed_at)=date('now', 'localtime')
               GROUP BY action_type, success""",
            (account_id,),
        ).fetchall()
        stats: dict = {"by_action": {}, "fail_total": 0, "ok_total": 0}
        for r in rows:
            key = r["action_type"]
            bucket = stats["by_action"].setdefault(key, {"ok": 0, "fail": 0})
            if r["success"]:
                bucket["ok"] += r["cnt"]
                stats["ok_total"] += r["cnt"]
            else:
                bucket["fail"] += r["cnt"]
                stats["fail_total"] += r["cnt"]
        return stats

    def get_recent_failed_actions(self, account_id: str, limit: int = 8) -> list[dict]:
        """最近失败动作列表。"""
        rows = self.conn.execute(
            """SELECT action_type, error_msg, executed_at
               FROM action_logs
               WHERE account_id = ? AND success = 0
               ORDER BY executed_at DESC LIMIT ?""",
            (account_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ================================================================
    # 朋友圈记录
    # ================================================================

    def record_moment(self, account_id: str, content: str, **kwargs):
        """记录朋友圈"""
        self.conn.execute(
            """INSERT INTO moments (account_id, content, has_images, image_paths, posted_at)
               VALUES (?, ?, ?, ?, datetime('now', 'localtime'))""",
            (account_id, content, kwargs.get("has_images", 0),
             kwargs.get("image_paths")),
        )
        self.conn.commit()

    def get_moments_count(self, account_id: str, days: int = 7) -> int:
        """获取最近 N 天朋友圈数量"""
        row = self.conn.execute(
            """SELECT COUNT(*) as cnt FROM moments
               WHERE account_id = ? AND posted_at >= datetime('now', 'localtime', ?)""",
            (account_id, f'-{days} days'),
        ).fetchone()
        return row["cnt"] if row else 0

    # ================================================================
    # 健康检查
    # ================================================================

    def record_health_check(self, **kwargs):
        """记录一次健康检查"""
        fields = list(kwargs.keys())
        placeholders = ", ".join([f":{f}" for f in fields])
        columns = ", ".join(fields)
        self.conn.execute(
            f"INSERT INTO health_checks ({columns}) VALUES ({placeholders})", kwargs
        )
        self.conn.commit()

    def get_latest_health_check(self, account_id: str) -> Optional[dict]:
        """获取最近一次健康检查"""
        row = self.conn.execute(
            "SELECT * FROM health_checks WHERE account_id = ? "
            "ORDER BY check_time DESC LIMIT 1",
            (account_id,),
        ).fetchone()
        return dict(row) if row else None

    # ================================================================
    # 设备绑定
    # ================================================================

    def bind_device(self, serial: str, account_id: str, **kwargs):
        """绑定设备与账号"""
        self.conn.execute(
            """INSERT OR REPLACE INTO device_bindings
               (serial, account_id, model, android_version, last_seen)
               VALUES (?, ?, ?, ?, datetime('now', 'localtime'))""",
            (serial, account_id, kwargs.get("model", ""),
             kwargs.get("android_version", "")),
        )
        self.conn.commit()

    def get_device_account(self, serial: str) -> Optional[str]:
        """获取设备绑定的账号 ID"""
        row = self.conn.execute(
            "SELECT account_id FROM device_bindings WHERE serial = ?", (serial,)
        ).fetchone()
        return row["account_id"] if row else None
