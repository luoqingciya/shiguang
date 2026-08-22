"""D3 WebUI 统计 — 数据访问层

聚合签到全局指标（活跃用户、签到率、规模），供管理端「运营」页展示：
- dau_7：最近 7 天（含今天）活跃签到用户数；
- week/month_checkin_rate：近 7 / 30 天活跃用户 ÷ 历史去重用户（百分比）；
- total_users / total_records / total_groups：用户、签到记录、群规模。
"""

from __future__ import annotations

import asyncio
from contextlib import closing
from datetime import date, timedelta


class StatsStoreMixin:
    async def get_checkin_stats(self) -> dict[str, object]:
        return await asyncio.to_thread(self._get_checkin_stats_sync)

    def _get_checkin_stats_sync(self) -> dict[str, object]:
        today = self.today_key()
        week_start = (date.fromisoformat(today) - timedelta(days=6)).isoformat()
        month_start = (date.fromisoformat(today) - timedelta(days=29)).isoformat()
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(DISTINCT CASE WHEN date_key >= ? THEN user_id END) AS dau_7,
                    COUNT(DISTINCT CASE WHEN date_key >= ? THEN user_id END) AS week_users,
                    COUNT(DISTINCT CASE WHEN date_key >= ? THEN user_id END) AS month_users,
                    (SELECT COUNT(DISTINCT user_id) FROM checkin_records) AS ever_users,
                    (SELECT COUNT(*) FROM checkin_users) AS total_users,
                    (SELECT COUNT(*) FROM checkin_records) AS total_records,
                    (SELECT COUNT(DISTINCT group_id) FROM checkin_group_presence) AS total_groups
                FROM checkin_records
                """,
                (week_start, week_start, month_start),
            ).fetchone()
        ever_users = int(row["ever_users"] or 0)
        week_users = int(row["week_users"] or 0)
        month_users = int(row["month_users"] or 0)
        week_checkin_rate = round(week_users / ever_users * 100, 2) if ever_users else 0.0
        month_checkin_rate = round(month_users / ever_users * 100, 2) if ever_users else 0.0
        return {
            "dau_7": int(row["dau_7"] or 0),
            "week_checkin_rate": week_checkin_rate,
            "month_checkin_rate": month_checkin_rate,
            "total_users": int(row["total_users"] or 0),
            "total_records": int(row["total_records"] or 0),
            "total_groups": int(row["total_groups"] or 0),
        }
