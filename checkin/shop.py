from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .._event import EventAdapter, Image, Plain
from .card import CardBackground
from .models import (
    BOOST_PRODUCTS,
    MAKEUP_CARD_PRICE,
    MONTHLY_CARD_PRICE,
)
from .rules import boost_status_text
from .store import CheckinStore
from .themes import (
    CHECKIN_THEMES,
    DEFAULT_CHECKIN_THEME_ID,
    get_checkin_theme,
    resolve_checkin_theme,
)

try:
    from ..pixiv.downloader import cleanup
except ImportError:  # Direct imports used by the test suite.
    from pixiv.downloader import cleanup

logger = logging.getLogger(__name__)


LOG_PREFIX = "[GetPx]"
PLUGIN_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class CheckinShopItem:
    """A displayable shop entry with a stable identifier."""

    item_id: str
    category: str
    command: str
    name: str
    price: int

    @property
    def price_label(self) -> str:
        return "免费" if self.price <= 0 else f"{self.price} 金币"

    def render_line(self) -> str:
        detail = f"{self.name}，" if self.name else ""
        return f"{self.command} - {detail}{self.price_label}"


def build_checkin_shop_items(
    refresh_cost: int,
    theme_cost: int = 1500,
) -> tuple[CheckinShopItem, ...]:
    """Build the current catalog; add future products in this one registry."""
    items = [
        CheckinShopItem(
            item_id=f"boost:{days}",
            category="boost",
            command=f"签到商店 加持 {days}",
            name=f"{days} 天",
            price=cost,
        )
        for days, cost in BOOST_PRODUCTS.items()
    ]
    items.append(
        CheckinShopItem(
            item_id="background:refresh",
            category="background",
            command="签到商店 刷新背景",
            name="",
            price=max(0, int(refresh_cost)),
        )
    )
    items.append(
        CheckinShopItem(
            item_id="makeup_card",
            category="items",
            command="签到商店 购买补签卡",
            name="补签卡（补签最近一天）",
            price=MAKEUP_CARD_PRICE,
        )
    )
    items.append(
        CheckinShopItem(
            item_id="monthly_card",
            category="items",
            command="签到商店 购买月卡",
            name="月卡（30 天金币双倍）",
            price=MONTHLY_CARD_PRICE,
        )
    )
    items.extend(
        CheckinShopItem(
            item_id=f"theme:{theme.theme_id}",
            category="theme",
            command=f"签到商店 主题购买 {theme.code}",
            name=theme.name,
            price=(0 if theme.theme_id == DEFAULT_CHECKIN_THEME_ID else max(0, int(theme_cost))),
        )
        for theme in CHECKIN_THEMES.values()
        if theme.enabled
    )
    return tuple(items)


class CheckinShopMixin:
    """Shop catalog, purchases, theme selection and paid background refresh."""

    async def _handle_buy_checkin_boost(self, event: EventAdapter, days: str):
        if not self._cfg_bool("checkin_enabled", True):
            yield event.plain_result("签到功能已关闭")
            return
        if self.checkin_store is None:
            yield event.plain_result("签到数据尚未初始化，请稍后再试")
            return
        if not days or not days.isdigit():
            yield event.plain_result("用法: 签到商店 加持 <1|3|7>\n示例: 签到商店 加持 3")
            return
        user_id = str(event.get_sender_id() or "")
        if not user_id:
            yield event.plain_result("无法识别用户 ID，暂时不能购买加持")
            return
        try:
            purchase = await self.checkin_store.purchase_boost(
                user_id=user_id,
                days=int(days),
            )
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} 购买签到加持失败: error_type={type(exc).__name__}")
            yield event.plain_result("购买失败，请稍后再试")
            return
        lines = [purchase.message, f"当前金币: {purchase.profile.coins}"]
        if purchase.success:
            lines.append(
                f"好感度加持: {boost_status_text(purchase.profile, CheckinStore.today_key())}"
            )
        yield event.plain_result("\n".join(lines))

    def _build_checkin_shop(self) -> str:
        refresh_cost = self._cfg_int("checkin_background_refresh_cost", 100, 0, 500)
        theme_cost = self._cfg_int("checkin_theme_cost", 1500, 0, 5000)
        lines = [
            "签到商店",
            "金币可购买好感度加持、更新当天背景和解锁签到主题。",
        ]
        lines.extend(
            item.render_line() for item in build_checkin_shop_items(refresh_cost, theme_cost)
        )
        lines.append("使用“签到商店 主题查看 <编号>”预览，“签到商店 主题列表”查看购买状态")
        lines.append("已购买主题可使用“签到商店 主题切换 <编号>”切换")
        return "\n".join(lines)

    async def _handle_checkin_themes(self, event: EventAdapter) -> str:
        if self.checkin_store is None:
            return "签到数据尚未初始化，请稍后再试"
        user_id = str(event.get_sender_id() or "")
        preference = await self.checkin_store.get_user_preference(user_id)
        owned = set(await self.checkin_store.list_owned_theme_ids(user_id))
        current = get_checkin_theme(preference.current_theme_id)
        lines = [f"签到主题（当前：{current.name}）"]
        for theme in CHECKIN_THEMES.values():
            if not theme.enabled:
                continue
            if theme.theme_id == current.theme_id:
                state = "当前"
            elif theme.theme_id in owned:
                state = "已购"
            else:
                state = "未购"
            lines.append(f"[{state}] {theme.code} · {theme.name} - {theme.description}")
        lines.append("使用“签到商店 主题查看 <编号>”预览，“签到商店 主题购买 <编号>”购买")
        lines.append("已购买主题可使用“签到商店 主题切换 <编号>”切换")
        return "\n".join(lines)

    async def _handle_checkin_theme_preview(self, event: EventAdapter, value: str):
        theme = resolve_checkin_theme(value)
        if theme is None:
            return event.plain_result("用法：签到商店 主题查看 <编号>\n示例：签到商店 主题查看 1")
        preview_path = theme.preview_path(PLUGIN_ROOT)
        if not preview_path.is_file():
            logger.error(
                f"{LOG_PREFIX} 签到主题预览图不存在: "
                f"theme_id={theme.theme_id} file={preview_path.name}"
            )
            return event.plain_result("主题预览图缺失，请联系管理员重新安装插件")
        return event.chain_result(
            [
                Plain(f"主题预览：{theme.code} · {theme.name}\n{theme.description}"),
                Image.fromFileSystem(str(preview_path)),
            ]
        )

    async def _handle_buy_checkin_theme(self, event: EventAdapter, value: str) -> str:
        if self.checkin_store is None:
            return "签到数据尚未初始化，请稍后再试"
        theme = resolve_checkin_theme(value)
        if theme is None:
            return "未知主题。使用“签到商店 主题列表”查看主题编号。"
        if theme.theme_id == DEFAULT_CHECKIN_THEME_ID:
            return await self._handle_select_checkin_theme(event, theme.theme_id)
        user_id = str(event.get_sender_id() or "")
        theme_cost = self._cfg_int("checkin_theme_cost", 1500, 0, 5000)
        try:
            purchase = await self.checkin_store.purchase_theme(
                user_id=user_id,
                theme_id=theme.theme_id,
                cost=theme_cost,
            )
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} 购买签到主题失败: error_type={type(exc).__name__}")
            return "购买主题失败，请稍后再试"
        return "\n".join(
            [
                purchase.message,
                f"主题: {theme.name}",
                f"当前金币: {purchase.profile.coins}",
                "今天已经签到时，可重新发送“签到”查看新主题。",
            ]
        )

    async def _handle_select_checkin_theme(self, event: EventAdapter, value: str) -> str:
        if self.checkin_store is None:
            return "签到数据尚未初始化，请稍后再试"
        theme = resolve_checkin_theme(value)
        if theme is None:
            return "未知主题。使用“签到商店 主题列表”查看主题编号。"
        user_id = str(event.get_sender_id() or "")
        try:
            await self.checkin_store.select_theme(
                user_id=user_id,
                theme_id=theme.theme_id,
            )
        except ValueError as exc:
            return str(exc)
        return f"已切换签到主题：{theme.name}\n今天已经签到时，可重新发送“签到”查看新主题。"

    async def _handle_refresh_checkin_background(
        self,
        event: EventAdapter,
        *,
        _flow_locked: bool = False,
    ):
        if not self._cfg_bool("checkin_enabled", True):
            yield event.plain_result("签到功能已关闭")
            return
        if self.checkin_store is None:
            yield event.plain_result("签到数据尚未初始化，请稍后再试")
            return
        if self._cfg_str("checkin_background_mode", "pixiv_daily") != "pixiv_daily":
            yield event.plain_result("只有在线每日背景模式支持付费更新背景")
            return
        user_id = str(event.get_sender_id() or "")
        if not user_id:
            yield event.plain_result("无法识别用户 ID")
            return
        if not _flow_locked:
            lock = self._checkin_flow_lock(user_id)
            async with lock:
                outputs = [
                    item
                    async for item in self._handle_refresh_checkin_background(
                        event, _flow_locked=True
                    )
                ]
            for output in outputs:
                yield output
            return

        record = await self.checkin_store.get_today_record(user_id)
        if record is None:
            yield event.plain_result("请先完成今天的签到，再更新背景")
            return
        cost = self._cfg_int("checkin_background_refresh_cost", 100, 0, 500)
        profile = await self.checkin_store.get_profile(user_id)
        if profile.coins < cost:
            yield event.plain_result(f"金币不足，需要 {cost}，当前只有 {profile.coins}。")
            return

        background: CardBackground | None = None
        claim_held = False
        cache = getattr(self, "checkin_cache", None)
        card_path: Path | None = None
        refresh_tier = self._configured_checkin_render_tier()
        try:
            background = await self._prepare_checkin_background(
                event,
                record,
                refresh_preview=True,
                render_tier=refresh_tier,
            )
            claim_held = bool(
                background is not None and background.mode == "pixiv_daily" and background.illust_id
            )
            if (
                not claim_held
                or background is None
                or background.illust_id == record.background_illust_id
            ):
                yield event.plain_result("暂时没有找到新的合适背景，本次不扣金币")
                return
            purchase = await self.checkin_store.purchase_background_refresh(
                user_id=user_id,
                cost=cost,
                mode=background.mode,
                source=background.source,
                illust_id=background.illust_id,
                title=background.title,
                author=background.author,
                quality=background.quality,
            )
            if not purchase.success or purchase.record is None:
                yield event.plain_result(purchase.message)
                return

            record = purchase.record
            profile = purchase.profile
            record = await self._refresh_checkin_hitokoto(event, record)
            bot_name = self._cfg_str("checkin_bot_name", "neko") or "neko"
            user_title = await self._get_checkin_user_title(user_id)
            card_path, actual_tier = await self._render_checkin_card_with_fallback(
                event,
                profile=profile,
                record=record,
                background=background,
                bot_name=bot_name,
                user_title=user_title,
                preferred_tier=refresh_tier,
                cache=cache,
            )
            record = await self._persist_checkin_render_tier(record, actual_tier)
            content = [Plain(purchase.message), Image.fromFileSystem(str(card_path))]
            if background.pixiv_caption:
                content.append(Plain(background.pixiv_caption))
            await event.send(event.chain_result(content))
            try:
                await self._record_checkin_background(event, background)
            except Exception as exc:
                logger.warning(
                    f"{LOG_PREFIX} 记录签到背景使用状态失败: error_type={type(exc).__name__}"
                )
            else:
                claim_held = False
            return
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} 更新签到背景失败: error_type={type(exc).__name__}")
            yield event.plain_result(
                "更新背景失败；若金币已经扣除，重新发送“签到”可查看已保存的新背景"
            )
        finally:
            if claim_held:
                await self._release_checkin_background_claim(event, background)
            if cache is None and card_path is not None:
                cleanup(str(card_path))
            if (
                background is not None
                and background.image_path
                and background.mode == "pixiv_daily"
            ):
                cleanup(background.image_path)

    async def _handle_buy_makeup_card(self, event: EventAdapter):
        if not self._cfg_bool("checkin_enabled", True):
            yield event.plain_result("签到功能已关闭")
            return
        if self.checkin_store is None:
            yield event.plain_result("签到数据尚未初始化，请稍后再试")
            return
        user_id = str(event.get_sender_id() or "")
        if not user_id:
            yield event.plain_result("无法识别用户 ID，暂时不能购买补签卡")
            return
        try:
            purchase = await self.checkin_store.purchase_makeup_card(user_id=user_id)
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} 购买补签卡失败: error_type={type(exc).__name__}")
            yield event.plain_result("购买失败，请稍后再试")
            return
        if not purchase.success:
            yield event.plain_result(purchase.message)
            return
        yield event.plain_result(f"已购买补签卡，当前持有 {purchase.count} 张")

    async def _handle_use_makeup_card(self, event: EventAdapter):
        if not self._cfg_bool("checkin_enabled", True):
            yield event.plain_result("签到功能已关闭")
            return
        if self.checkin_store is None:
            yield event.plain_result("签到数据尚未初始化，请稍后再试")
            return
        user_id = str(event.get_sender_id() or "")
        if not user_id:
            yield event.plain_result("无法识别用户 ID，暂时不能使用补签卡")
            return
        try:
            result = await self.checkin_store.use_makeup_card(user_id=user_id)
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} 使用补签卡失败: error_type={type(exc).__name__}")
            yield event.plain_result("补签失败，请稍后再试")
            return
        message = result.message
        if result.success:
            message += f"\n当前补签卡: {result.profile.makeup_cards} 张"
        yield event.plain_result(message)

    async def _handle_buy_monthly_card(self, event: EventAdapter):
        if not self._cfg_bool("checkin_enabled", True):
            yield event.plain_result("签到功能已关闭")
            return
        if self.checkin_store is None:
            yield event.plain_result("签到数据尚未初始化，请稍后再试")
            return
        user_id = str(event.get_sender_id() or "")
        if not user_id:
            yield event.plain_result("无法识别用户 ID，暂时不能购买月卡")
            return
        try:
            purchase = await self.checkin_store.purchase_monthly_card(user_id=user_id)
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} 购买月卡失败: error_type={type(exc).__name__}")
            yield event.plain_result("购买失败，请稍后再试")
            return
        yield event.plain_result(
            "\n".join([purchase.message, f"当前金币: {purchase.profile.coins}"])
        )

    async def _handle_send_flower(self, event: EventAdapter, target: str):
        if not self._cfg_bool("checkin_enabled", True):
            yield event.plain_result("签到功能已关闭")
            return
        if self.checkin_store is None:
            yield event.plain_result("签到数据尚未初始化，请稍后再试")
            return
        user_id = str(event.get_sender_id() or "")
        if not user_id:
            yield event.plain_result("无法识别用户 ID，暂时不能送花")
            return
        target_id = str(target or "").strip().lstrip("@")
        if not target_id:
            yield event.plain_result("用法: 签到商店 送花 <@用户>")
            return
        try:
            result = await self.checkin_store.send_flower(user_id=user_id, target_id=target_id)
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} 送花失败: error_type={type(exc).__name__}")
            yield event.plain_result("送花失败，请稍后再试")
            return
        yield event.plain_result(result.message)

    async def _handle_transfer_coins(self, event: EventAdapter, target: str, amount: str):
        if not self._cfg_bool("checkin_enabled", True):
            yield event.plain_result("签到功能已关闭")
            return
        if self.checkin_store is None:
            yield event.plain_result("签到数据尚未初始化，请稍后再试")
            return
        user_id = str(event.get_sender_id() or "")
        if not user_id:
            yield event.plain_result("无法识别用户 ID，暂时不能转账")
            return
        target_id = str(target or "").strip().lstrip("@")
        if not target_id:
            yield event.plain_result("用法: 签到商店 转账 <@用户> <金币数>")
            return
        amount_text = str(amount or "").strip()
        if not amount_text.isdigit() or not 1 <= int(amount_text) <= 100_000:
            yield event.plain_result("转账金额必须是 1 至 100000 之间的整数")
            return
        try:
            result = await self.checkin_store.transfer_coins(
                user_id=user_id,
                target_id=target_id,
                amount=int(amount_text),
            )
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} 金币转账失败: error_type={type(exc).__name__}")
            yield event.plain_result("转账失败，请稍后再试")
            return
        yield event.plain_result(result.message)

    async def _handle_checkin_bond(self, event: EventAdapter, target: str):
        if not self._cfg_bool("checkin_enabled", True):
            yield event.plain_result("签到功能已关闭")
            return
        if self.checkin_store is None:
            yield event.plain_result("签到数据尚未初始化，请稍后再试")
            return
        user_id = str(event.get_sender_id() or "")
        if not user_id:
            yield event.plain_result("无法识别用户 ID，暂时不能查询羁绊")
            return
        target_id = str(target or "").strip().lstrip("@")
        if not target_id:
            yield event.plain_result("用法: 签到商店 羁绊 <@用户>")
            return
        try:
            mutual_days = await self.checkin_store.get_mutual_days(user_id, target_id)
            result = await self.checkin_store.claim_bond_reward(user_id, target_id)
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} 查询羁绊失败: error_type={type(exc).__name__}")
            yield event.plain_result("羁绊查询失败，请稍后再试")
            return
        yield event.plain_result(f"当前共同签到 {mutual_days} 天。\n{result}")
