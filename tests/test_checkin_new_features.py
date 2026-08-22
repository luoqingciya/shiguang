"""新功能（A1/B1/C1/A2/A3/A4/C2/D5/E1/D2）存储层测试

覆盖：补签卡/月卡、送花/转账/羁绊、收藏、赛季排行与结算、打卡日历、
幸运日加成、每日一图订阅、定时提醒订阅、审计日志、签到运势。
"""

from __future__ import annotations

import tempfile

import pytest

from checkin import CheckinStore
from checkin.fortune import generate_fortune
from checkin.holiday_tags import festival_tag_for
from checkin.models import (
    BOND_REWARD_COINS,
    GIFT_COST,
    LUCKY_BONUS_COINS,
    MAKEUP_CARD_GRANT_COINS,
    MAKEUP_CARD_PRICE,
    MONTHLY_CARD_PRICE,
    CheckinProfile,
)
from checkin.season_store import season_bounds


class FrozenCheckinStore(CheckinStore):
    def __init__(self, data_dir: str, *, date_key: str):
        self.date_key = date_key
        super().__init__(data_dir)

    def today_key(self) -> str:
        return self.date_key

    def now_iso(self) -> str:
        return f"{self.date_key}T12:00:00+08:00"


def _profile(**overrides) -> CheckinProfile:
    defaults = {
        "user_id": "10001",
        "coins": 0,
        "affection": 0.0,
        "total_days": 0,
        "streak_days": 0,
        "last_checkin_date": "",
        "boost_start_date": "",
        "boost_until_date": "",
        "repeat_penalty_date": "",
        "repeat_penalty_total": 0.0,
        "created_at": "2026-07-01T12:00:00+08:00",
        "updated_at": "2026-07-01T12:00:00+08:00",
    }
    defaults.update(overrides)
    return CheckinProfile(**defaults)


async def _seed_coins(store: CheckinStore, user_id: str, coins: int) -> None:
    await store.update_checkin_member(
        user_id=user_id,
        coins=coins,
        affection=0.0,
        total_days=0,
        streak_days=0,
    )


# ============ A1 补签卡 / 月卡 ============


@pytest.mark.asyncio
async def test_purchase_makeup_card_requires_coins() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = FrozenCheckinStore(tmp, date_key="2026-07-20")
        await store.checkin(user_id="10001", username="Alice", bot_name="neko")
        await _seed_coins(store, "10001", 10)
        purchase = await store.purchase_makeup_card(user_id="10001")
        assert purchase.success is False
        assert "金币不足" in purchase.message
        profile = await store.get_profile("10001")
        assert profile.makeup_cards == 0


@pytest.mark.asyncio
async def test_purchase_and_use_makeup_card_backfills_missing_day() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = FrozenCheckinStore(tmp, date_key="2026-07-19")
        await store.checkin(user_id="10001", username="Alice", bot_name="neko")
        store.date_key = "2026-07-20"
        await _seed_coins(store, "10001", 500)
        # 保留签到产生的 total/streak（_seed_coins 会覆盖）
        await store.update_checkin_member(
            user_id="10001",
            coins=500,
            affection=0.0,
            total_days=1,
            streak_days=1,
        )

        purchase = await store.purchase_makeup_card(user_id="10001")
        assert purchase.success
        assert purchase.count == 1

        # 已签 07-19，今天 07-20 未签：补签最近缺失的历史日 07-18
        result = await store.use_makeup_card(user_id="10001")
        assert result.success
        assert result.date_key == "2026-07-18"
        profile = await store.get_profile("10001")
        assert profile.makeup_cards == 0
        assert profile.coins == 500 - MAKEUP_CARD_PRICE + MAKEUP_CARD_GRANT_COINS
        # 补签不破坏 total / streak
        assert profile.total_days == 1
        assert profile.streak_days == 1


@pytest.mark.asyncio
async def test_monthly_card_doubles_checkin_coins() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = FrozenCheckinStore(tmp, date_key="2026-07-20")
        await store.checkin(user_id="10001", username="Alice", bot_name="neko")
        await _seed_coins(store, "10001", 1000)

        purchase = await store.purchase_monthly_card(user_id="10001")
        assert purchase.success
        assert purchase.monthly_until
        profile = await store.get_profile("10001")
        assert profile.coins == 1000 - MONTHLY_CARD_PRICE
        assert profile.monthly_card_until >= "2026-07-20"

        # 次日签到：金币奖励翻倍
        store.date_key = "2026-07-21"
        doubled = await store.checkin(user_id="10001", username="Alice", bot_name="neko")
        assert doubled.record is not None
        assert doubled.record.coins_reward == 2 * (
            doubled.record.base_coins + doubled.record.bonus_coins
        )
        assert "月卡" in doubled.record.note


# ============ B1 送花 / 转账 / 羁绊 ============


@pytest.mark.asyncio
async def test_send_flower_transfers_affection_and_costs_coins() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = FrozenCheckinStore(tmp, date_key="2026-07-20")
        await store.checkin(user_id="10001", username="Alice", bot_name="neko")
        await store.checkin(user_id="10002", username="Bob", bot_name="neko")
        await _seed_coins(store, "10001", 200)
        target_before = (await store.get_profile("10002")).affection

        result = await store.send_flower(user_id="10001", target_id="10002")
        assert result.success
        assert result.affection > 0
        assert (await store.get_profile("10001")).coins == 200 - GIFT_COST
        target_after = (await store.get_profile("10002")).affection
        assert round(target_after - target_before, 2) == result.affection


@pytest.mark.asyncio
async def test_transfer_coins_moves_balance() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = FrozenCheckinStore(tmp, date_key="2026-07-20")
        await store.checkin(user_id="10001", username="Alice", bot_name="neko")
        await store.checkin(user_id="10002", username="Bob", bot_name="neko")
        await _seed_coins(store, "10001", 500)
        target_before = (await store.get_profile("10002")).coins

        result = await store.transfer_coins(user_id="10001", target_id="10002", amount=120)
        assert result.success
        assert (await store.get_profile("10001")).coins == 380
        assert (await store.get_profile("10002")).coins == target_before + 120

        failed = await store.transfer_coins(user_id="10001", target_id="10002", amount=9999)
        assert failed.success is False


@pytest.mark.asyncio
async def test_bond_reward_after_three_mutual_days() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = FrozenCheckinStore(tmp, date_key="2026-07-20")
        for offset in range(3):
            store.date_key = f"2026-07-{20 + offset:02d}"
            await store.checkin(
                user_id="10001", username="Alice", bot_name="neko", group_id="20001"
            )
            await store.checkin(user_id="10002", username="Bob", bot_name="neko", group_id="20001")

        assert await store.get_mutual_days("10001", "10002") >= 3
        message = await store.claim_bond_reward("10001", "10002")
        assert "羁绊" in message and "已解锁" in message
        assert (await store.get_profile("10001")).coins >= BOND_REWARD_COINS
        again = await store.claim_bond_reward("10001", "10002")
        assert "已领取" in again


# ============ C1 收藏 / 画廊 ============


@pytest.mark.asyncio
async def test_favorite_add_list_remove() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = FrozenCheckinStore(tmp, date_key="2026-07-20")
        await store.checkin(user_id="10001", username="Alice", bot_name="neko")

        assert await store.add_favorite(
            user_id="10001", illust_id="123456", title="示例", author="画师A"
        )
        assert await store.is_favorite(user_id="10001", illust_id="123456")
        assert await store.add_favorite(user_id="10001", illust_id="654321", title="示例2")
        listing = await store.list_favorites(user_id="10001")
        assert listing["total"] == 2
        filtered = await store.list_favorites(user_id="10001", author="画师A")
        assert filtered["total"] == 1
        assert await store.remove_favorite(user_id="10001", illust_id="123456")
        assert not await store.is_favorite(user_id="10001", illust_id="123456")


# ============ A2 赛季排行 + 结算 ============


def test_season_bounds_is_deterministic_window() -> None:
    start, end, key = season_bounds()
    assert (end - start).days == 29
    assert key == start.isoformat()
    same_start, _same_end, same_key = season_bounds()
    assert same_key == key


@pytest.mark.asyncio
async def test_season_ranking_and_settle_are_idempotent() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        start, _end, _key = season_bounds()
        store = FrozenCheckinStore(tmp, date_key=start.isoformat())
        await store.checkin(user_id="10001", username="Alice", bot_name="neko", group_id="20001")
        await store.checkin(user_id="10002", username="Bob", bot_name="neko", group_id="20001")
        await _seed_coins(store, "10001", 100)
        await _seed_coins(store, "10002", 100)

        ranking = await store.get_season_ranking(group_id="20001", limit=10)
        assert ranking["type"] == "season"
        assert ranking["entries"]
        assert ranking["entries"][0]["user_id"] == "10001"

        first = await store.settle_season("20001")
        assert first["already_settled"] is False
        assert any(item["rank"] == 1 for item in first["payouts"])
        assert (await store.get_profile("10001")).coins >= 100 + 200

        second = await store.settle_season("20001")
        assert second["already_settled"] is True
        assert second["payouts"] == []


# ============ A3 打卡日历 ============


@pytest.mark.asyncio
async def test_month_calendar_marks_signed_days() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = FrozenCheckinStore(tmp, date_key="2026-07-20")
        await store.checkin(user_id="10001", username="Alice", bot_name="neko")
        calendar = await store.get_month_calendar(user_id="10001", year=2026, month=7)
        assert calendar["total_days"] == 31
        assert calendar["signed_days"] == 1
        signed = [day for day in calendar["days"] if day["signed"]]
        assert signed and signed[0]["day"] == 20


# ============ A4 幸运日加成 ============


def test_lucky_day_tail_matching() -> None:
    from checkin.season_store import SeasonStoreMixin

    # 日期尾号 28 → 尾字符 8 命中幸运尾号
    assert SeasonStoreMixin.is_lucky_day("10001", "200016", "2026-07-28")
    # 日期尾号 25 → 尾字符 5 未命中
    assert not SeasonStoreMixin.is_lucky_day("10001", "200016", "2026-07-25")


@pytest.mark.asyncio
async def test_award_lucky_bonus_updates_coins_and_ledger() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = FrozenCheckinStore(tmp, date_key="2026-07-20")
        await store.checkin(user_id="10001", username="Alice", bot_name="neko")
        before = (await store.get_profile("10001")).coins
        new_coins = await store.award_lucky_bonus(
            user_id="10001", date_key="2026-07-20", coins=LUCKY_BONUS_COINS, note="幸运签"
        )
        assert new_coins == before + LUCKY_BONUS_COINS
        record = await store.get_today_record("10001")
        assert record is not None and "幸运签" in record.secondary_note


# ============ C2 每日一图订阅 ============


@pytest.mark.asyncio
async def test_group_subscription_crud_and_push_match() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = FrozenCheckinStore(tmp, date_key="2026-07-20")
        subscription = await store.upsert_group_subscription(
            group_id="20001",
            group_name="Group A",
            tag="风景",
            push_time="09:00",
            weekdays="1,3,5",
        )
        assert subscription.enabled
        assert subscription.tag == "风景"

        fetched = await store.get_group_subscription("20001")
        assert fetched is not None and fetched.push_time == "09:00"

        updated = await store.set_subscription_tag("20001", "少女")
        assert updated is not None and updated.tag == "少女"
        await store.set_subscription_enabled("20001", False)
        assert (await store.get_group_subscription("20001")).enabled is False
        assert await store.list_group_subscriptions(enabled_only=True) == []

        # push_time / weekday 命中匹配（周一=1，周三=3）
        await store.set_subscription_enabled("20001", True)
        matched = await store.get_subscriptions_for_push(weekday=1, current_time="09:00")
        assert [item.group_id for item in matched] == ["20001"]
        not_matched = await store.get_subscriptions_for_push(weekday=2, current_time="09:00")
        assert not_matched == []
        time_miss = await store.get_subscriptions_for_push(weekday=1, current_time="10:00")
        assert time_miss == []


# ============ D5 定时提醒订阅 ============


@pytest.mark.asyncio
async def test_group_reminder_crud() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = FrozenCheckinStore(tmp, date_key="2026-07-20")
        reminder = await store.upsert_group_reminder(
            group_id="20001", group_name="Group A", remind_time="21:00"
        )
        assert reminder.enabled and reminder.remind_time == "21:00"
        assert (await store.get_group_reminder("20001")).remind_time == "21:00"

        await store.upsert_group_reminder(group_id="20001", enabled=False)
        assert (await store.get_group_reminder("20001")).enabled is False
        assert await store.list_group_reminders(enabled_only=True) == []


# ============ E1 审计日志 ============


@pytest.mark.asyncio
async def test_audit_log_record_and_filter() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = FrozenCheckinStore(tmp, date_key="2026-07-20")
        await store.record_audit(operator="admin", action="checkin.event_add", target="2026-08-01")
        await store.record_audit(operator="admin", action="checkin.event_delete", target="3")

        listing = await store.list_audit()
        assert listing["total"] == 2
        add_only = await store.list_audit(action="checkin.event_add")
        assert add_only["total"] == 1
        operator_only = await store.list_audit(operator="admin")
        assert operator_only["total"] == 2


# ============ D2 签到运势 ============


def test_fortune_is_stable_per_user_and_date() -> None:
    profile = _profile(user_id="10001")
    first = generate_fortune(profile, "2026-07-20")
    second = generate_fortune(profile, "2026-07-20")
    assert first == second
    assert first.startswith("今日运势")
    other_date = generate_fortune(profile, "2026-07-21")
    assert other_date != first


def test_fortune_empty_without_profile() -> None:
    assert generate_fortune(_profile(user_id=""), "2026-07-20") == ""


# ============ D3 统计聚合 ============


@pytest.mark.asyncio
async def test_checkin_stats_aggregates() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = FrozenCheckinStore(tmp, date_key="2026-07-20")
        await store.checkin(user_id="10001", username="Alice", bot_name="neko", group_id="20001")
        stats = await store.get_checkin_stats()
        assert stats["total_users"] == 1
        assert stats["total_records"] == 1
        assert stats["total_groups"] == 1
        assert stats["dau_7"] == 1
        assert stats["week_checkin_rate"] == 100.0


# ============ C3 节日图集 ============


def test_festival_tag_mapping() -> None:
    # 在线节假日名称命中
    assert festival_tag_for("2026-02-17", "春节") == "新年"
    assert festival_tag_for("2026-09-25", "中秋节") == "中秋"
    assert festival_tag_for("2026-10-01", "国庆节") == "国庆"
    # 日期特判兜底（非国假）
    assert festival_tag_for("2026-12-25", "") == "圣诞"
    assert festival_tag_for("2026-02-14", "") == "情人节"
    # 未命中返回空串
    assert festival_tag_for("2026-07-20", "正常工作日") == ""
    assert festival_tag_for("2026-07-20", "") == ""
    assert festival_tag_for("invalid", "") == ""


# ============ E3 统计报表 CSV ============


def test_report_csv_render() -> None:
    from shiguang.plugin_api.api import _render_report_csv

    csv_text = _render_report_csv(
        [
            {
                "group_id": "20001",
                "group_name": "群A",
                "platform": "aiocqhttp",
                "today_checkins": 3,
                "month_checkins": 80,
                "avg_daily_active": 4.0,
                "total_checkins": 30,
                "season_top1": "Alice",
            }
        ],
        days=7,
    )
    lines = csv_text.strip().splitlines()
    assert lines[0].startswith("群号")
    assert "20001" in lines[1]
    assert "Alice" in lines[1]
