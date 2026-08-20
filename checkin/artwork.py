from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from .._event import EventAdapter
from .cache import is_valid_card_jpeg
from .card import (
    CHECKIN_CARD_HEIGHT,
    CHECKIN_CARD_WIDTH,
    CardBackground,
    build_checkin_card_data,
    get_checkin_card_template,
)
from .models import CheckinRecord
from .quality import (
    CHECKIN_JPEG_QUALITY,
    DEFAULT_CHECKIN_RENDER_TIER,
    CheckinRenderTier,
    checkin_render_fallbacks,
    get_checkin_render_tier,
    normalize_checkin_render_tier,
)

_CHECKIN_BACKGROUND_MODE_LABELS = {
    "pixiv_daily": "在线图片",
    "custom": "自定义背景",
    "fallback": "占位图",
}


def _checkin_background_mode_label(background: CardBackground | None) -> str:
    mode = getattr(background, "mode", "") or "none"
    return _CHECKIN_BACKGROUND_MODE_LABELS.get(str(mode), str(mode))


try:
    from ..pixiv.downloader import cleanup
except ImportError:  # Direct imports used by the test suite.
    from pixiv.downloader import cleanup

logger = logging.getLogger(__name__)


LOG_PREFIX = "[GetPx]"
CHECKIN_PREVIEW_BACKGROUND_TTL_SECONDS = 300.0


class CheckinArtworkMixin:
    """Render cards and select, claim, restore and release artwork."""

    async def _record_checkin_background(
        self, event: EventAdapter, background: CardBackground | None
    ) -> None:
        if (
            background is None
            or background.mode != "pixiv_daily"
            or not background.source
            or not background.illust_id
        ):
            return
        illust = dict(background.illust or {})
        illust.setdefault("id", background.illust_id)
        illust.setdefault("title", background.title)
        illust.setdefault("user", {"name": background.author})
        await self._record_image_usage(
            event,
            background.source,
            illust,
            feature="checkin",
            user_id=str(event.get_sender_id() or ""),
        )

    async def _restore_checkin_background(
        self,
        event: EventAdapter,
        record: CheckinRecord,
    ) -> CardBackground:
        """恢复签到背景（委托 CheckinBackgroundService，保留 Mixin 接口）。"""
        return await self.checkin_background_service.restore(event, record)

    async def _render_checkin_card(
        self,
        event: EventAdapter,
        *,
        profile,
        record,
        background: CardBackground | None,
        bot_name: str,
        user_title: str = "",
        render_tier: str | None = None,
    ) -> str:
        avatar_url = (
            self._checkin_avatar_url(event)
            if self._cfg_bool("checkin_avatar_enabled", True)
            else ""
        )
        width = CHECKIN_CARD_WIDTH
        height = CHECKIN_CARD_HEIGHT
        render_spec = get_checkin_render_tier(render_tier or self._configured_checkin_render_tier())
        # 视图模型构建含背景图 Data URL 编码（读文件 + base64），放入线程池
        data = await asyncio.to_thread(
            build_checkin_card_data,
            profile=profile,
            record=record,
            bot_name=bot_name,
            avatar_url=avatar_url,
            background=background,
            user_title=user_title,
            width=width,
            height=height,
            background_refresh_cost=self._cfg_int("checkin_background_refresh_cost", 100, 0, 500),
        )
        options = {
            "full_page": False,
            "type": "jpeg",
            "quality": CHECKIN_JPEG_QUALITY,
            "clip": {"x": 0, "y": 0, "width": width, "height": height},
            "viewport": {"width": width, "height": height},
            "animations": "disabled",
        }
        if render_spec.scale_level is not None:
            options["device_scale_factor_level"] = render_spec.scale_level
        return await self._checkin_render_html(
            get_checkin_card_template(getattr(record, "theme_id", "default")),
            data,
            options=options,
        )

    def _configured_checkin_render_tier(self) -> str:
        return normalize_checkin_render_tier(
            self._cfg_str("checkin_card_quality_tier", DEFAULT_CHECKIN_RENDER_TIER)
        )

    @staticmethod
    def _record_checkin_render_tier(record: CheckinRecord) -> str:
        return normalize_checkin_render_tier(
            getattr(record, "render_tier", DEFAULT_CHECKIN_RENDER_TIER)
        )

    @staticmethod
    def _cache_get_for_tier(cache, date_key: str, cache_key: str, spec):
        return cache.get(
            date_key,
            cache_key,
            expected_size=spec.expected_size,
        )

    @staticmethod
    async def _cache_store_for_tier(
        cache,
        date_key: str,
        cache_key: str,
        renderer,
        spec: CheckinRenderTier,
    ):
        return await cache.store(
            date_key,
            cache_key,
            renderer,
            expected_size=spec.expected_size,
        )

    async def _get_cached_checkin_card(
        self,
        event: EventAdapter,
        *,
        cache,
        profile,
        record,
        background: CardBackground | None,
        bot_name: str,
        user_title: str,
        preferred_tier: str,
    ) -> tuple[Path | None, str]:
        for spec in checkin_render_fallbacks(preferred_tier):
            cache_key = await asyncio.to_thread(
                self._checkin_card_cache_key,
                event,
                profile=profile,
                record=record,
                background=background,
                bot_name=bot_name,
                user_title=user_title,
                render_tier=spec.name,
            )
            cached = await asyncio.to_thread(
                self._cache_get_for_tier,
                cache,
                record.date_key,
                cache_key,
                spec,
            )
            if cached is not None:
                logger.debug(
                    f"{LOG_PREFIX} 签到卡缓存命中: 画质={spec.name} "
                    f"日期={record.date_key} 输出尺寸={spec.expected_size[0]}x{spec.expected_size[1]}"
                )
                return Path(cached), spec.name
            logger.debug(
                f"{LOG_PREFIX} 签到卡缓存未命中: 画质={spec.name} "
                f"日期={record.date_key} 输出尺寸={spec.expected_size[0]}x{spec.expected_size[1]}"
            )
        return None, normalize_checkin_render_tier(preferred_tier)

    async def _render_checkin_card_with_fallback(
        self,
        event: EventAdapter,
        *,
        profile,
        record,
        background: CardBackground | None,
        bot_name: str,
        user_title: str = "",
        preferred_tier: str,
        cache=None,
    ) -> tuple[Path, str]:
        last_error: Exception | None = None
        fallback_specs = checkin_render_fallbacks(preferred_tier)
        for tier_index, spec in enumerate(fallback_specs):
            renderer_source_path = ""
            render_succeeded = False
            started_at = time.monotonic()

            async def render_card(spec=spec) -> str:
                nonlocal renderer_source_path
                renderer_source_path = await self._render_checkin_card(
                    event,
                    profile=profile,
                    record=record,
                    background=background,
                    bot_name=bot_name,
                    user_title=user_title,
                    render_tier=spec.name,
                )
                return renderer_source_path

            try:
                logger.debug(
                    f"{LOG_PREFIX} 签到卡开始渲染: 画质={spec.name} "
                    f"输出尺寸={spec.expected_size[0]}x{spec.expected_size[1]} "
                    f"背景模式={_checkin_background_mode_label(background)}"
                )
                if cache is None:
                    card_path = Path(await render_card())
                    if not is_valid_card_jpeg(card_path, spec.expected_size):
                        width, height = spec.expected_size
                        raise ValueError(f"renderer output must be a valid {width}x{height} JPEG")
                else:
                    cache_key = await asyncio.to_thread(
                        self._checkin_card_cache_key,
                        event,
                        profile=profile,
                        record=record,
                        background=background,
                        bot_name=bot_name,
                        user_title=user_title,
                        render_tier=spec.name,
                    )
                    card_path = Path(
                        await self._cache_store_for_tier(
                            cache,
                            record.date_key,
                            cache_key,
                            render_card,
                            spec,
                        )
                    )
                render_succeeded = True
                logger.info(
                    f"{LOG_PREFIX} 签到卡渲染完成：画质={spec.name} "
                    f"输出尺寸={spec.expected_size[0]}x{spec.expected_size[1]} "
                    f"耗时={int((time.monotonic() - started_at) * 1000)}ms"
                )
                return card_path, spec.name
            except Exception as exc:
                last_error = exc
                has_lower_tier = tier_index + 1 < len(fallback_specs)
                logger.warning(
                    f"{LOG_PREFIX} 签到卡渲染失败: 画质={spec.name} "
                    f"输出尺寸={spec.expected_size[0]}x{spec.expected_size[1]} "
                    f"是否降档={'是' if has_lower_tier else '否'} "
                    f"错误类型={type(exc).__name__}"
                )
            finally:
                if cache is not None or not render_succeeded:
                    cleanup(renderer_source_path)
        raise RuntimeError("all check-in card render tiers failed") from last_error

    async def _prepare_checkin_background(
        self,
        event: EventAdapter,
        record,
        *,
        claim_usage: bool = True,
        refresh_preview: bool = False,
        render_tier: str | None = None,
    ) -> CardBackground | None:
        mode = self._cfg_str("checkin_background_mode", "pixiv_daily") or "pixiv_daily"
        if mode == "custom":
            custom_path = self._resolve_custom_background_path(
                self._cfg_str("checkin_custom_background", "")
            )
            if custom_path:
                logger.debug(f"{LOG_PREFIX} 签到背景选择完成: mode=custom")
                return CardBackground(
                    image_path=str(custom_path),
                    mode="custom",
                    source="custom",
                )
            logger.info(f"{LOG_PREFIX} 签到自定义背景不可用，回退在线图片源背景")
        elif mode != "pixiv_daily":
            mode = "pixiv_daily"
        preview_nonce = 0
        preview_excluded_ids: set[str] = set()
        if refresh_preview:
            preview_nonce = int(getattr(self, "_checkin_preview_sequence", 0)) + 1
            self._checkin_preview_sequence = preview_nonce
            recent_map = getattr(self, "_checkin_preview_background_ids", None)
            if recent_map is None:
                recent_map = {}
                self._checkin_preview_background_ids = recent_map
            now_monotonic = time.monotonic()
            for recent_user_id, recent_items in list(recent_map.items()):
                active_items = [
                    (illust_id, created_at)
                    for illust_id, created_at in recent_items
                    if now_monotonic - created_at < CHECKIN_PREVIEW_BACKGROUND_TTL_SECONDS
                ]
                if active_items:
                    recent_map[recent_user_id] = active_items[-20:]
                else:
                    recent_map.pop(recent_user_id, None)
            active_recent = [
                (illust_id, created_at)
                for illust_id, created_at in recent_map.get(record.user_id, ())
            ]
            recent_map[record.user_id] = active_recent
            preview_excluded_ids.update(item[0] for item in active_recent)

        pixiv_bg = await self.checkin_background_service.download(
            event,
            record,
            claim_usage=claim_usage,
            preview_nonce=preview_nonce,
            preview_excluded_ids=preview_excluded_ids,
            background_quality=get_checkin_render_tier(
                render_tier or self._configured_checkin_render_tier()
            ).background_quality,
        )
        if pixiv_bg is not None:
            if refresh_preview and pixiv_bg.illust_id:
                recent = list(self._checkin_preview_background_ids.get(record.user_id, ()))
                recent.append((pixiv_bg.illust_id, time.monotonic()))
                self._checkin_preview_background_ids[record.user_id] = recent[-20:]
            return pixiv_bg
        logger.info(
            f"{LOG_PREFIX} 签到背景选择失败，使用占位图: reason=no_available_online_background"
        )
        return CardBackground(mode="fallback", source="fallback")

    def _resolve_custom_background_path(self, value: str) -> Path | None:
        raw = str(value or "").strip().strip('"')
        if not raw:
            return None
        path = Path(raw)
        if not path.is_absolute():
            path = Path(self.data_dir) / raw
        try:
            resolved = path.resolve()
        except OSError as exc:
            logger.debug(
                f"{LOG_PREFIX} 签到自定义背景不可用: stage=resolve error_type={type(exc).__name__}"
            )
            return None
        if not resolved.is_file():
            logger.debug(f"{LOG_PREFIX} 签到自定义背景不可用: reason=file_not_found")
            return None
        if resolved.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            logger.debug(f"{LOG_PREFIX} 签到自定义背景不可用: reason=unsupported_format")
            return None
        try:
            from PIL import Image as PILImage
        except ImportError:
            logger.warning(f"{LOG_PREFIX} 未安装 Pillow\uff0c跳过背景完整性校验")
            return resolved
        try:
            with PILImage.open(resolved) as img:
                img.verify()
        except Exception:
            logger.debug(f"{LOG_PREFIX} 签到自定义背景不可用: reason=corrupt_or_unreadable")
            return None
        return resolved

    async def _release_checkin_background_claim(
        self, event: EventAdapter, background: CardBackground | None
    ) -> None:
        """释放签到背景占用（委托 CheckinBackgroundService，保留 Mixin 接口）。"""
        await self.checkin_background_service.release_claim(event, background)

    def _checkin_avatar_url(self, event: EventAdapter) -> str:
        user_id = str(event.get_sender_id() or "")
        if not user_id:
            return ""
        platform = event.get_platform_name()
        if platform == "aiocqhttp" and user_id.isdigit():
            return f"https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640"
        return ""
