"""A1 补签卡 / 月卡 — 道具存储层

- 补签卡：消耗一张，把最近一个缺失的历史日期补写入 checkin_records，
  补给少量金币，不破坏 streak / total 计算（视为"恩惠"而非正常签到）。
- 月卡：购买后 30 天有效，期间每日签到金币翻倍（由 record_store._checkin_sync
  通过 monthly_card_active 判断），并在卡面展示专属徽标。

写路径复用 CheckinStore 的 `BEGIN IMMEDIATE` + 全局 asyncio 锁模式。
"""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing
from datetime import date, timedelta

from .models import (
    MAKEUP_CARD_GRANT_COINS,
    MAKEUP_CARD_PRICE,
    MONTHLY_CARD_DAYS,
    MONTHLY_CARD_PRICE,
    ItemPurchaseResult,
    MakeupResult,
)
from .rules import is_monthly_card_active as _is_monthly_card_active


class ItemsStoreMixin:
    # ============ 月卡 ============

    async def purchase_monthly_card(self, *, user_id: str) -> ItemPurchaseResult:
        user_id = str(user_id or "")
        if not user_id:
            raise ValueError("user_id is required")
        async with self._lock:
            return await asyncio.to_thread(self._purchase_monthly_card_sync, user_id)

    @staticmethod
    def monthly_card_active(profile, date_key: str) -> bool:
        """判断某用户在某日期月卡是否生效（供签到奖励调用）。"""
        return _is_monthly_card_active(profile, date_key)

    # ============ 补签卡 ============

    async def purchase_makeup_card(self, *, user_id: str) -> ItemPurchaseResult:
        user_id = str(user_id or "")
        if not user_id:
            raise ValueError("user_id is required")
        async with self._lock:
            return await asyncio.to_thread(self._purchase_makeup_card_sync, user_id)

    async def use_makeup_card(self, *, user_id: str) -> MakeupResult:
        user_id = str(user_id or "")
        if not user_id:
            raise ValueError("user_id is required")
        async with self._lock:
            return await asyncio.to_thread(self._use_makeup_card_sync, user_id, self.today_key())

    async def makeup_card_count(self, user_id: str) -> int:
        profile = await self.get_profile(str(user_id or ""))
        return profile.makeup_cards

    # ============ 同步实现 ============

    def _purchase_monthly_card_sync(self, user_id: str) -> ItemPurchaseResult:
        now = self.now_iso()
        date_key = self.today_key()
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO checkin_users (
                        user_id, coins, affection, total_days, streak_days,
                        last_checkin_date, boost_start_date, boost_until_date,
                        repeat_penalty_date, repeat_penalty_total,
                        created_at, updated_at
                    ) VALUES (?, 0, 0, 0, 0, '', '', '', '', 0, ?, ?)
                    """,
                    (user_id, now, now),
                )
                row = conn.execute(
                    "SELECT * FROM checkin_users WHERE user_id = ?", (user_id,)
                ).fetchone()
                if row is None:
                    raise ValueError("用户初始化失败")
                coins = int(row["coins"] or 0)
                if coins < MONTHLY_CARD_PRICE:
                    return ItemPurchaseResult(
                        False,
                        self._row_to_profile(row),
                        "monthly_card",
                        MONTHLY_CARD_PRICE,
                        f"金币不足，需要 {MONTHLY_CARD_PRICE}，当前只有 {coins}。",
                    )
                today = date.fromisoformat(date_key)
                current_until = _parse_iso(str(row["monthly_card_until"] or ""))
                if current_until is not None and current_until >= today:
                    new_until = current_until + timedelta(days=MONTHLY_CARD_DAYS)
                else:
                    new_until = today + timedelta(days=MONTHLY_CARD_DAYS - 1)
                remaining = coins - MONTHLY_CARD_PRICE
                conn.execute(
                    "UPDATE checkin_users SET coins = ?, monthly_card_until = ?, updated_at = ? WHERE user_id = ?",
                    (remaining, new_until.isoformat(), now, user_id),
                )
                self._insert_ledger_sync(
                    conn, user_id, "shop_monthly", -MONTHLY_CARD_PRICE, 0, "购买月卡", now
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        updated = self._get_or_create_profile_sync(user_id)
        return ItemPurchaseResult(
            True,
            updated,
            "monthly_card",
            MONTHLY_CARD_PRICE,
            f"购买成功，消耗 {MONTHLY_CARD_PRICE} 金币，月卡生效至 {new_until.isoformat()}。",
            monthly_until=new_until.isoformat(),
        )

    def _purchase_makeup_card_sync(self, user_id: str) -> ItemPurchaseResult:
        now = self.now_iso()
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO checkin_users (
                        user_id, coins, affection, total_days, streak_days,
                        last_checkin_date, boost_start_date, boost_until_date,
                        repeat_penalty_date, repeat_penalty_total,
                        created_at, updated_at
                    ) VALUES (?, 0, 0, 0, 0, '', '', '', '', 0, ?, ?)
                    """,
                    (user_id, now, now),
                )
                row = conn.execute(
                    "SELECT * FROM checkin_users WHERE user_id = ?", (user_id,)
                ).fetchone()
                coins = int(row["coins"] or 0)
                if coins < MAKEUP_CARD_PRICE:
                    return ItemPurchaseResult(
                        False,
                        self._row_to_profile(row),
                        "makeup_card",
                        MAKEUP_CARD_PRICE,
                        f"金币不足，需要 {MAKEUP_CARD_PRICE}，当前只有 {coins}。",
                    )
                remaining = coins - MAKEUP_CARD_PRICE
                conn.execute(
                    "UPDATE checkin_users SET coins = ?, makeup_cards = makeup_cards + 1, updated_at = ? WHERE user_id = ?",
                    (remaining, now, user_id),
                )
                self._insert_ledger_sync(
                    conn, user_id, "shop_makeup", -MAKEUP_CARD_PRICE, 0, "购买补签卡", now
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        updated = self._get_or_create_profile_sync(user_id)
        return ItemPurchaseResult(
            True,
            updated,
            "makeup_card",
            MAKEUP_CARD_PRICE,
            f"购买成功，消耗 {MAKEUP_CARD_PRICE} 金币，当前持有 {updated.makeup_cards} 张补签卡。",
            count=updated.makeup_cards,
        )

    def _use_makeup_card_sync(self, user_id: str, date_key: str) -> MakeupResult:
        today = date.fromisoformat(date_key)
        now = self.now_iso()
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM checkin_users WHERE user_id = ?", (user_id,)
                ).fetchone()
                if row is None:
                    raise ValueError("用户不存在，请先签到")
                if int(row["makeup_cards"] or 0) < 1:
                    return MakeupResult(
                        False,
                        self._row_to_profile(row),
                        None,
                        "",
                        "没有补签卡，可到“签到商店”购买。",
                    )
                if str(row["last_checkin_date"] or "") == date_key:
                    return MakeupResult(
                        False,
                        self._row_to_profile(row),
                        None,
                        "",
                        "今天已经签到，无需补签。",
                    )
                # 找到最近一个缺失的历史日期（最多回溯 7 天）
                have = {
                    str(r["date_key"])
                    for r in conn.execute(
                        "SELECT date_key FROM checkin_records WHERE user_id = ?", (user_id,)
                    ).fetchall()
                }
                target: date | None = None
                cursor = today - timedelta(days=1)
                for _ in range(7):
                    if cursor.isoformat() not in have and cursor < today:
                        target = cursor
                        break
                    cursor -= timedelta(days=1)
                if target is None:
                    return MakeupResult(
                        False,
                        self._row_to_profile(row),
                        None,
                        "",
                        "最近 7 天没有需要补签的日期。",
                    )
                target_key = target.isoformat()
                if target_key == str(row["last_checkin_date"] or ""):
                    last = _parse_iso(str(row["last_checkin_date"] or "")) or today
                    target = last + timedelta(days=1)
                    target_key = target.isoformat()
                    if target_key in have or target >= today:
                        return MakeupResult(
                            False,
                            self._row_to_profile(row),
                            None,
                            "",
                            "没有需要补签的日期。",
                        )
                username = str(row["user_id"])
                conn.execute(
                    """
                    INSERT OR IGNORE INTO checkin_records (
                        date_key, user_id, username, bot_name,
                        base_coins, bonus_coins, coins_reward,
                        base_affection, bonus_affection, affection_reward,
                        boost_active, boost_multiplier,
                        total_coins_after, total_affection_after,
                        total_days_after, streak_days_after,
                        note, theme_id, template_version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        target_key,
                        user_id,
                        username,
                        "",
                        MAKEUP_CARD_GRANT_COINS,
                        0,
                        MAKEUP_CARD_GRANT_COINS,
                        0.0,
                        0.0,
                        0.0,
                        0,
                        1.0,
                        int(row["coins"] or 0) + MAKEUP_CARD_GRANT_COINS,
                        float(row["affection"] or 0),
                        int(row["total_days"] or 0),
                        int(row["streak_days"] or 0),
                        "补签",
                        str(row["current_theme_id"] or "default"),
                        "default:1",
                        now,
                        now,
                    ),
                )
                conn.execute(
                    "UPDATE checkin_users SET makeup_cards = makeup_cards - 1, coins = coins + ?, updated_at = ? WHERE user_id = ?",
                    (MAKEUP_CARD_GRANT_COINS, now, user_id),
                )
                self._insert_ledger_sync(
                    conn, user_id, "makeup", MAKEUP_CARD_GRANT_COINS, 0, f"补签 {target_key}", now
                )
                record_row = conn.execute(
                    "SELECT * FROM checkin_records WHERE date_key = ? AND user_id = ?",
                    (target_key, user_id),
                ).fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        updated_profile = self._get_or_create_profile_sync(user_id)
        return MakeupResult(
            True,
            updated_profile,
            self._row_to_record(record_row),
            target_key,
            f"补签成功：{target_key}，获得 {MAKEUP_CARD_GRANT_COINS} 金币。",
        )

    @staticmethod
    def _insert_ledger_sync(
        conn: sqlite3.Connection,
        user_id: str,
        kind: str,
        coins: int,
        affection: float,
        memo: str,
        now: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO checkin_ledger (user_id, kind, amount_coins, amount_affection, memo, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, kind, coins, round(affection, 2), memo, now),
        )


def _parse_iso(value: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None
