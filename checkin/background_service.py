from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import time
from dataclasses import replace
from typing import Any

from .._event import EventAdapter
from .background import (
    CHECKIN_ARTWORK_TARGET_RATIO,
    CHECKIN_ARTWORK_TOLERANCE,
    filter_illusts_by_aspect_ratio,
)
from .card import CardBackground
from .models import CheckinRecord
from .quality import get_checkin_render_tier

try:
    from ..pixiv.index import ordered_by_unused
except ImportError:  # Direct imports used by the test suite.
    from pixiv.index import ordered_by_unused

logger = logging.getLogger(__name__)

LOG_PREFIX = "[GetPx]"
CHECKIN_BACKGROUND_PAGE_ATTEMPTS = 5
CHECKIN_USED_IDS_CACHE_TTL_SECONDS = 60.0
CHECKIN_USED_IDS_CACHE_MAX_ENTRIES = 512


class CheckinBackgroundService:
    """签到背景的选择、下载、恢复与占用管理。

    从 CheckinArtworkMixin 拆出：隔离「在线背景下载 / 恢复 / 去重占用」
    与卡片渲染等展示逻辑，降低单个 Mixin 的职责复杂度。

    通过 host（ShiguangPlugin 实例）访问共享设施（配置、下载器、
    图片索引、内容安全过滤等），不直接持有插件生命周期。
    """

    def __init__(self, host: Any) -> None:
        self.host = host
        # (scope, source_key) -> (monotonic 时间戳, used_ids 集合) 的短期缓存，
        # 避免同一背景源在短时间内被反复全量查询
        self._used_ids_cache: dict[tuple[str, str], tuple[float, set[str]]] = {}

    # ============ 背景恢复 ============

    async def restore(self, event: EventAdapter, record: CheckinRecord) -> CardBackground:
        saved = self.host._checkin_background_from_record(record)
        if record.background_mode == "custom":
            custom_path = self.host._resolve_custom_background_path(
                self.host._cfg_str("checkin_custom_background", "")
            )
            if custom_path is not None:
                logger.debug(f"{LOG_PREFIX} 签到背景恢复完成: mode=custom")
                return replace(saved, image_path=str(custom_path), mode="custom")
            logger.warning(
                f"{LOG_PREFIX} 签到背景恢复失败: mode=custom reason=custom_file_unavailable"
            )
            return replace(saved, mode="fallback")

        if not record.background_illust_id:
            logger.debug(f"{LOG_PREFIX} 签到背景恢复跳过: reason=no_persisted_illust")
            return replace(saved, mode="fallback")
        source = str(record.background_source or "")
        detail_id_text = str(record.background_illust_id)
        detail_page = 0
        if source.startswith("lolicon:"):
            detail_id_text, separator, page_text = detail_id_text.partition(":")
            if separator:
                try:
                    detail_page = int(page_text)
                except ValueError:
                    logger.warning(f"{LOG_PREFIX} 签到背景恢复失败: reason=invalid_lolicon_page")
                    return replace(saved, mode="fallback")
            if not self.host._cfg_str("pixiv_refresh_token"):
                logger.warning(
                    f"{LOG_PREFIX} 签到背景恢复失败: reason=lolicon_restore_requires_pixiv_token"
                )
                return replace(saved, mode="fallback")
        try:
            detail_id = int(detail_id_text)
        except ValueError:
            logger.warning(f"{LOG_PREFIX} 签到背景恢复失败: reason=invalid_illust_id")
            return replace(saved, mode="fallback")
        if getattr(self.host, "client", None) is None:
            self.host._init_client()
        if self.host.client is None:
            logger.warning(f"{LOG_PREFIX} 签到背景恢复失败: reason=pixiv_client_unavailable")
            return replace(saved, mode="fallback")

        try:
            illust = await self.host.client.illust_detail(detail_id)
            if not illust:
                logger.warning(
                    f"{LOG_PREFIX} 签到背景恢复失败: reason=detail_not_found "
                    f"illust_id={record.background_illust_id}"
                )
                return replace(saved, mode="fallback")
            if source.startswith("lolicon:"):
                illust = self.select_pixiv_detail_page(illust, detail_page)
                if not illust:
                    logger.warning(
                        f"{LOG_PREFIX} 签到背景恢复失败: "
                        f"reason=detail_page_not_found "
                        f"illust_id={record.background_illust_id}"
                    )
                    return replace(saved, mode="fallback")
            if await self.host._blacklist_reason_for_illust(illust, record.background_illust_id):
                logger.warning(
                    f"{LOG_PREFIX} 签到背景恢复被内容安全策略拒绝: "
                    f"illust_id={record.background_illust_id}"
                )
                return replace(saved, mode="fallback")
            if not filter_illusts_by_aspect_ratio(
                [illust],
                CHECKIN_ARTWORK_TARGET_RATIO,
                CHECKIN_ARTWORK_TOLERANCE,
            ):
                logger.warning(
                    f"{LOG_PREFIX} 签到背景恢复拒绝非 3:4 作品 {record.background_illust_id}"
                )
                return replace(saved, mode="fallback")
            if source.startswith("lolicon:"):
                illust = dict(illust)
                illust["_source"] = "lolicon"

            timeout_sec = self.host._cfg_float("request_timeout", 30.0, 5.0, 120.0)
            saved_quality = str(getattr(record, "background_quality", "") or "")
            background_quality = (
                saved_quality
                or get_checkin_render_tier(
                    self.host._record_checkin_render_tier(record)
                ).background_quality
            )
            path, actual_quality, file_size = await self.host.downloader.download_for_send(
                illust,
                background_quality,
                timeout=timeout_sec,
                downgrade_limit_bytes=0,
                log_context=f"[签到背景恢复] 作品 {record.background_illust_id}",
            )
            logger.debug(
                f"{LOG_PREFIX} 签到背景恢复完成: mode=pixiv_daily "
                f"source={source.partition(':')[0] or 'unknown'} "
                f"illust_id={record.background_illust_id} quality={actual_quality}"
            )
            return CardBackground(
                image_path=path,
                mode="pixiv_daily",
                source=record.background_source,
                illust_id=record.background_illust_id,
                title=record.background_title,
                author=record.background_author,
                illust=illust,
                quality=actual_quality,
                file_size=file_size,
            )
        except Exception as e:
            logger.warning(
                f"{LOG_PREFIX} 签到背景恢复失败，使用占位图: "
                f"illust_id={record.background_illust_id} "
                f"error_type={type(e).__name__}"
            )
            return replace(saved, mode="fallback")

    @staticmethod
    def select_pixiv_detail_page(illust: dict, page: int) -> dict | None:
        """把 Pixiv 详情转换为 Lolicon 记录所指向的具体页面。"""
        if page < 0:
            return None
        meta_pages = illust.get("meta_pages") or []
        if not meta_pages:
            return illust if page == 0 else None
        if page >= len(meta_pages):
            return None
        page_data = meta_pages[page] or {}
        page_urls = page_data.get("image_urls") or {}
        selected = dict(illust)
        selected["id"] = f"{illust['id']}:{page}"
        selected["meta_single_page"] = {"original_image_url": str(page_urls.get("original") or "")}
        selected["image_urls"] = dict(page_urls)
        selected["meta_pages"] = [page_data]
        return selected

    # ============ 背景选择与下载 ============

    def tag_candidates(self, tag_config: object) -> list[str]:
        tags = self.host._split_config_tags(tag_config)
        if not tags:
            return [""]
        candidates = list(tags)
        random.shuffle(candidates)
        return candidates

    async def download(
        self,
        event: EventAdapter,
        record: CheckinRecord,
        *,
        claim_usage: bool = True,
        preview_nonce: int = 0,
        preview_excluded_ids: set[str] | None = None,
        background_quality: str = "medium",
        _selected_tag: str | None = None,
    ) -> CardBackground | None:
        if _selected_tag is None:
            tag_config = self.host._cfg_str("checkin_background_tag", "")
            for selected_tag in self.tag_candidates(tag_config):
                background = await self.download(
                    event,
                    record,
                    claim_usage=claim_usage,
                    preview_nonce=preview_nonce,
                    preview_excluded_ids=preview_excluded_ids,
                    background_quality=background_quality,
                    _selected_tag=selected_tag,
                )
                if background is not None:
                    return background
            return None

        selected_tag = _selected_tag

        source_key = ""
        used_ids: set[str] = set(preview_excluded_ids or ())
        used_source_key = ""
        illusts: list[dict] = []
        raw_count = 0
        transient_offset = 0

        for page_attempt in range(1, CHECKIN_BACKGROUND_PAGE_ATTEMPTS + 1):
            try:
                illusts, raw_count, source_key = await self.host._fetch_source_candidates(
                    event,
                    selected_tag,
                    count=20,
                    offset=transient_offset if preview_nonce else 0,
                    aspect_ratio="vertical",
                    use_page_cursor=not preview_nonce,
                )
            except Exception as e:
                logger.warning(
                    f"{LOG_PREFIX} 签到背景请求失败: "
                    f"tag_configured={'yes' if selected_tag else 'no'} "
                    f"error_type={type(e).__name__}"
                )
                return None
            if preview_nonce:
                transient_offset += raw_count
            if source_key != used_source_key:
                used_ids = set(preview_excluded_ids or ())
                used_ids.update(await self.used_ids(event, source_key))
                used_source_key = source_key
            if not illusts:
                return None

            if self.host._cfg_bool("filter_manga", True):
                illusts = self.host._filter_manga(illusts)
            try:
                illusts = await self.host._filter_blacklisted_illusts(illusts)
            except RuntimeError as exc:
                logger.warning(
                    f"{LOG_PREFIX} 签到背景安全检查不可用，使用占位图: "
                    f"error_type={type(exc).__name__}"
                )
                return None
            illusts = filter_illusts_by_aspect_ratio(
                illusts,
                CHECKIN_ARTWORK_TARGET_RATIO,
                CHECKIN_ARTWORK_TOLERANCE,
            )
            if not illusts:
                if preview_nonce:
                    continue
                if self.host.image_index is None:
                    return None
                try:
                    await self.host.image_index.advance_page_offset(
                        self.host._event_scope(event), source_key, raw_count
                    )
                    logger.info(
                        f"{LOG_PREFIX} 签到背景第 {page_attempt} 页无符合 3:4 的竖向作品\uff0c切换下一页"
                    )
                except Exception as e:
                    logger.warning(
                        f"{LOG_PREFIX} 签到背景分页游标更新失败: error_type={type(e).__name__}"
                    )
                    return None
                continue

            ordered = ordered_by_unused(illusts, used_ids)
            fresh = [illust for illust in ordered if str(illust.get("id") or "") not in used_ids]
            if fresh:
                illusts = fresh
                break
            if preview_nonce:
                continue
            if self.host.image_index is None:
                logger.info(f"{LOG_PREFIX} 签到背景候选均已在去重窗口内使用，跳过图片源背景")
                return None

            try:
                await self.host.image_index.advance_page_offset(
                    self.host._event_scope(event), source_key, raw_count
                )
                logger.info(
                    f"{LOG_PREFIX} 签到背景第 {page_attempt} 页候选均已使用\uff0c切换下一页"
                )
            except Exception as e:
                logger.warning(
                    f"{LOG_PREFIX} 签到背景分页游标更新失败: error_type={type(e).__name__}"
                )
                return None
        else:
            logger.info(
                f"{LOG_PREFIX} 签到背景连续 {CHECKIN_BACKGROUND_PAGE_ATTEMPTS} 页无可用竖向作品"
            )
            return None

        seed_text = f"checkin-bg|{record.date_key}|{record.user_id}|{source_key}"
        if preview_nonce:
            seed_text += f"|{preview_nonce}"
        seed = int.from_bytes(hashlib.sha256(seed_text.encode("utf-8")).digest()[:8], "big")
        start = seed % len(illusts)
        ordered = illusts[start:] + illusts[:start]
        timeout_sec = self.host._cfg_float("request_timeout", 30.0, 5.0, 120.0)
        for idx, illust in enumerate(ordered[:8], 1):
            illust_id = str(illust.get("id") or "")
            if not illust_id:
                continue
            try:
                reason = await self.host._blacklist_reason_for_illust(illust, illust_id)
            except RuntimeError as exc:
                # 自定义安全词/黑名单读取失败时 fail-closed：不放过该候选，
                # 跳过去试下一个；若所有候选都不可用则回退占位图。
                logger.warning(
                    f"{LOG_PREFIX} 签到背景安全检查不可用，跳过作品: "
                    f"illust_id={illust_id} error_type={type(exc).__name__}"
                )
                continue
            if reason:
                logger.debug(
                    f"{LOG_PREFIX} 签到背景跳过: reason=content_policy illust_id={illust_id}"
                )
                continue
            claimed = False
            if claim_usage:
                claimed = await self.claim_usage(event, source_key, illust_id)
                if not claimed:
                    logger.debug(
                        f"{LOG_PREFIX} 签到背景跳过\uff1a作品 {illust_id} 已被其他签到占用"
                    )
                    continue
            title = illust.get("title", "无标题")
            background_ready = False
            try:
                path, actual_q, file_size = await self.host.downloader.download_for_send(
                    illust,
                    background_quality,
                    timeout=timeout_sec,
                    downgrade_limit_bytes=0,
                    log_context=f"[签到背景 {idx}] 作品 {illust_id}",
                )
                author = str((illust.get("user") or {}).get("name") or "")
                background = CardBackground(
                    image_path=path,
                    mode="pixiv_daily",
                    source=source_key,
                    illust_id=illust_id,
                    title=str(title or ""),
                    author=author,
                    illust=illust,
                    quality=actual_q,
                    file_size=file_size,
                )
                background_ready = True
                logger.debug(
                    f"{LOG_PREFIX} 签到背景选择完成: mode=pixiv_daily "
                    f"source={source_key.partition(':')[0] or 'unknown'} "
                    f"illust_id={illust_id} quality={actual_q} file_size={file_size}"
                )
                return background
            except asyncio.TimeoutError:
                logger.debug(f"{LOG_PREFIX} 签到背景候选跳过: reason=timeout illust_id={illust_id}")
            except Exception as e:
                logger.debug(
                    f"{LOG_PREFIX} 签到背景候选跳过: reason=download_error "
                    f"illust_id={illust_id} "
                    f"error_type={type(e).__name__}"
                )
            finally:
                if claimed and not background_ready:
                    await self.release_usage(event, source_key, illust_id)
        return None

    # ============ 去重占用 ============

    def _invalidate_used_ids_cache(self, event: EventAdapter, source_key: str) -> None:
        if not source_key:
            return
        cache_key = (self.host._event_scope(event), source_key)
        self._used_ids_cache.pop(cache_key, None)

    async def used_ids(self, event: EventAdapter, source_key: str) -> set[str]:
        scope = self.host._event_scope(event)
        cache_key = (scope, source_key)
        now = time.monotonic()
        cached = self._used_ids_cache.get(cache_key)
        if cached is not None and now - cached[0] < CHECKIN_USED_IDS_CACHE_TTL_SECONDS:
            return set(cached[1])
        used_ids: set[str] = set()
        if self.host.image_index is not None:
            try:
                used_ids.update(await self.host.image_index.get_used_illust_ids(scope, source_key))
            except Exception as exc:
                logger.warning(
                    f"{LOG_PREFIX} 签到背景读取去重索引失败: error_type={type(exc).__name__}"
                )
        self._used_ids_cache[cache_key] = (now, set(used_ids))
        if len(self._used_ids_cache) > CHECKIN_USED_IDS_CACHE_MAX_ENTRIES:
            cutoff = now - CHECKIN_USED_IDS_CACHE_TTL_SECONDS
            self._used_ids_cache = {
                key: value for key, value in self._used_ids_cache.items() if value[0] >= cutoff
            }
        return used_ids

    async def claim_usage(self, event: EventAdapter, source_key: str, illust_id: str) -> bool:
        if self.host.image_index is None or not source_key or not illust_id:
            return True
        try:
            claimed = await self.host.image_index.claim_usage(
                scope=self.host._event_scope(event),
                source_key=source_key,
                illust_id=illust_id,
                feature="checkin_pending",
                user_id=str(event.get_sender_id() or ""),
            )
            if claimed:
                self._invalidate_used_ids_cache(event, source_key)
            logger.debug(
                f"{LOG_PREFIX} 签到背景占用结果: "
                f"result={'claimed' if claimed else 'duplicate'} "
                f"source={source_key.partition(':')[0] or 'unknown'} "
                f"illust_id={illust_id}"
            )
            return claimed
        except Exception as exc:
            logger.debug(
                f"{LOG_PREFIX} 签到背景占用失败，拒绝使用候选: "
                f"reason=index_error error_type={type(exc).__name__}"
            )
            return False

    async def release_usage(self, event: EventAdapter, source_key: str, illust_id: str) -> None:
        if self.host.image_index is None or not source_key or not illust_id:
            return
        try:
            await self.host.image_index.release_usage(
                scope=self.host._event_scope(event),
                source_key=source_key,
                illust_id=illust_id,
                feature="checkin_pending",
            )
            self._invalidate_used_ids_cache(event, source_key)
            logger.debug(
                f"{LOG_PREFIX} 签到背景占用已释放: "
                f"source={source_key.partition(':')[0] or 'unknown'} "
                f"illust_id={illust_id}"
            )
        except Exception as exc:
            logger.debug(
                f"{LOG_PREFIX} 签到背景占用释放未完成: "
                f"reason=index_error error_type={type(exc).__name__}"
            )

    async def release_claim(self, event: EventAdapter, background: CardBackground | None) -> None:
        if (
            background is None
            or background.mode != "pixiv_daily"
            or not background.source
            or not background.illust_id
        ):
            return
        await self.release_usage(event, background.source, background.illust_id)
