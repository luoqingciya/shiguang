"""A2 赛季排行 + 结算奖励 / A3 打卡日历 / A4 幸运日

赛季为固定 30 天滚动窗口，由 epoch 推导赛季起点（season_key），
与现有 ranking_store 完全兼容；结算经 scheduler 定时触发，用
checkin_season_rewards 台账防重复发放。
"""

from __future__ import annotations

import asyncio
from contextlib import closing
from datetime import date, timedelta

from .models import (
    SEASON_REWARD_TOP1,
    SEASON_REWARD_TOP3,
    SEASON_REWARD_TOP10,
    SEASON_WINDOW_DAYS,
)

_EPOCH = date(2020, 1, 1)
_SEASON_BONUS_COINS = {1: SEASON_REWARD_TOP1, 3: SEASON_REWARD_TOP3, 10: SEASON_REWARD_TOP10}
SEASON_CHECKIN_TOTAL = _SEASON_BONUS_COINS
_LUCKY_TAILS = {"7", "8", "9"}


def season_bounds(today: date | None = None) -> tuple[date, date, str]:
    today = today or date.today()
    days = (today - _EPOCH).days
    window_index = days // SEASON_WINDOW_DAYS
    start = _EPOCH + timedelta(days=window_index * SEASON_WINDOW_DAYS)
    end = start + timedelta(days=SEASON_WINDOW_DAYS - 1)
    return start, end, start.isoformat()


class SeasonStoreMixin:
    async def get_season_ranking(self, *, group_id: str, limit: int = 10) -> dict[str, object]:
        start, end, key = season_bounds()
        limit = max(1, min(int(limit), 100))
        return await asyncio.to_thread(
            self._get_season_ranking_sync, group_id, start, end, key, limit
        )

    async def settle_season(self, group_id: str) -> dict[str, object]:
        start, end, key = season_bounds()
        return await asyncio.to_thread(
            self._settle_season_sync, group_id, start.isoformat(), end.isoformat(), key
        )

    async def get_month_calendar(self, *, user_id: str, year: int, month: int) -> dict[str, object]:
        try:
            start = date(year, month, 1)
        except ValueError as exc:
            raise ValueError("年月无效") from exc
        if month == 12:
            next_month = date(year + 1, 1, 1)
        else:
            next_month = date(year, month + 1, 1)
        end = next_month - timedelta(days=1)
        return await asyncio.to_thread(
            self._get_month_calendar_sync, str(user_id or ""), start, end
        )

    @staticmethod
    def is_lucky_day(user_id: str, group_id: str, date_key: str) -> bool:
        """A4 幸运日：群号或日期尾号命中幸运尾号集合。"""
        tail_source = f"{group_id}{date_key.replace('-', '')[-2:]}"
        return tail_source.endswith(tuple(_LUCKY_TAILS))

    async def award_lucky_bonus(
        self, *, user_id: str, date_key: str, coins: int, note: str = ""
    ) -> int:
        """A4 幸运日加成：加金币、写流水、同步当日记录快照，返回新金币余额。"""
        user_id = str(user_id or "")
        if not user_id or int(coins) <= 0:
            return 0
        return await asyncio.to_thread(
            self._award_lucky_bonus_sync,
            user_id,
            str(date_key or ""),
            int(coins),
            str(note or ""),
        )

    def _award_lucky_bonus_sync(self, user_id: str, date_key: str, coins: int, note: str) -> int:
        now = self.now_iso()
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT coins FROM checkin_users WHERE user_id = ?", (user_id,)
                ).fetchone()
                if row is None:
                    conn.rollback()
                    return 0
                new_total = int(row["coins"] or 0) + coins
                conn.execute(
                    "UPDATE checkin_users SET coins = ?, updated_at = ? WHERE user_id = ?",
                    (new_total, now, user_id),
                )
                conn.execute(
                    "INSERT INTO checkin_ledger (user_id, kind, amount_coins, amount_affection, memo, created_at) VALUES (?, 'lucky', ?, 0, ?, ?)",
                    (user_id, coins, note or "幸运日加成", now),
                )
                if date_key:
                    rec = conn.execute(
                        "SELECT total_coins_after, secondary_note FROM checkin_records "
                        "WHERE user_id = ? AND date_key = ?",
                        (user_id, date_key),
                    ).fetchone()
                    if rec is not None:
                        new_total_after = int(rec["total_coins_after"] or 0) + coins
                        old_note = str(rec["secondary_note"] or "")
                        new_note = (
                            f"{old_note} · {note}" if old_note and note else (note or old_note)
                        )
                        conn.execute(
                            "UPDATE checkin_records SET total_coins_after = ?, secondary_note = ?, updated_at = ? "
                            "WHERE user_id = ? AND date_key = ?",
                            (new_total_after, new_note, now, user_id, date_key),
                        )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return new_total

    # ============ 同步实现 ============

    def _get_season_ranking_sync(self, group_id, start, end, key, limit) -> dict[str, object]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT user_id,
                       COALESCE(NULLIF(MAX(username), ''), user_id) AS username,
                       COUNT(DISTINCT date_key) AS days,
                       MIN(first_seen_at) AS first_seen_at
                FROM checkin_group_presence
                WHERE group_id = ? AND date_key BETWEEN ? AND ?
                GROUP BY user_id
                ORDER BY days DESC, first_seen_at ASC, user_id
                """,
                (group_id, start.isoformat(), end.isoformat()),
            ).fetchall()
        entries = [
            {
                "user_id": str(r["user_id"]),
                "username": str(r["username"] or r["user_id"]),
                "value": int(r["days"] or 0),
                "rank": idx,
            }
            for idx, r in enumerate(rows[:limit], 1)
        ]
        return {
            "group_id": group_id,
            "type": "season",
            "season_key": key,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "total": len(rows),
            "entries": entries,
        }

    def _settle_season_sync(self, group_id, start, end, key) -> dict[str, object]:
        now = self.now_iso()
        ranking = self._get_season_ranking_sync(
            group_id, date.fromisoformat(start), date.fromisoformat(end), key, 10
        )
        complete = ranking["entries"]
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = {
                    str(r["user_id"])
                    for r in conn.execute(
                        "SELECT user_id FROM checkin_season_rewards WHERE season_key = ? AND group_id = ?",
                        (key, group_id),
                    ).fetchall()
                }
                if existing:
                    conn.rollback()
                    return {
                        "season_key": key,
                        "group_id": group_id,
                        "already_settled": True,
                        "payouts": [],
                    }
                payouts: list[dict[str, object]] = []
                for entry in complete:
                    coins = _season_coins_for_rank(int(entry["rank"]))
                    if coins <= 0:
                        continue
                    user_id = str(entry["user_id"])
                    conn.execute(
                        "UPDATE checkin_users SET coins = coins + ?, updated_at = ? WHERE user_id = ?",
                        (coins, now, user_id),
                    )
                    conn.execute(
                        """
                        INSERT INTO checkin_season_rewards
                        (season_key, group_id, user_id, rank, coins, title, settled_at)
                        VALUES (?, ?, ?, ?, ?, '', ?)
                        """,
                        (key, group_id, user_id, int(entry["rank"]), coins, now),
                    )
                    conn.execute(
                        "INSERT INTO checkin_ledger (user_id, kind, amount_coins, amount_affection, memo, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            user_id,
                            "season_reward",
                            coins,
                            0,
                            f"{key} 赛季第 {int(entry['rank'])} 名",
                            now,
                        ),
                    )
                    payouts.append({"user_id": user_id, "rank": int(entry["rank"]), "coins": coins})
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return {
            "season_key": key,
            "group_id": group_id,
            "already_settled": False,
            "payouts": payouts,
        }

    def _get_month_calendar_sync(self, user_id, start, end) -> dict[str, object]:
        today = self.today_key()
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT date_key
                FROM checkin_records
                WHERE user_id = ? AND date_key BETWEEN ? AND ?
                """,
                (user_id, start.isoformat(), end.isoformat()),
            ).fetchall()
        signed = {str(r["date_key"]) for r in rows}
        days = []
        for offset in range((end - start).days + 1):
            day = start + timedelta(days=offset)
            key = day.isoformat()
            days.append(
                {
                    "day": day.day,
                    "date": key,
                    "signed": key in signed,
                    "is_today": key == today,
                }
            )
        return {
            "year": start.year,
            "month": start.month,
            "total_days": (end - start).days + 1,
            "signed_days": len(signed),
            "streak_days": _consecutive_tail(sorted(signed), today) if signed else 0,
            "days": days,
        }


def _season_coins_for_rank(rank: int) -> int:
    for threshold, coins in sorted(SEASON_CHECKIN_TOTAL.items()):
        if rank <= threshold:
            return coins
    return 0


def _consecutive_tail(sorted_days: list[str], today_key: str) -> int:
    today = date.fromisoformat(today_key)
    cursor = today if today_key in sorted_days else today - timedelta(days=1)
    count = 0
    while cursor.isoformat() in sorted_days:
        count += 1
        cursor -= timedelta(days=1)
    return count
