"""B1 签到互助 — 送花 / 金币转账 / 双人连签羁绊

- 送花：消耗 GIFT_COST 金币，给对方 +GIFT_AFFECTION_STEP 好感（单目标每日上限
  GIFT_MAX_AFFECTION），双方同为同群成员。
- 金币转账：Sender 扣减、Target 增加，`BEGIN IMMEDIATE` 双更新，避免竞态。
- 羁绊：统计两位用户在同一个群、同一天共同签到的天数（含连续），
  达到阈值后可领取一次金币奖励。
"""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing
from datetime import date, timedelta

from .models import (
    BOND_REWARD_COINS,
    BOND_REWARD_TITLE,
    GIFT_AFFECTION_STEP,
    GIFT_COST,
    GIFT_MAX_AFFECTION,
    GIFT_MAX_PER_DAY,
    GiftResult,
)

_BOND_THRESHOLD_DAYS = 3
_BOND_SEARCH_DAYS = 365


class SocialStoreMixin:
    async def send_flower(self, *, user_id: str, target_id: str) -> GiftResult:
        user_id, target_id = str(user_id or ""), str(target_id or "")
        if not user_id or not target_id:
            raise ValueError("sender and target are required")
        if user_id == target_id:
            raise ValueError("不能给自己送花")
        async with self._lock:
            return await asyncio.to_thread(self._send_flower_sync, user_id, target_id)

    async def transfer_coins(self, *, user_id: str, target_id: str, amount: int) -> GiftResult:
        user_id, target_id = str(user_id or ""), str(target_id or "")
        if isinstance(amount, bool) or not isinstance(amount, int):
            raise ValueError("转账金额必须是整数")
        if not user_id or not target_id:
            raise ValueError("转账双方必须指定")
        if user_id == target_id:
            raise ValueError("不能给自己转账")
        if not 1 <= amount <= 100_000:
            raise ValueError("转账金额必须在 1 至 100000 之间")
        async with self._lock:
            return await asyncio.to_thread(self._transfer_coins_sync, user_id, target_id, amount)

    async def get_mutual_days(self, user1: str, user2: str) -> int:
        user1, user2 = str(user1 or ""), str(user2 or "")
        if not user1 or not user2 or user1 == user2:
            return 0
        async with self._lock:
            return await asyncio.to_thread(self._get_mutual_days_sync, user1, user2)

    async def claim_bond_reward(self, user1: str, user2: str) -> str:
        user1, user2 = str(user1 or ""), str(user2 or "")
        if not user1 or not user2 or user1 == user2:
            raise ValueError("必须与另一位用户建立羁绊")
        async with self._lock:
            return await asyncio.to_thread(self._claim_bond_reward_sync, user1, user2)

    # ============ 同步实现 ============

    def _ensure_user_sync(self, conn: sqlite3.Connection, user_id: str, now: str) -> None:
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

    def _send_flower_sync(self, user_id: str, target_id: str) -> GiftResult:
        now = self.now_iso()
        date_key = self.today_key()
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._ensure_user_sync(conn, user_id, now)
                self._ensure_user_sync(conn, target_id, now)
                rows = {
                    r["user_id"]: r
                    for r in conn.execute(
                        "SELECT * FROM checkin_users WHERE user_id IN (?, ?)",
                        (user_id, target_id),
                    ).fetchall()
                }
                sender = self._row_to_profile(rows[user_id])
                target = self._row_to_profile(rows[target_id])
                if sender.coins < GIFT_COST:
                    return GiftResult(
                        False,
                        sender,
                        target,
                        0,
                        0.0,
                        f"金币不足，送花需要 {GIFT_COST} 金币。",
                    )
                # 每日送花次数上限
                fields = {
                    str(r["entry_id"]): r
                    for r in conn.execute(
                        "SELECT entry_id, kind, memo FROM checkin_ledger "
                        "WHERE user_id = ? AND kind = 'flower' AND substr(created_at, 1, 10) = ?",
                        (user_id, date_key),
                    ).fetchall()
                }
                if len(fields) >= GIFT_MAX_PER_DAY:
                    return GiftResult(
                        False,
                        sender,
                        target,
                        0,
                        0.0,
                        f"今天最多送花 {GIFT_MAX_PER_DAY} 次。",
                    )
                target_affection_today = sum(
                    float(f.get("amount_affection") or 0)
                    for f in conn.execute(
                        "SELECT amount_affection FROM checkin_ledger "
                        "WHERE user_id = ? AND memo LIKE ? AND substr(created_at, 1, 10) = ?",
                        (target_id, "收到赠花%", date_key),
                    ).fetchall()
                )
                if target_affection_today + GIFT_AFFECTION_STEP > GIFT_MAX_AFFECTION:
                    return GiftResult(
                        False,
                        sender,
                        target,
                        0,
                        0.0,
                        f"对方今天收到的好感已达到上限（{GIFT_MAX_AFFECTION:g}）。",
                    )
                gain = round(
                    min(GIFT_AFFECTION_STEP, GIFT_MAX_AFFECTION - target_affection_today), 2
                )
                remaining = sender.coins - GIFT_COST
                new_target_aff = round(target.affection + gain, 2)
                conn.execute(
                    "UPDATE checkin_users SET coins = ?, updated_at = ? WHERE user_id = ?",
                    (remaining, now, user_id),
                )
                conn.execute(
                    "UPDATE checkin_users SET affection = ?, updated_at = ? WHERE user_id = ?",
                    (new_target_aff, now, target_id),
                )
                conn.execute(
                    "INSERT INTO checkin_ledger (user_id, kind, amount_coins, amount_affection, memo, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (user_id, "flower", -GIFT_COST, 0, f"赠花给 {target_id}", now),
                )
                conn.execute(
                    "INSERT INTO checkin_ledger (user_id, kind, amount_coins, amount_affection, memo, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (target_id, "flower", 0, gain, f"收到赠花来自 {user_id}", now),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        new_sender = self._get_or_create_profile_sync(user_id)
        new_target = self._get_or_create_profile_sync(target_id)
        return GiftResult(
            True,
            new_sender,
            new_target,
            GIFT_COST,
            gain,
            f"已为 {target_id} 送上一朵花，好感 +{gain:g}。",
        )

    def _transfer_coins_sync(self, user_id: str, target_id: str, amount: int) -> GiftResult:
        now = self.now_iso()
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._ensure_user_sync(conn, user_id, now)
                self._ensure_user_sync(conn, target_id, now)
                rows = {
                    r["user_id"]: r
                    for r in conn.execute(
                        "SELECT * FROM checkin_users WHERE user_id IN (?, ?)",
                        (user_id, target_id),
                    ).fetchall()
                }
                sender = self._row_to_profile(rows[user_id])
                target = self._row_to_profile(rows[target_id])
                if sender.coins < amount:
                    return GiftResult(
                        False,
                        sender,
                        target,
                        amount,
                        0.0,
                        f"金币不足，需要 {amount}，当前只有 {sender.coins}。",
                    )
                conn.execute(
                    "UPDATE checkin_users SET coins = coins - ?, updated_at = ? WHERE user_id = ?",
                    (amount, now, user_id),
                )
                conn.execute(
                    "UPDATE checkin_users SET coins = coins + ?, updated_at = ? WHERE user_id = ?",
                    (amount, now, target_id),
                )
                conn.execute(
                    "INSERT INTO checkin_ledger (user_id, kind, amount_coins, amount_affection, memo, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (user_id, "transfer", -amount, 0, f"转账给 {target_id}", now),
                )
                conn.execute(
                    "INSERT INTO checkin_ledger (user_id, kind, amount_coins, amount_affection, memo, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (target_id, "transfer", amount, 0, f"收到转账来自 {user_id}", now),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return GiftResult(
            True,
            self._get_or_create_profile_sync(user_id),
            self._get_or_create_profile_sync(target_id),
            amount,
            0.0,
            f"已向 {target_id} 转账 {amount} 金币。",
        )

    def _get_mutual_days_sync(self, user1: str, user2: str) -> int:
        """统计两用户在同一群、同一天共同签到（出席）的天数。"""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT group_id, date_key
                FROM checkin_group_presence
                WHERE user_id = ?
                INTERSECT
                SELECT group_id, date_key
                FROM checkin_group_presence
                WHERE user_id = ?
                """,
                (user1, user2),
            ).fetchall()
        dates = sorted({str(row["date_key"]) for row in rows})
        if not dates:
            return 0
        first = date.fromisoformat(dates[-1]) - timedelta(days=_BOND_SEARCH_DAYS)
        counts: dict[date, int] = {}
        for day in dates:
            d = date.fromisoformat(day)
            if d >= first:
                counts[d] = counts.get(d, 0) + 1
        # 连续天数 = 以最后一个共同日倒推的最长连续区间
        ordered = sorted(counts)
        if not ordered:
            return 0
        streak = 1
        for i in range(len(ordered) - 2, -1, -1):
            if (ordered[i + 1] - ordered[i]).days == 1:
                streak += 1
            else:
                break
        return streak

    def _claim_bond_reward_sync(self, user1: str, user2: str) -> str:
        pair = tuple(sorted((user1, user2)))
        key = f"{pair[0]}:{pair[1]}"
        now = self.now_iso()
        mutual = self._get_mutual_days_sync(user1, user2)
        if mutual < _BOND_THRESHOLD_DAYS:
            return f"羁绊需要两位好友连续共同签到 {_BOND_THRESHOLD_DAYS} 天，当前只有 {mutual} 天。"
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT 1 FROM checkin_bonds WHERE bond_pair = ?",
                    (key,),
                ).fetchone()
                if row is not None:
                    return f"羁绊奖励已领取，当前共同签到 {mutual} 天。"
                conn.execute(
                    "INSERT INTO checkin_bonds (bond_pair, user1, user2, mutual_days, gifts, updated_at) VALUES (?, ?, ?, ?, 0, ?)",
                    (key, pair[0], pair[1], mutual, now),
                )
                conn.execute(
                    "UPDATE checkin_users SET coins = coins + ? WHERE user_id IN (?, ?)",
                    (BOND_REWARD_COINS, pair[0], pair[1]),
                )
                conn.execute(
                    "INSERT INTO checkin_ledger (user_id, kind, amount_coins, amount_affection, memo, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        pair[0],
                        "bond_reward",
                        BOND_REWARD_COINS,
                        0,
                        f"解锁「{BOND_REWARD_TITLE}」",
                        now,
                    ),
                )
                conn.execute(
                    "INSERT INTO checkin_ledger (user_id, kind, amount_coins, amount_affection, memo, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        pair[1],
                        "bond_reward",
                        BOND_REWARD_COINS,
                        0,
                        f"解锁「{BOND_REWARD_TITLE}」",
                        now,
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                conn.rollback()
                return "羁绊奖励已领取。"
            except Exception:
                conn.rollback()
                raise
        return f"羁绊 {mutual} 天达成！双向各奖励 {BOND_REWARD_COINS} 金币，已解锁「{BOND_REWARD_TITLE}」称号。"
