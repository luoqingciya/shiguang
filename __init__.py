"""拾光集（shiguang）— Qingci-Bot 安全插画发图与签到插件

由 AstrBot 插件「画境拾珍」（astrbot_plugin_get_px）移植而来：
- 发图：Lolicon 主源、Pixiv 可选回退、内容安全过滤、多自然日去重、
  合并转发（OneBot 11）/ 逐条发送
- 签到：金币/好感/连签/加持/成就/称号/生日/节假日/全局事件/主题商店、排行、备份
- 自然语言触发（可配置）与纯文本签到

命令：
    /p [标签] [数量]          搜索并发送图片
    /签到                      每日签到
    /签到帮助                  签到指令帮助
    /签到我的 <状态|生日|成就|称号>   个人签到资料
    /签到排行 <今日|月榜|连签|累计>    群排行
    /签到商店 <查看|加持|主题|刷新背景> 签到商店
    /签到管理 <预览|导出|事件>       管理员维护
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from qingci_plugin_sdk import (
    ADMIN,
    MatcherContext,
    PluginBase,
    on_command,
    on_message,
    regex,
)

from ._event import EventAdapter, result_to_reply
from .checkin import CheckinStore, UnversionedCheckinDatabaseError
from .checkin.application import CheckinApplicationMixin
from .checkin.artwork import CheckinArtworkMixin
from .checkin.background_service import CheckinBackgroundService
from .checkin.cache import CheckinCardCache
from .checkin.commands import CheckinCommandMixin
from .checkin.greeting import CheckinGreetingGenerator
from .checkin.holiday import HolidayCalendar
from .checkin.shop import CheckinShopMixin
from .pixiv import DeliveryMixin, FiltersMixin, SearchMixin
from .pixiv.client import PixivClient
from .pixiv.constants import MAX_IMAGE_COUNT
from .pixiv.downloader import ImageDownloader
from .pixiv.index import ImageIndexStore
from .pixiv.lolicon import LoliconClient
from .plugin_api import PluginWebApi

logger = logging.getLogger(__name__)

LOG_PREFIX = "[ShiGuang]"


def _read_plugin_version() -> str:
    """从 plugin.json 读取版本作为权威来源，规避多处方言漂移导致的持久"可更新"死循环"""
    try:
        meta = json.loads(
            (Path(__file__).resolve().parent / "plugin.json").read_text(encoding="utf-8")
        )
        version = meta.get("version") if isinstance(meta, dict) else None
    except (OSError, ValueError):
        version = None
    return str(version) if version else "1.0.2"


PLUGIN_VERSION = _read_plugin_version()
WEB_INTERNAL_ERROR_MESSAGE = "服务内部错误，请稍后重试"
WEB_PAGE_TITLE = "拾光集管理中心"

AUTO_TRIGGER_PATTERN = r"^/?(来\s*(.*?)(份|个|张|点))(.*?)(福利|色|瑟|涩|塞)?图$"
CHECKIN_REGEX_PATTERN = r"^(?!/)签到$"
CHECKIN_HELP_IMAGE = Path(__file__).resolve().parent / "assets" / "checkin_help_v4.png"

CHINESE_NUMBER_MAP = {
    "一": "1",
    "二": "2",
    "两": "2",
    "三": "3",
    "四": "4",
    "五": "5",
    "六": "6",
    "七": "7",
    "八": "8",
    "九": "9",
    "十": "10",
}


@lru_cache(maxsize=32)
def _compile_checkin_template(template_html: str):
    """缓存编译后的 Jinja2 模板，避免每次签到卡渲染重复编译。"""
    from jinja2 import Template

    return Template(template_html)


class Config:
    """拾光集插件配置（WebUI 表单 schema 由类型注解自动生成）"""

    pixiv_refresh_token: str = ""
    lolicon_api_url: str = "https://api.lolicon.app/setu/v2"
    lolicon_exclude_ai: bool = True
    lolicon_image_proxy_origins: str = ""
    filter_manga: bool = True
    max_count: int = 5
    dedupe_days: int = 1
    dedupe_ttl_hours: float = 24.0
    dedupe_days_migrated: bool = False
    request_timeout: float = 30.0
    image_quality: str = "original"
    auto_downgrade_original_mb: float = 3.0
    forward_threshold: int = 1
    auto_trigger_enabled: bool = False
    checkin_enabled: bool = True
    checkin_bot_name: str = "neko"
    checkin_background_mode: str = "pixiv_daily"
    checkin_background_refresh_cost: int = 100
    checkin_theme_cost: int = 1500
    checkin_background_tag: str = ""
    checkin_custom_background: str = ""
    checkin_avatar_enabled: bool = True
    checkin_card_quality_tier: str = "省流量"
    checkin_greeting_mode: str = "hitokoto"
    checkin_hitokoto_categories: list = None  # type: ignore[assignment]
    checkin_ai_greeting_provider_id: str = ""
    checkin_ai_greeting_prompt: str = ""
    checkin_ai_greeting_timeout: float = 8.0
    checkin_hitokoto_timeout: float = 5.0
    rate_limit_seconds: int = 3
    webui_font_source: str = "mirror"


@dataclass
class ShiguangSettings:
    """运行期类型化配置快照（由 on_load 从 self.config 构建）。

    字段与 Config 一一对应，但默认值为 None 表示「用户未配置」，
    由 _cfg_* 在读取时回退到各调用点的默认参数，避免与调用点默认不一致。
    集中声明配置 key 便于 IDE 提示与后续校验。
    """

    pixiv_refresh_token: str | None = None
    lolicon_api_url: str | None = None
    lolicon_exclude_ai: bool | None = None
    lolicon_image_proxy_origins: str | None = None
    filter_manga: bool | None = None
    max_count: int | None = None
    dedupe_days: int | None = None
    dedupe_ttl_hours: float | None = None
    dedupe_days_migrated: bool | None = None
    request_timeout: float | None = None
    image_quality: str | None = None
    auto_downgrade_original_mb: float | None = None
    forward_threshold: int | None = None
    send_as_forward: bool | None = None
    auto_trigger_enabled: bool | None = None
    checkin_enabled: bool | None = None
    checkin_bot_name: str | None = None
    checkin_background_mode: str | None = None
    checkin_background_refresh_cost: int | None = None
    checkin_theme_cost: int | None = None
    checkin_background_tag: str | None = None
    checkin_custom_background: str | None = None
    checkin_avatar_enabled: bool | None = None
    checkin_card_quality_tier: str | None = None
    checkin_greeting_mode: str | None = None
    checkin_hitokoto_categories: list | None = None
    checkin_ai_greeting_provider_id: str | None = None
    checkin_ai_greeting_prompt: str | None = None
    checkin_ai_greeting_timeout: float | None = None
    checkin_hitokoto_timeout: float | None = None
    rate_limit_seconds: int | None = None
    webui_font_source: str | None = None

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any]) -> ShiguangSettings:
        settings = cls()
        for name, value in mapping.items():
            if value is None or not hasattr(settings, name):
                continue
            setattr(settings, name, value)
        return settings


class ShiguangPlugin(
    CheckinApplicationMixin,
    CheckinCommandMixin,
    CheckinShopMixin,
    CheckinArtworkMixin,
    SearchMixin,
    DeliveryMixin,
    FiltersMixin,
    PluginBase,
):
    name = "shiguang"
    version = PLUGIN_VERSION
    author = "Qingci-Bot"
    description = "拾光集：安全插画发图 + 签到（由画境拾珍移植）"
    category = "fun"

    def __init__(self):
        super().__init__()
        self.config: dict = {}
        self.settings: ShiguangSettings | None = None
        self.client: PixivClient | None = None
        self.lolicon_client: LoliconClient | None = None
        self.downloader = ImageDownloader("")
        self._last_request: dict[str, float] = {}
        self.image_index: ImageIndexStore | None = None
        self.checkin_store: CheckinStore | None = None
        self.checkin_cache: CheckinCardCache | None = None
        self.checkin_greeting: CheckinGreetingGenerator | None = None
        self.checkin_background_service: CheckinBackgroundService | None = None
        self.holiday_calendar: HolidayCalendar | None = None
        self.plugin_web_api: PluginWebApi | None = None
        self._holiday_refresh_task: asyncio.Task | None = None
        self._termination_task: asyncio.Task[None] | None = None
        # 显式 dict 承载签到流程锁：WeakValueDictionary 中的锁可能在
        # 无外部强引用时被 GC 提前回收，并发下重建锁会破坏串行保证
        self._checkin_flow_locks: dict[str, asyncio.Lock] = {}

    # ============ 生命周期 ============

    async def on_load(self):
        cfg = self.plugin_config
        if cfg is None:
            cfg = {}
        elif hasattr(cfg, "model_dump"):
            cfg = cfg.model_dump()
        self.config = dict(cfg)
        self.settings = ShiguangSettings.from_mapping(self.config)
        self.downloader = ImageDownloader(self._cfg_str("lolicon_image_proxy_origins", ""))

        await asyncio.to_thread(self._ensure_checkin_hitokoto_defaults)
        self._register_matchers()
        self._register_web_center()
        await self._initialize()
        self.checkin_background_service = CheckinBackgroundService(self)

    async def on_unload(self):
        await self.terminate()

    async def on_shutdown(self):
        await self.terminate()

    def _register_web_center(self) -> None:
        """注册插件 Web API 与管理页面（Stage 5：WebUI 管理中心）"""
        self.plugin_web_api = PluginWebApi(
            self,
            plugin_name=self.name,
            log_prefix=LOG_PREFIX,
            internal_error_message=WEB_INTERNAL_ERROR_MESSAGE,
        )
        self.plugin_web_api.register()
        static_dir = Path(__file__).resolve().parent / "pages" / "pluginCenter"
        self.register_page(
            WEB_PAGE_TITLE,
            icon="🌅",
            static_dir=str(static_dir),
        )
        logger.info(f"{LOG_PREFIX} WebUI 管理中心已注册: {WEB_PAGE_TITLE}")

    def _ensure_checkin_hitokoto_defaults(self) -> None:
        if not isinstance(self.config.get("checkin_hitokoto_categories"), list):
            self.config["checkin_hitokoto_categories"] = ["全部"]
        if self.settings is not None:
            self.settings.checkin_hitokoto_categories = self.config["checkin_hitokoto_categories"]

    # ============ 命令注册 ============

    def _register_matchers(self) -> None:
        self.matchers = []

        # 发图主指令
        self.matchers.append(
            on_command(
                "p",
                aliases=("图",),
                description="搜索并发送图片：[标签] [数量]",
            )(self.cmd_p)
        )
        # 签到
        self.matchers.append(on_command("签到", description="每日签到")(self.cmd_checkin))
        self.matchers.append(
            on_command("签到帮助", description="签到指令帮助")(self.cmd_checkin_help)
        )
        self.matchers.append(
            on_command(
                "签到我的",
                description="个人签到资料",
                subcommands={
                    "状态": self.cmd_checkin_status,
                    "生日查看": self.cmd_checkin_birthday_view,
                    "生日设置": self.cmd_checkin_birthday_set,
                    "生日清除": self.cmd_checkin_birthday_clear,
                    "成就": self.cmd_checkin_achievements,
                    "称号查看": self.cmd_checkin_titles,
                    "称号佩戴": self.cmd_select_checkin_title,
                },
            )(self._noop)
        )
        self.matchers.append(
            on_command(
                "签到排行",
                description="签到排行",
                subcommands={
                    "今日": self.cmd_checkin_ranking_today,
                    "月榜": self.cmd_checkin_ranking_month,
                    "连签": self.cmd_checkin_ranking_streak,
                    "累计": self.cmd_checkin_ranking_total,
                },
            )(self._noop)
        )
        self.matchers.append(
            on_command(
                "签到商店",
                description="签到商店",
                subcommands={
                    "查看": self.cmd_checkin_shop,
                    "加持": self.cmd_buy_checkin_boost,
                    "主题列表": self.cmd_checkin_themes,
                    "主题查看": self.cmd_preview_checkin_theme,
                    "主题购买": self.cmd_buy_checkin_theme,
                    "主题切换": self.cmd_select_checkin_theme,
                    "刷新背景": self.cmd_refresh_checkin_background,
                },
            )(self._noop)
        )
        self.matchers.append(
            on_command(
                "签到管理",
                description="签到管理（管理员）",
                permission=ADMIN,
                subcommands={
                    "预览": self.cmd_checkin_preview,
                    "导出": self.cmd_checkin_export,
                    "事件查看": self.cmd_checkin_event_list,
                    "事件添加": self.cmd_checkin_event_add,
                    "事件删除": self.cmd_checkin_event_delete,
                },
            )(self._noop)
        )
        # 纯文本签到触发
        self.matchers.append(
            on_message(rule=regex(CHECKIN_REGEX_PATTERN), description="纯文本签到")(
                self.checkin_auto_trigger
            )
        )
        # 自然语言发图触发
        self.matchers.append(
            on_message(rule=regex(AUTO_TRIGGER_PATTERN), description="自然语言发图")(
                self.auto_trigger
            )
        )

    @staticmethod
    async def _noop(_ctx: MatcherContext) -> None:
        return None

    # ============ 初始化 / 清理 ============

    async def _initialize(self) -> None:
        data_dir = self.data_dir
        dedupe_days = self._migrate_dedupe_config()
        self._init_client()
        self.image_index = await asyncio.to_thread(
            ImageIndexStore,
            str(data_dir),
            retention_days=dedupe_days,
        )
        if self.image_index is not None:
            await self.image_index.cleanup_old_days(trigger="startup")
        checkin_database_existed = (data_dir / "checkin.sqlite3").exists()
        try:
            self.checkin_store = await asyncio.to_thread(CheckinStore, str(data_dir))
        except UnversionedCheckinDatabaseError:
            logger.error(
                f"{LOG_PREFIX} 签到数据库缺少 schema 版本号且已包含数据表，"
                "请检查或移除 checkin.sqlite3 后重启插件。"
            )
            raise
        database_action = "已加载" if checkin_database_existed else "已创建"
        db_path = str(self.checkin_store._db_path) if self.checkin_store else "?"
        logger.info(
            f"{LOG_PREFIX} 签到数据库{database_action}: version={PLUGIN_VERSION}, path={db_path}"
        )
        self.checkin_cache = CheckinCardCache(data_dir / "checkin_card_cache")
        await asyncio.to_thread(self.checkin_cache.cleanup_expired, force=True)
        self.holiday_calendar = HolidayCalendar(data_dir, plugin_version=PLUGIN_VERSION)
        self._holiday_refresh_task = asyncio.create_task(self._refresh_holiday_calendar())
        self.checkin_greeting = CheckinGreetingGenerator(getattr(self, "llm", None))
        logger.info(f"{LOG_PREFIX} 插件已加载: version={PLUGIN_VERSION}")

    async def terminate(self) -> None:
        task = self._termination_task
        if task is not None and task.done() and (task.cancelled() or task.exception() is not None):
            self._termination_task = None
            task = None
        if task is None:
            task = asyncio.create_task(self._terminate_resources())
            self._termination_task = task
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled() and self._termination_task is task:
                self._termination_task = None
            raise
        except Exception:
            if self._termination_task is task:
                self._termination_task = None
            raise

    async def _terminate_resources(self) -> None:
        if self._holiday_refresh_task is not None:
            self._holiday_refresh_task.cancel()
            await asyncio.gather(self._holiday_refresh_task, return_exceptions=True)
            self._holiday_refresh_task = None
        for attr in ("client", "lolicon_client"):
            client = getattr(self, attr, None)
            if client is not None:
                try:
                    await client.close()
                except Exception as exc:
                    logger.warning(
                        f"{LOG_PREFIX} 关闭 {attr} 失败: error_type={type(exc).__name__}"
                    )
                setattr(self, attr, None)
        try:
            await self.downloader.close()
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} 关闭图片下载器失败: error_type={type(exc).__name__}")
        if self.checkin_greeting is not None:
            try:
                await self.checkin_greeting.close()
            except Exception as exc:
                logger.warning(
                    f"{LOG_PREFIX} 关闭签到问候会话失败: error_type={type(exc).__name__}"
                )
            self.checkin_greeting = None
        self._last_request.clear()
        locks = getattr(self, "_checkin_flow_locks", None)
        if locks is not None:
            locks.clear()
        if self.image_index is not None:
            try:
                self.image_index.close()
            except Exception as exc:
                logger.warning(f"{LOG_PREFIX} 关闭图片索引失败: error_type={type(exc).__name__}")
        self.image_index = None
        self.checkin_store = None
        self.checkin_background_service = None
        logger.info(f"{LOG_PREFIX} 插件已停止")

    async def _refresh_holiday_calendar(self) -> None:
        if self.holiday_calendar is None:
            return
        try:
            updated = await self.holiday_calendar.refresh_if_due()
            if updated:
                logger.info(f"{LOG_PREFIX} 节假日数据已更新")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                f"{LOG_PREFIX} 节假日数据更新失败，继续使用本地规则: "
                f"error_type={type(exc).__name__}"
            )

    def _init_client(self) -> None:
        lolicon_url = self._cfg_str("lolicon_api_url", "https://api.lolicon.app/setu/v2")
        if getattr(self, "lolicon_client", None) is None:
            self.lolicon_client = LoliconClient(
                api_url=lolicon_url,
                exclude_ai=self._cfg_bool("lolicon_exclude_ai", True),
                request_timeout=self._cfg_float("request_timeout", 30.0, 5.0, 120.0),
            )
        token = self._cfg_str("pixiv_refresh_token")
        if not token:
            logger.info(f"{LOG_PREFIX} 未配置 Pixiv refresh_token，仅使用 Lolicon 主源")
            return
        self.client = PixivClient(
            refresh_token=token,
            request_timeout=self._cfg_float("request_timeout", 30.0, 5.0, 120.0),
        )
        logger.info(f"{LOG_PREFIX} Lolicon 主源和 Pixiv 回退客户端已初始化")

    # ============ 命令处理器 ============

    def _adapt(self, ctx: MatcherContext) -> EventAdapter:
        return EventAdapter(ctx, self)

    @staticmethod
    async def _collect(agen) -> str | list | None:
        """收集 async generator 的 yield 结果，转为 Qingci 返回值"""
        last = None
        async for result in agen:
            if result is not None:
                last = result_to_reply(result)
        return last

    async def cmd_p(self, ctx: MatcherContext):
        event = self._adapt(ctx)
        if not self._ensure_client_or_error(event):
            return "⚠️ 图片源暂不可用，请配置 Lolicon API，或填写 pixiv_refresh_token 作为回退"
        raw_query = str(ctx.args or "").strip()
        tag, count = self._split_tag_and_count(raw_query)
        return await self._collect(self._handle_search(event, tag=tag, count_str=count))

    async def cmd_checkin(self, ctx: MatcherContext):
        event = self._adapt(ctx)
        return await self._collect(self._handle_checkin(event))

    async def cmd_checkin_help(self, ctx: MatcherContext):
        event = self._adapt(ctx)
        if not CHECKIN_HELP_IMAGE.is_file():
            logger.error(f"{LOG_PREFIX} 签到帮助图片不存在: {CHECKIN_HELP_IMAGE}")
            return "签到帮助图片缺失，请联系管理员重新安装插件"
        return self._send_checkin_help_image(event)

    def _send_checkin_help_image(self, event: EventAdapter):
        from ._event import Image as _Image

        return _components_to_reply([_Image.fromFileSystem(str(CHECKIN_HELP_IMAGE))])

    # 签到我的
    async def cmd_checkin_status(self, ctx: MatcherContext):
        event = self._adapt(ctx)
        return await self._collect(self._handle_checkin_status(event))

    async def cmd_checkin_birthday_view(self, ctx: MatcherContext):
        event = self._adapt(ctx)
        return await self._handle_checkin_birthday(event, "查看", "")

    async def cmd_checkin_birthday_set(self, ctx: MatcherContext):
        event = self._adapt(ctx)
        return await self._handle_checkin_birthday(event, "设置", str(ctx.args or ""))

    async def cmd_checkin_birthday_clear(self, ctx: MatcherContext):
        event = self._adapt(ctx)
        return await self._handle_checkin_birthday(event, "清除", "")

    async def cmd_checkin_achievements(self, ctx: MatcherContext):
        event = self._adapt(ctx)
        return await self._handle_checkin_achievements(event)

    async def cmd_checkin_titles(self, ctx: MatcherContext):
        event = self._adapt(ctx)
        return await self._handle_checkin_titles(event)

    async def cmd_select_checkin_title(self, ctx: MatcherContext):
        event = self._adapt(ctx)
        return await self._handle_select_checkin_title(event, str(ctx.args or ""))

    # 签到排行
    async def cmd_checkin_ranking_today(self, ctx: MatcherContext):
        event = self._adapt(ctx)
        return await self._handle_checkin_ranking(event, "今日")

    async def cmd_checkin_ranking_month(self, ctx: MatcherContext):
        event = self._adapt(ctx)
        return await self._handle_checkin_ranking(event, "月榜")

    async def cmd_checkin_ranking_streak(self, ctx: MatcherContext):
        event = self._adapt(ctx)
        return await self._handle_checkin_ranking(event, "连签")

    async def cmd_checkin_ranking_total(self, ctx: MatcherContext):
        event = self._adapt(ctx)
        return await self._handle_checkin_ranking(event, "累计")

    # 签到商店
    async def cmd_checkin_shop(self, ctx: MatcherContext):
        self._adapt(ctx)
        if not self._cfg_bool("checkin_enabled", True):
            return "签到功能已关闭"
        return self._build_checkin_shop()

    async def cmd_buy_checkin_boost(self, ctx: MatcherContext):
        event = self._adapt(ctx)
        return await self._collect(self._handle_buy_checkin_boost(event, str(ctx.args or "")))

    async def cmd_checkin_themes(self, ctx: MatcherContext):
        event = self._adapt(ctx)
        return await self._handle_checkin_themes(event)

    async def cmd_preview_checkin_theme(self, ctx: MatcherContext):
        event = self._adapt(ctx)
        return await self._collect(self._handle_checkin_theme_preview(event, str(ctx.args or "")))

    async def cmd_buy_checkin_theme(self, ctx: MatcherContext):
        event = self._adapt(ctx)
        return await self._handle_buy_checkin_theme(event, str(ctx.args or ""))

    async def cmd_select_checkin_theme(self, ctx: MatcherContext):
        event = self._adapt(ctx)
        return await self._handle_select_checkin_theme(event, str(ctx.args or ""))

    async def cmd_refresh_checkin_background(self, ctx: MatcherContext):
        event = self._adapt(ctx)
        return await self._collect(self._handle_refresh_checkin_background(event))

    # 签到管理（管理员）
    async def cmd_checkin_preview(self, ctx: MatcherContext):
        event = self._adapt(ctx)
        return await self._collect(self._handle_checkin_preview(event))

    async def cmd_checkin_export(self, ctx: MatcherContext):
        event = self._adapt(ctx)
        result = await self._handle_checkin_export(event)
        if result is not None:
            return result_to_reply(result)
        return None

    async def cmd_checkin_event_list(self, ctx: MatcherContext):
        event = self._adapt(ctx)
        return await self._handle_checkin_event_admin(event, "", "", "", "")

    async def cmd_checkin_event_add(self, ctx: MatcherContext):
        event = self._adapt(ctx)
        return await self._handle_checkin_event_admin(event, "添加", str(ctx.args or ""), "", "")

    async def cmd_checkin_event_delete(self, ctx: MatcherContext):
        event = self._adapt(ctx)
        return await self._handle_checkin_event_admin(event, "删除", str(ctx.args or ""), "", "")

    # 正则触发
    async def checkin_auto_trigger(self, ctx: MatcherContext):
        if not self._cfg_bool("checkin_enabled", True):
            return None
        event = self._adapt(ctx)
        return await self._collect(self._handle_checkin(event, silent_when_disabled=True))

    async def auto_trigger(self, ctx: MatcherContext):
        if not self._cfg_bool("auto_trigger_enabled", False):
            return None
        event = self._adapt(ctx)
        if not self._ensure_client_or_error(event):
            return None
        message = ctx.plain_text.strip()
        match = re.match(AUTO_TRIGGER_PATTERN, message)
        if not match:
            return None
        count_part = match.group(2).strip() if match.group(2) else ""
        tag_part = (match.group(4) or "").strip()
        count_str = ""
        raw = count_part if count_part else "1"
        if raw.isdigit():
            count_str = raw
        else:
            for cn_digit, arabic in CHINESE_NUMBER_MAP.items():
                if raw == cn_digit:
                    count_str = arabic
                    break
            if not count_str:
                count_str = "1"
        logger.info(
            f"{LOG_PREFIX} 自然语言触发: count={count_str} "
            f"tag_configured={'yes' if tag_part else 'no'}"
        )
        return await self._collect(self._handle_search(event, tag=tag_part, count_str=count_str))

    # ============ 渲染（Stage 4：接入框架渲染器） ============

    async def _checkin_render_html(
        self, template_html: str, data: dict, *, options: dict | None = None
    ):
        """渲染签到 HTML 卡为图片；渲染能力不可用时返回 None（上层降级纯文本）

        template_html 为 get_checkin_card_template() 产出的完整 HTML 字符串
        （CSS 内联 + 字体 base64 + season artwork SVG 已嵌入），仅需 jinja2
        填充变量后交给框架 HtmlRenderer 渲染。
        """
        try:
            html = _compile_checkin_template(template_html).render(data)
            renderer = getattr(self.bot, "html_renderer", None) if self.bot else None
            if renderer is None or not getattr(renderer, "is_supported", lambda: False)():
                logger.debug(f"{LOG_PREFIX} HTML 渲染能力不可用，签到卡降级纯文本")
                return None
            opts = dict(options or {})
            viewport = opts.get("viewport") or {}
            result_path = await renderer.render_html(
                html,
                width=int(viewport.get("width") or 960),
                height=int(viewport.get("height") or 540),
                image_format=str(opts.get("type") or "jpeg"),
                quality=int(opts.get("quality") or 90),
                timeout=30.0,
            )
            return str(result_path)
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 签到卡渲染失败，降级纯文本: {type(exc).__name__}")
            return None

    # ============ 工具方法 ============

    @staticmethod
    def _split_tag_and_count(query: str) -> tuple[str, str]:
        tokens = query.split()
        if not tokens:
            return "", ""
        if tokens[-1].isdigit():
            return " ".join(tokens[:-1]), tokens[-1]
        return " ".join(tokens), ""

    def _check_rate_limit(self, user_id: str) -> int:
        rate_limit = self._cfg_int("rate_limit_seconds", 3, 0, 60)
        if rate_limit <= 0:
            return 0
        now = time.monotonic()
        if len(self._last_request) > 1024:
            cutoff = now - max(float(rate_limit) * 2, 60.0)
            self._last_request = {
                key: timestamp
                for key, timestamp in self._last_request.items()
                if timestamp >= cutoff
            }
        last = self._last_request.get(user_id, 0.0)
        elapsed = now - last
        if elapsed < rate_limit:
            return int(rate_limit - elapsed) + 1
        self._last_request[user_id] = now
        return 0

    def _checkin_flow_lock(self, user_id: str) -> asyncio.Lock:
        locks = getattr(self, "_checkin_flow_locks", None)
        if locks is None:
            locks = {}
            self._checkin_flow_locks = locks
        lock = locks.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            locks[user_id] = lock
            if len(locks) > 1024:
                # 容量上限：仅逐出当前未被持有的锁，防止长时间运行的实例
                # 内存无限增长，同时不破坏在途会话的串行保证
                stale_ids = [key for key, item in locks.items() if not item.locked()][:256]
                for stale_id in stale_ids:
                    locks.pop(stale_id, None)
        return lock

    # ============ 配置读取（带类型校验） ============

    def _migrate_dedupe_config(self) -> int:
        config = getattr(self, "config", None)
        if config is None:
            return 1
        if not self._cfg_bool("dedupe_days_migrated", False):
            legacy_value = self._cfg_float("dedupe_ttl_hours", 24.0, 0.0, 24.0)
            config["dedupe_days"] = 0 if legacy_value <= 0 else 1
            config["dedupe_days_migrated"] = True
            if self.settings is not None:
                self.settings.dedupe_days = config["dedupe_days"]
                self.settings.dedupe_days_migrated = True
            logger.info(
                f"{LOG_PREFIX} 已迁移旧去重配置: dedupe_ttl_hours={legacy_value:g} -> "
                f"dedupe_days={config['dedupe_days']}"
            )
        return self._cfg_int("dedupe_days", 1, 0, 7)

    def _settings_value(self, key: str, default: Any) -> Any:
        """优先读取类型化配置快照；未配置时回退 self.config（兼容测试等未调 on_load 的场景）。"""
        settings = getattr(self, "settings", None)
        if settings is not None and hasattr(settings, key):
            value = getattr(settings, key)
            if value is not None:
                return value
        return self.config.get(key, default)

    def _cfg_str(self, key: str, default: str = "") -> str:
        val = self._settings_value(key, default)
        return str(val).strip() if val is not None else default

    def _cfg_int(self, key: str, default: int, lo: int, hi: int) -> int:
        raw = self._settings_value(key, default)
        if isinstance(raw, (bool, float)):
            return default
        try:
            val = int(raw)
        except (TypeError, ValueError):
            return default
        return val if lo <= val <= hi else default

    def _forward_threshold(self) -> int:
        if "forward_threshold" in self.config:
            return self._cfg_int("forward_threshold", 1, 0, MAX_IMAGE_COUNT)
        return 0 if self._cfg_bool("send_as_forward", True) else MAX_IMAGE_COUNT

    def _cfg_float(self, key: str, default: float, lo: float, hi: float) -> float:
        raw = self._settings_value(key, default)
        try:
            val = float(raw)
        except (TypeError, ValueError):
            return default
        return val if lo <= val <= hi else default

    def _cfg_bool(self, key: str, default: bool) -> bool:
        val = self._settings_value(key, default)
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.lower() in ("true", "1", "yes")
        return bool(val) if val is not None else default


def _components_to_reply(content: list) -> list[dict]:
    from ._event import components_to_segments

    return list(components_to_segments(content))
