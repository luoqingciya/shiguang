"""C1 用户收藏 / E1 管理操作审计

- 收藏：把插画去重后的元数据快照存入 checkin_user_favorites，
  支持分页与按画师筛选；`/画廊` 与 `回复收藏` 复用。
- 审计：管理写操作（金币调整、事件增删、黑名单变更、导入覆盖）
  记录到 checkin_audit_logs，供 WebUI 查询追溯。
"""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing

from .models import AuditLog, FavoriteItem


class FavoritesStoreMixin:
    async def add_favorite(
        self,
        *,
        user_id: str,
        illust_id: str,
        title: str = "",
        author: str = "",
        source: str = "",
        url: str = "",
        thumb_url: str = "",
    ) -> bool:
        user_id, illust_id = str(user_id or ""), str(illust_id or "")
        if not user_id or not illust_id:
            raise ValueError("user_id and illust_id are required")
        if len(illust_id) > 64:
            raise ValueError("illust_id 过长")
        async with self._lock:
            return await asyncio.to_thread(
                self._add_favorite_sync,
                user_id,
                illust_id,
                str(title or ""),
                str(author or ""),
                str(source or ""),
                str(url or ""),
                str(thumb_url or ""),
            )

    async def remove_favorite(self, *, user_id: str, illust_id: str) -> bool:
        user_id, illust_id = str(user_id or ""), str(illust_id or "")
        if not user_id or not illust_id:
            return False
        async with self._lock:
            return await asyncio.to_thread(self._remove_favorite_sync, user_id, illust_id)

    async def list_favorites(
        self, *, user_id: str, author: str = "", limit: int = 20, offset: int = 0
    ) -> dict[str, object]:
        user_id = str(user_id or "")
        if not user_id:
            return {"total": 0, "items": []}
        author = str(author or "").strip()
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        async with self._lock:
            return await asyncio.to_thread(
                self._list_favorites_sync, user_id, author, limit, offset
            )

    async def is_favorite(self, *, user_id: str, illust_id: str) -> bool:
        if not user_id or not illust_id:
            return False
        async with self._lock:
            return await asyncio.to_thread(self._is_favorite_sync, user_id, illust_id)

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

    def _add_favorite_sync(self, user_id, illust_id, title, author, source, url, thumb_url) -> bool:
        now = self.now_iso()
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._ensure_user_sync(conn, user_id, now)
                changed = conn.execute(
                    """
                    INSERT OR IGNORE INTO checkin_user_favorites
                    (user_id, illust_id, title, author, source, url, thumb_url, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (user_id, illust_id, title, author, source, url, thumb_url, now),
                ).rowcount
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return bool(changed)

    def _remove_favorite_sync(self, user_id: str, illust_id: str) -> bool:
        with closing(self._connect()) as conn:
            changed = conn.execute(
                "DELETE FROM checkin_user_favorites WHERE user_id = ? AND illust_id = ?",
                (user_id, illust_id),
            ).rowcount
            conn.commit()
        return bool(changed)

    def _list_favorites_sync(self, user_id, author, limit, offset) -> dict[str, object]:
        where = ["user_id = ?"]
        params: list[object] = [user_id]
        if author:
            escaped = author.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where.append("author LIKE ? ESCAPE '\\'")
            params.append(f"%{escaped}%")
        where_sql = " AND ".join(where)
        with closing(self._connect()) as conn:
            total_row = conn.execute(
                f"SELECT COUNT(*) AS count FROM checkin_user_favorites WHERE {where_sql}",
                params,
            ).fetchone()
            rows = conn.execute(
                f"""
                SELECT * FROM checkin_user_favorites
                WHERE {where_sql}
                ORDER BY created_at DESC, illust_id
                LIMIT ? OFFSET ?
                """,
                (*params, limit, offset),
            ).fetchall()
        return {
            "total": int(total_row["count"] or 0),
            "items": [self._row_to_favorite(row) for row in rows],
        }

    def _is_favorite_sync(self, user_id: str, illust_id: str) -> bool:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT 1 FROM checkin_user_favorites WHERE user_id = ? AND illust_id = ?",
                (user_id, illust_id),
            ).fetchone()
        return row is not None

    @staticmethod
    def _row_to_favorite(row: sqlite3.Row) -> FavoriteItem:
        return FavoriteItem(
            user_id=str(row["user_id"]),
            illust_id=str(row["illust_id"]),
            title=str(row["title"] or ""),
            author=str(row["author"] or ""),
            source=str(row["source"] or ""),
            url=str(row["url"] or ""),
            thumb_url=str(row["thumb_url"] or ""),
            created_at=str(row["created_at"] or ""),
        )


class AuditStoreMixin:
    async def record_audit(
        self,
        *,
        operator: str,
        action: str,
        target: str = "",
        detail: str = "",
        ip: str = "",
    ) -> AuditLog:
        if not str(action or "").strip():
            raise ValueError("action is required")
        if len(str(action)) > 64:
            raise ValueError("action 过长")
        async with self._lock:
            return await asyncio.to_thread(
                self._record_audit_sync,
                str(operator or ""),
                str(action or "").strip(),
                str(target or ""),
                str(detail or ""),
                str(ip or ""),
            )

    async def list_audit(
        self, *, action: str = "", operator: str = "", limit: int = 50, offset: int = 0
    ) -> dict[str, object]:
        action = str(action or "").strip()
        operator = str(operator or "").strip()
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        async with self._lock:
            return await asyncio.to_thread(self._list_audit_sync, action, operator, limit, offset)

    def _record_audit_sync(self, operator, action, target, detail, ip) -> AuditLog:
        now = self.now_iso()
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                """
                INSERT INTO checkin_audit_logs
                (operator, action, target, detail, ip, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (operator, action, target, detail, ip, now),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM checkin_audit_logs WHERE log_id = ?", (cursor.lastrowid,)
            ).fetchone()
        return self._row_to_audit(row)

    def _list_audit_sync(self, action, operator, limit, offset) -> dict[str, object]:
        where: list[str] = []
        params: list[object] = []
        if action:
            where.append("action = ?")
            params.append(action)
        if operator:
            where.append("operator = ?")
            params.append(operator)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        with closing(self._connect()) as conn:
            total_row = conn.execute(
                f"SELECT COUNT(*) AS count FROM checkin_audit_logs {where_sql}", params
            ).fetchone()
            rows = conn.execute(
                f"""
                SELECT * FROM checkin_audit_logs
                {where_sql}
                ORDER BY created_at DESC, log_id DESC
                LIMIT ? OFFSET ?
                """,
                (*params, limit, offset),
            ).fetchall()
        return {
            "total": int(total_row["count"] or 0),
            "logs": [self._row_to_audit(row) for row in rows],
        }

    @staticmethod
    def _row_to_audit(row: sqlite3.Row) -> AuditLog:
        return AuditLog(
            log_id=int(row["log_id"]),
            operator=str(row["operator"] or ""),
            action=str(row["action"] or ""),
            target=str(row["target"] or ""),
            detail=str(row["detail"] or ""),
            ip=str(row["ip"] or ""),
            created_at=str(row["created_at"] or ""),
        )
