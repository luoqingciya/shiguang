from __future__ import annotations

import sqlite3
from contextlib import closing

from .themes import CHECKIN_THEMES

CHECKIN_DB_SCHEMA_VERSION = 3


class UnversionedCheckinDatabaseError(RuntimeError):
    """签到数据库存在数据表但缺少 schema 版本号（user_version=0）。"""


class SchemaMixin:
    def _connect(self) -> sqlite3.Connection:
        # M5/清理项：busy timeout 10s（默认 5s），瞬时写锁冲突时有限等待避免误报
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        with closing(self._connect()) as conn:
            schema_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if schema_version not in (0, 1, 2, CHECKIN_DB_SCHEMA_VERSION):
                raise RuntimeError(f"unsupported check-in database schema: {schema_version}")
            if (
                schema_version == 0
                and conn.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' LIMIT 1"
                ).fetchone()
            ):
                raise UnversionedCheckinDatabaseError(
                    "unversioned non-empty check-in database is unsupported"
                )
            # WAL 切换不能在事务内执行，需先于 BEGIN IMMEDIATE。
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("BEGIN IMMEDIATE")
            try:
                if schema_version in (1, 2, CHECKIN_DB_SCHEMA_VERSION):
                    self._ensure_v2_record_columns(conn)
                if schema_version in (2, CHECKIN_DB_SCHEMA_VERSION):
                    self._ensure_v3_user_columns(conn)
                self._create_checkin_schema(conn)
                self._sync_builtin_themes(conn)
                conn.execute(f"PRAGMA user_version = {CHECKIN_DB_SCHEMA_VERSION}")
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    @staticmethod
    def _ensure_v2_record_columns(conn: sqlite3.Connection) -> None:
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(checkin_records)").fetchall()
        }
        if "render_tier" not in columns:
            conn.execute(
                "ALTER TABLE checkin_records ADD COLUMN render_tier TEXT NOT NULL DEFAULT '省流量'"
            )
        if "background_quality" not in columns:
            conn.execute(
                "ALTER TABLE checkin_records ADD COLUMN background_quality TEXT NOT NULL DEFAULT ''"
            )

    @staticmethod
    def _ensure_v3_user_columns(conn: sqlite3.Connection) -> None:
        """v3 迁移：为历史库补齐补签卡 / 月卡字段。新库由 CREATE 直接建列。"""
        columns = {
            str(row["name"]) for row in conn.execute("PRAGMA table_info(checkin_users)").fetchall()
        }
        if "makeup_cards" not in columns:
            conn.execute(
                "ALTER TABLE checkin_users ADD COLUMN makeup_cards INTEGER NOT NULL DEFAULT 0"
            )
        if "monthly_card_until" not in columns:
            conn.execute(
                "ALTER TABLE checkin_users ADD COLUMN monthly_card_until TEXT NOT NULL DEFAULT ''"
            )

    @staticmethod
    def _create_checkin_schema(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS checkin_themes (
                theme_id TEXT PRIMARY KEY,
                code TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                version INTEGER NOT NULL DEFAULT 1,
                price INTEGER NOT NULL DEFAULT 0,
                enabled INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS checkin_users (
                user_id TEXT PRIMARY KEY,
                coins INTEGER NOT NULL DEFAULT 0,
                affection REAL NOT NULL DEFAULT 0,
                total_days INTEGER NOT NULL DEFAULT 0,
                streak_days INTEGER NOT NULL DEFAULT 0,
                last_checkin_date TEXT NOT NULL DEFAULT '',
                boost_start_date TEXT NOT NULL DEFAULT '',
                boost_until_date TEXT NOT NULL DEFAULT '',
                repeat_penalty_date TEXT NOT NULL DEFAULT '',
                repeat_penalty_total REAL NOT NULL DEFAULT 0,
                birthday_month INTEGER NOT NULL DEFAULT 0,
                birthday_day INTEGER NOT NULL DEFAULT 0,
                birthday_source TEXT NOT NULL DEFAULT '',
                qq_birthday_checked INTEGER NOT NULL DEFAULT 0,
                selected_title_id TEXT NOT NULL DEFAULT '',
                current_theme_id TEXT NOT NULL DEFAULT 'default',
                makeup_cards INTEGER NOT NULL DEFAULT 0,
                monthly_card_until TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (current_theme_id) REFERENCES checkin_themes(theme_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS checkin_user_themes (
                user_id TEXT NOT NULL,
                theme_id TEXT NOT NULL,
                price_paid INTEGER NOT NULL DEFAULT 0,
                acquired_at TEXT NOT NULL,
                PRIMARY KEY (user_id, theme_id),
                FOREIGN KEY (user_id) REFERENCES checkin_users(user_id) ON DELETE CASCADE,
                FOREIGN KEY (theme_id) REFERENCES checkin_themes(theme_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS checkin_records (
                date_key TEXT NOT NULL,
                user_id TEXT NOT NULL,
                username TEXT NOT NULL DEFAULT '',
                bot_name TEXT NOT NULL DEFAULT '',
                base_coins INTEGER NOT NULL DEFAULT 0,
                bonus_coins INTEGER NOT NULL DEFAULT 0,
                coins_reward INTEGER NOT NULL DEFAULT 0,
                base_affection REAL NOT NULL DEFAULT 0,
                bonus_affection REAL NOT NULL DEFAULT 0,
                affection_reward REAL NOT NULL DEFAULT 0,
                boost_active INTEGER NOT NULL DEFAULT 0,
                boost_multiplier REAL NOT NULL DEFAULT 1,
                total_coins_after INTEGER NOT NULL DEFAULT 0,
                total_affection_after REAL NOT NULL DEFAULT 0,
                total_days_after INTEGER NOT NULL DEFAULT 0,
                streak_days_after INTEGER NOT NULL DEFAULT 0,
                note TEXT NOT NULL DEFAULT '',
                event_key TEXT NOT NULL DEFAULT '',
                event_label TEXT NOT NULL DEFAULT '',
                greeting TEXT NOT NULL DEFAULT '',
                greeting_source TEXT NOT NULL DEFAULT 'local',
                greeting_attribution TEXT NOT NULL DEFAULT '',
                secondary_note TEXT NOT NULL DEFAULT '',
                template_version TEXT NOT NULL DEFAULT 'default:1',
                theme_id TEXT NOT NULL DEFAULT 'default',
                render_tier TEXT NOT NULL DEFAULT '省流量',
                background_mode TEXT NOT NULL DEFAULT '',
                background_source TEXT NOT NULL DEFAULT '',
                background_illust_id TEXT NOT NULL DEFAULT '',
                background_title TEXT NOT NULL DEFAULT '',
                background_author TEXT NOT NULL DEFAULT '',
                background_quality TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (date_key, user_id),
                FOREIGN KEY (user_id) REFERENCES checkin_users(user_id) ON DELETE CASCADE,
                FOREIGN KEY (theme_id) REFERENCES checkin_themes(theme_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS checkin_global_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                date_value TEXT NOT NULL,
                name TEXT NOT NULL,
                created_by TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(event_type, date_value)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS checkin_achievements (
                user_id TEXT NOT NULL,
                achievement_id TEXT NOT NULL,
                unlocked_at TEXT NOT NULL,
                PRIMARY KEY (user_id, achievement_id),
                FOREIGN KEY (user_id) REFERENCES checkin_users(user_id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS checkin_group_presence (
                date_key TEXT NOT NULL,
                group_id TEXT NOT NULL,
                group_name TEXT NOT NULL DEFAULT '',
                platform TEXT NOT NULL DEFAULT '',
                user_id TEXT NOT NULL,
                username TEXT NOT NULL DEFAULT '',
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                PRIMARY KEY (date_key, group_id, user_id),
                FOREIGN KEY (user_id) REFERENCES checkin_users(user_id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_checkin_group_presence_lookup
            ON checkin_group_presence (group_id, date_key, first_seen_at)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_checkin_records_member_updated
            ON checkin_records (user_id, updated_at)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_checkin_group_presence_member_seen
            ON checkin_group_presence (user_id, last_seen_at)
            """
        )
        # ---- 新功能表（C2 / C1 / B1 / E1 / A2）----
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS checkin_group_subscriptions (
                group_id TEXT PRIMARY KEY,
                group_name TEXT NOT NULL DEFAULT '',
                platform TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                tag TEXT NOT NULL DEFAULT '',
                push_time TEXT NOT NULL DEFAULT '09:00',
                weekdays TEXT NOT NULL DEFAULT '1,2,3,4,5,6,7',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS checkin_user_favorites (
                user_id TEXT NOT NULL,
                illust_id TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                author TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL DEFAULT '',
                thumb_url TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                PRIMARY KEY (user_id, illust_id),
                FOREIGN KEY (user_id) REFERENCES checkin_users(user_id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_checkin_user_favorites_user_created
            ON checkin_user_favorites (user_id, created_at)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS checkin_bonds (
                bond_pair TEXT PRIMARY KEY,
                user1 TEXT NOT NULL,
                user2 TEXT NOT NULL,
                mutual_days INTEGER NOT NULL DEFAULT 0,
                gifts INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                UNIQUE (user1, user2)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS checkin_audit_logs (
                log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                operator TEXT NOT NULL DEFAULT '',
                action TEXT NOT NULL DEFAULT '',
                target TEXT NOT NULL DEFAULT '',
                detail TEXT NOT NULL DEFAULT '',
                ip TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_checkin_audit_logs_created
            ON checkin_audit_logs (created_at)
            """
        )
        # 金币/好感流水（B1 转账、商店、管理调整可追溯）
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS checkin_ledger (
                entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                amount_coins INTEGER NOT NULL DEFAULT 0,
                amount_affection REAL NOT NULL DEFAULT 0,
                memo TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES checkin_users(user_id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_checkin_ledger_user_created
            ON checkin_ledger (user_id, created_at)
            """
        )
        # A2 赛季结算奖励台账（避免同一赛季重复发放）
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS checkin_season_rewards (
                season_key TEXT NOT NULL,
                group_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                rank INTEGER NOT NULL,
                coins INTEGER NOT NULL DEFAULT 0,
                title TEXT NOT NULL DEFAULT '',
                settled_at TEXT NOT NULL,
                PRIMARY KEY (season_key, group_id, user_id)
            )
            """
        )
        # D3 群签到提醒（WebUI 运营页可配置）
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS checkin_group_reminders (
                group_id TEXT PRIMARY KEY,
                group_name TEXT NOT NULL DEFAULT '',
                platform TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                remind_time TEXT NOT NULL DEFAULT '21:00',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

    @staticmethod
    def _sync_builtin_themes(conn: sqlite3.Connection) -> None:
        rows = [
            (
                theme.theme_id,
                theme.code,
                theme.name,
                theme.description,
                theme.version,
                theme.price,
                int(theme.enabled),
                sort_order,
            )
            for sort_order, theme in enumerate(CHECKIN_THEMES.values())
        ]
        conn.executemany(
            """
            INSERT INTO checkin_themes
                (theme_id, code, name, description, version, price, enabled, sort_order)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(theme_id) DO UPDATE SET
                code = excluded.code,
                name = excluded.name,
                description = excluded.description,
                version = excluded.version,
                price = excluded.price,
                enabled = excluded.enabled,
                sort_order = excluded.sort_order
            """,
            rows,
        )
