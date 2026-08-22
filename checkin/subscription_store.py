"""C2 每日一图群订阅 — 数据访问层

把「每日一图定时推送」的目标群订阅落库 checkin_group_subscriptions：
- 群可订阅推送（默认每日 09:00），记录偏好标签（tag）与投放星期（weekdays）；
- 定时任务按 weekday + push_time 拉取匹配的启用订阅，逐群推送一图。
"""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing

from .models import (
    DAILY_PUSH_DEFAULT_TIME,
    DAILY_PUSH_DEFAULT_WEEKDAYS,
    GroupReminder,
    GroupSubscription,
)

_DAILY_PUSH_TIME_PATTERN_LEN = 5  # "HH:MM"
_REMIND_TIME_DEFAULT = "21:00"


def _parse_weekdays(value: str) -> set[int]:
    result: set[int] = set()
    for token in str(value or "").split(","):
        token = token.strip()
        if not token.isdigit():
            continue
        day = int(token)
        if 1 <= day <= 7:
            result.add(day)
    return result


def _normalize_push_time(value: str) -> str:
    text = str(value or "").strip()
    if len(text) == 4 and ":" not in text:
        try:
            text = f"{text[:2]}:{text[2:]}"
        except Exception:
            pass
    if len(text) != _DAILY_PUSH_TIME_PATTERN_LEN or ":" not in text:
        return DAILY_PUSH_DEFAULT_TIME
    try:
        hour, minute = text.split(":")
        if not (0 <= int(hour) <= 23 and 0 <= int(minute) <= 59):
            return DAILY_PUSH_DEFAULT_TIME
    except (TypeError, ValueError):
        return DAILY_PUSH_DEFAULT_TIME
    return f"{int(hour):02d}:{int(minute):02d}"


def _normalize_weekdays(value: str) -> str:
    parsed = sorted(_parse_weekdays(value))
    return ",".join(str(day) for day in parsed) if parsed else DAILY_PUSH_DEFAULT_WEEKDAYS


def _normalize_remind_time(value: str) -> str:
    """校验/归一化提醒时间（HH:MM），非法值回退到默认 21:00。"""
    text = str(value or "").strip()
    if len(text) != _DAILY_PUSH_TIME_PATTERN_LEN or ":" not in text:
        return _REMIND_TIME_DEFAULT
    try:
        hour, minute = text.split(":")
        if not (0 <= int(hour) <= 23 and 0 <= int(minute) <= 59):
            return _REMIND_TIME_DEFAULT
    except (TypeError, ValueError):
        return _REMIND_TIME_DEFAULT
    return f"{int(hour):02d}:{int(minute):02d}"


class SubscriptionStoreMixin:
    async def subscribe_group(
        self,
        *,
        group_id: str,
        group_name: str = "",
        platform: str = "",
        tag: str = "",
        push_time: str = DAILY_PUSH_DEFAULT_TIME,
        weekdays: str = DAILY_PUSH_DEFAULT_WEEKDAYS,
        enabled: bool = True,
    ) -> GroupSubscription:
        group_id = str(group_id or "").strip()
        if not group_id:
            raise ValueError("group_id is required")
        push_time = _normalize_push_time(push_time)
        weekdays = _normalize_weekdays(weekdays)
        async with self._lock:
            return await asyncio.to_thread(
                self._subscribe_group_sync,
                group_id,
                str(group_name or "").strip(),
                str(platform or "").strip(),
                str(tag or "").strip(),
                push_time,
                weekdays,
                1 if enabled else 0,
            )

    async def update_subscription(
        self,
        *,
        group_id: str,
        tag: str | None = None,
        push_time: str | None = None,
        weekdays: str | None = None,
        enabled: bool | None = None,
    ) -> GroupSubscription | None:
        group_id = str(group_id or "").strip()
        if not group_id:
            raise ValueError("group_id is required")
        if tag is not None:
            tag = str(tag).strip()
        if push_time is not None:
            push_time = _normalize_push_time(push_time)
        if weekdays is not None:
            weekdays = _normalize_weekdays(weekdays)
        async with self._lock:
            return await asyncio.to_thread(
                self._update_subscription_sync, group_id, tag, push_time, weekdays, enabled
            )

    async def unsubscribe_group(self, group_id: str) -> bool:
        group_id = str(group_id or "").strip()
        if not group_id:
            return False
        async with self._lock:
            return await asyncio.to_thread(self._unsubscribe_group_sync, group_id)

    async def get_subscription(self, group_id: str) -> GroupSubscription | None:
        group_id = str(group_id or "").strip()
        if not group_id:
            return None
        async with self._lock:
            return await asyncio.to_thread(self._get_subscription_sync, group_id)

    async def is_group_subscribed(self, group_id: str) -> bool:
        subscription = await self.get_subscription(group_id)
        return subscription is not None and subscription.enabled

    async def list_subscriptions(self, *, limit: int = 50, offset: int = 0) -> dict[str, object]:
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        async with self._lock:
            return await asyncio.to_thread(self._list_subscriptions_sync, limit, offset)

    async def get_subscriptions_for_push(
        self,
        *,
        weekday: int,
        current_time: str,
        include_disabled: bool = False,
    ) -> list[GroupSubscription]:
        """供定时任务使用：返回 push_time 命中且（默认仅启用）的订阅群。"""
        weekday = int(weekday)
        current_time = _normalize_push_time(current_time)
        async with self._lock:
            return await asyncio.to_thread(
                self._get_subscriptions_for_push_sync,
                weekday,
                current_time,
                1 if not include_disabled else 0,
            )

    async def upsert_group_subscription(
        self,
        *,
        group_id: str,
        group_name: str = "",
        platform: str = "",
        enabled: bool = True,
        tag: str = "",
        push_time: str = DAILY_PUSH_DEFAULT_TIME,
        weekdays: str = DAILY_PUSH_DEFAULT_WEEKDAYS,
    ) -> GroupSubscription:
        group_id = str(group_id or "").strip()
        if not group_id:
            raise ValueError("group_id is required")
        push_time = _normalize_push_time(push_time)
        weekdays = _normalize_weekdays(weekdays)
        async with self._lock:
            return await asyncio.to_thread(
                self._upsert_group_subscription_sync,
                group_id,
                str(group_name or "").strip(),
                str(platform or "").strip(),
                1 if enabled else 0,
                str(tag or "").strip(),
                push_time,
                weekdays,
            )

    async def get_group_subscription(self, group_id: str) -> GroupSubscription | None:
        group_id = str(group_id or "").strip()
        if not group_id:
            return None
        async with self._lock:
            return await asyncio.to_thread(self._get_subscription_sync, group_id)

    async def list_group_subscriptions(
        self, *, enabled_only: bool = False
    ) -> list[GroupSubscription]:
        async with self._lock:
            return await asyncio.to_thread(
                self._list_group_subscriptions_sync, 1 if enabled_only else 0
            )

    async def set_subscription_enabled(
        self, group_id: str, enabled: bool
    ) -> GroupSubscription | None:
        group_id = str(group_id or "").strip()
        if not group_id:
            raise ValueError("group_id is required")
        async with self._lock:
            return await asyncio.to_thread(
                self._set_subscription_field_sync, group_id, "enabled", 1 if enabled else 0
            )

    async def set_subscription_tag(self, group_id: str, tag: str) -> GroupSubscription | None:
        group_id = str(group_id or "").strip()
        if not group_id:
            raise ValueError("group_id is required")
        async with self._lock:
            return await asyncio.to_thread(
                self._set_subscription_field_sync, group_id, "tag", str(tag or "").strip()
            )

    async def set_subscription_push_time(
        self, group_id: str, push_time: str
    ) -> GroupSubscription | None:
        group_id = str(group_id or "").strip()
        if not group_id:
            raise ValueError("group_id is required")
        async with self._lock:
            return await asyncio.to_thread(
                self._set_subscription_field_sync,
                group_id,
                "push_time",
                _normalize_push_time(push_time),
            )

    async def upsert_group_reminder(
        self,
        *,
        group_id: str,
        group_name: str = "",
        platform: str = "",
        enabled: bool = True,
        remind_time: str = _REMIND_TIME_DEFAULT,
    ) -> GroupReminder:
        group_id = str(group_id or "").strip()
        if not group_id:
            raise ValueError("group_id is required")
        remind_time = _normalize_remind_time(remind_time)
        async with self._lock:
            return await asyncio.to_thread(
                self._upsert_group_reminder_sync,
                group_id,
                str(group_name or "").strip(),
                str(platform or "").strip(),
                1 if enabled else 0,
                remind_time,
            )

    async def get_group_reminder(self, group_id: str) -> GroupReminder | None:
        group_id = str(group_id or "").strip()
        if not group_id:
            return None
        async with self._lock:
            return await asyncio.to_thread(self._get_group_reminder_sync, group_id)

    async def list_group_reminders(self, *, enabled_only: bool = False) -> list[GroupReminder]:
        async with self._lock:
            return await asyncio.to_thread(
                self._list_group_reminders_sync, 1 if enabled_only else 0
            )

    # ============ 同步实现 ============

    def _subscribe_group_sync(
        self,
        group_id,
        group_name,
        platform,
        tag,
        push_time,
        weekdays,
        enabled,
    ) -> GroupSubscription:
        now = self.now_iso()
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO checkin_group_subscriptions
                    (group_id, group_name, platform, enabled, tag, push_time,
                     weekdays, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(group_id) DO UPDATE SET
                    group_name = excluded.group_name,
                    platform = excluded.platform,
                    enabled = excluded.enabled,
                    tag = excluded.tag,
                    push_time = excluded.push_time,
                    weekdays = excluded.weekdays,
                    updated_at = excluded.updated_at
                """,
                (group_id, group_name, platform, enabled, tag, push_time, weekdays, now, now),
            )
            conn.commit()
        row = conn.execute(
            "SELECT * FROM checkin_group_subscriptions WHERE group_id = ?", (group_id,)
        ).fetchone()
        return self._row_to_subscription(row)

    def _update_subscription_sync(
        self, group_id, tag, push_time, weekdays, enabled
    ) -> GroupSubscription | None:
        with closing(self._connect()) as conn:
            if tag is None and push_time is None and weekdays is None and enabled is None:
                row = conn.execute(
                    "SELECT * FROM checkin_group_subscriptions WHERE group_id = ?",
                    (group_id,),
                ).fetchone()
                return self._row_to_subscription(row) if row is not None else None
            assignments: list[str] = []
            params: list[object] = []
            if tag is not None:
                assignments.append("tag = ?")
                params.append(tag)
            if push_time is not None:
                assignments.append("push_time = ?")
                params.append(push_time)
            if weekdays is not None:
                assignments.append("weekdays = ?")
                params.append(weekdays)
            if enabled is not None:
                assignments.append("enabled = ?")
                params.append(1 if enabled else 0)
            assignments.append("updated_at = ?")
            params.append(self.now_iso())
            params.append(group_id)
            updated = conn.execute(
                f"""
                UPDATE checkin_group_subscriptions
                SET {", ".join(assignments)}
                WHERE group_id = ?
                """,
                params,
            ).rowcount
            conn.commit()
            if not updated:
                return None
            row = conn.execute(
                "SELECT * FROM checkin_group_subscriptions WHERE group_id = ?", (group_id,)
            ).fetchone()
        return self._row_to_subscription(row)

    def _unsubscribe_group_sync(self, group_id: str) -> bool:
        with closing(self._connect()) as conn:
            changed = conn.execute(
                "DELETE FROM checkin_group_subscriptions WHERE group_id = ?", (group_id,)
            ).rowcount
            conn.commit()
        return bool(changed)

    def _get_subscription_sync(self, group_id: str) -> GroupSubscription | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM checkin_group_subscriptions WHERE group_id = ?", (group_id,)
            ).fetchone()
        return self._row_to_subscription(row) if row is not None else None

    def _list_subscriptions_sync(self, limit: int, offset: int) -> dict[str, object]:
        with closing(self._connect()) as conn:
            total_row = conn.execute(
                "SELECT COUNT(*) AS count FROM checkin_group_subscriptions"
            ).fetchone()
            rows = conn.execute(
                """
                SELECT * FROM checkin_group_subscriptions
                ORDER BY updated_at DESC, group_id
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
        return {
            "total": int(total_row["count"] or 0),
            "subscriptions": [self._row_to_subscription(row) for row in rows],
        }

    def _get_subscriptions_for_push_sync(
        self, weekday: int, current_time: str, skip_disabled: int
    ) -> list[GroupSubscription]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT * FROM checkin_group_subscriptions
                WHERE push_time = ? AND (? = 0 OR enabled = 1)
                ORDER BY group_id
                """,
                (current_time, skip_disabled),
            ).fetchall()
        subscriptions: list[GroupSubscription] = []
        for row in rows:
            subscription = self._row_to_subscription(row)
            if weekday in _parse_weekdays(subscription.weekdays):
                subscriptions.append(subscription)
        return subscriptions

    def _upsert_group_subscription_sync(
        self, group_id, group_name, platform, enabled, tag, push_time, weekdays
    ) -> GroupSubscription:
        now = self.now_iso()
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """
                    INSERT INTO checkin_group_subscriptions
                        (group_id, group_name, platform, enabled, tag, push_time,
                         weekdays, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(group_id) DO UPDATE SET
                        group_name = CASE WHEN excluded.group_name != ''
                            THEN excluded.group_name
                            ELSE checkin_group_subscriptions.group_name END,
                        platform = CASE WHEN excluded.platform != ''
                            THEN excluded.platform
                            ELSE checkin_group_subscriptions.platform END,
                        enabled = excluded.enabled,
                        tag = excluded.tag,
                        push_time = excluded.push_time,
                        weekdays = excluded.weekdays,
                        updated_at = excluded.updated_at
                    """,
                    (group_id, group_name, platform, enabled, tag, push_time, weekdays, now, now),
                )
                row = conn.execute(
                    "SELECT * FROM checkin_group_subscriptions WHERE group_id = ?", (group_id,)
                ).fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return self._row_to_subscription(row)

    def _list_group_subscriptions_sync(self, enabled_only: int) -> list[GroupSubscription]:
        where = "WHERE enabled = 1" if enabled_only else ""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM checkin_group_subscriptions
                {where}
                ORDER BY group_id
                """
            ).fetchall()
        return [self._row_to_subscription(row) for row in rows]

    def _set_subscription_field_sync(
        self, group_id: str, field: str, value: object
    ) -> GroupSubscription | None:
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                updated = conn.execute(
                    f"""
                    UPDATE checkin_group_subscriptions
                    SET {field} = ?, updated_at = ?
                    WHERE group_id = ?
                    """,
                    (value, self.now_iso(), group_id),
                ).rowcount
                if not updated:
                    conn.rollback()
                    return None
                row = conn.execute(
                    "SELECT * FROM checkin_group_subscriptions WHERE group_id = ?", (group_id,)
                ).fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return self._row_to_subscription(row)

    def _upsert_group_reminder_sync(
        self, group_id, group_name, platform, enabled, remind_time
    ) -> GroupReminder:
        now = self.now_iso()
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """
                    INSERT INTO checkin_group_reminders
                        (group_id, group_name, platform, enabled, remind_time,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(group_id) DO UPDATE SET
                        group_name = CASE WHEN excluded.group_name != ''
                            THEN excluded.group_name
                            ELSE checkin_group_reminders.group_name END,
                        platform = CASE WHEN excluded.platform != ''
                            THEN excluded.platform
                            ELSE checkin_group_reminders.platform END,
                        enabled = excluded.enabled,
                        remind_time = excluded.remind_time,
                        updated_at = excluded.updated_at
                    """,
                    (group_id, group_name, platform, enabled, remind_time, now, now),
                )
                row = conn.execute(
                    "SELECT * FROM checkin_group_reminders WHERE group_id = ?", (group_id,)
                ).fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return self._row_to_reminder(row)

    def _get_group_reminder_sync(self, group_id: str) -> GroupReminder | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM checkin_group_reminders WHERE group_id = ?", (group_id,)
            ).fetchone()
        return self._row_to_reminder(row) if row is not None else None

    def _list_group_reminders_sync(self, enabled_only: int) -> list[GroupReminder]:
        where = "WHERE enabled = 1" if enabled_only else ""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM checkin_group_reminders
                {where}
                ORDER BY group_id
                """
            ).fetchall()
        return [self._row_to_reminder(row) for row in rows]

    @staticmethod
    def _row_to_reminder(row: sqlite3.Row) -> GroupReminder:
        return GroupReminder(
            group_id=str(row["group_id"]),
            group_name=str(row["group_name"] or ""),
            platform=str(row["platform"] or ""),
            enabled=bool(row["enabled"]),
            remind_time=str(row["remind_time"] or _REMIND_TIME_DEFAULT),
            created_at=str(row["created_at"] or ""),
            updated_at=str(row["updated_at"] or ""),
        )

    @staticmethod
    def _row_to_subscription(row: sqlite3.Row) -> GroupSubscription:
        return GroupSubscription(
            group_id=str(row["group_id"]),
            group_name=str(row["group_name"] or ""),
            platform=str(row["platform"] or ""),
            enabled=bool(row["enabled"]),
            tag=str(row["tag"] or ""),
            push_time=str(row["push_time"] or DAILY_PUSH_DEFAULT_TIME),
            weekdays=str(row["weekdays"] or DAILY_PUSH_DEFAULT_WEEKDAYS),
            created_at=str(row["created_at"] or ""),
            updated_at=str(row["updated_at"] or ""),
        )
