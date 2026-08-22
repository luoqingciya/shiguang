"""D1 LLM 签到助手 — 插件级 Function Calling 工具

模块级 @llm_tool 由 Qingci PluginManager 在插件加载时自动收集，
工具注册名自动加插件前缀（shiguang_get_user_checkin_stats 等）。
store 引用由插件在 _initialize 时注入（模块级全局），仅用于只读查询，
不写入任何数据，避免 LLM 工具链产生副作用。
"""

from __future__ import annotations

from typing import Any

from qingci_plugin_sdk import llm_tool

_store_holder: list[Any] = []


def bind_llm_tools_store(store: Any) -> None:
    """由插件在签到库初始化后注入 store 引用（幂等，重复调用覆盖）。"""
    _store_holder.clear()
    if store is not None:
        _store_holder.append(store)


def _store() -> Any:
    return _store_holder[0] if _store_holder else None


@llm_tool(
    name="get_user_checkin_stats",
    description="查询签到用户的统计信息：累计签到天数、连续签到天数、金币、好感度。当用户询问“我签到多少天”“我的金币”“我的好感度”时使用。",
    parameters={
        "type": "object",
        "properties": {
            "user_id": {
                "type": "string",
                "description": "用户的数字 ID（QQ 号 / Telegram ID）",
            }
        },
        "required": ["user_id"],
    },
)
async def get_user_checkin_stats(user_id: str) -> str:
    store = _store()
    if store is None:
        return "签到数据暂不可用"
    if not str(user_id or "").strip():
        return "缺少 user_id 参数"
    try:
        profile = await store.get_profile(str(user_id))
    except Exception:
        return "查询签到数据失败"
    if profile is None:
        return f"用户 {user_id} 还没有签到记录"
    return (
        f"用户 {user_id}：累计签到 {profile.total_days} 天，连续签到 {profile.streak_days} 天，"
        f"金币 {profile.coins}，好感度 {profile.affection:.2f}，"
        f"最后签到 {profile.last_checkin_date or '无'}。"
    )


@llm_tool(
    name="get_group_season_ranking",
    description="查询某群当前赛季的签到排行（前 10 名）。当用户询问“本周/本赛季谁是榜首”“群签到排行”时使用，需要提供群号。",
    parameters={
        "type": "object",
        "properties": {
            "group_id": {
                "type": "string",
                "description": "群号（数字 ID）",
            }
        },
        "required": ["group_id"],
    },
)
async def get_group_season_ranking(group_id: str) -> str:
    store = _store()
    if store is None:
        return "排行数据暂不可用"
    group_id = str(group_id or "").strip()
    if not group_id:
        return "缺少 group_id 参数"
    try:
        result = await store.get_season_ranking(group_id=group_id, limit=10)
    except Exception:
        return "查询排行失败"
    entries = result.get("entries") or []
    if not entries:
        return f"群 {group_id} 本赛季还没有签到记录"
    lines = [f"群 {group_id} 本赛季签到排行（{result.get('start')} 至 {result.get('end')}）"]
    for entry in entries:
        lines.append(f"{entry['rank']}. {entry['username']} {entry['value']} 天")
    return "\n".join(lines)
