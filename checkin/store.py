from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

from .backup_store import BackupStoreMixin
from .favorites_store import AuditStoreMixin, FavoritesStoreMixin
from .feature_store import FeatureStoreMixin
from .items_store import ItemsStoreMixin
from .models import SHANGHAI_TZ
from .ranking_store import RankingStoreMixin
from .record_store import RecordStoreMixin
from .schema import SchemaMixin
from .season_store import SeasonStoreMixin
from .social_store import SocialStoreMixin
from .stats_store import StatsStoreMixin
from .subscription_store import SubscriptionStoreMixin


class CheckinStore(
    RecordStoreMixin,
    ItemsStoreMixin,
    SocialStoreMixin,
    FavoritesStoreMixin,
    AuditStoreMixin,
    SeasonStoreMixin,
    SubscriptionStoreMixin,
    StatsStoreMixin,
    RankingStoreMixin,
    FeatureStoreMixin,
    BackupStoreMixin,
    SchemaMixin,
):
    def __init__(self, data_dir: Path | str):
        self._db_path = Path(data_dir) / "checkin.sqlite3"
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._init_db()

    @staticmethod
    def today_key() -> str:
        return datetime.now(SHANGHAI_TZ).date().isoformat()

    @staticmethod
    def now_iso() -> str:
        return datetime.now(SHANGHAI_TZ).isoformat(timespec="seconds")
