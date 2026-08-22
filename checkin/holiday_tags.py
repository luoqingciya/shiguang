"""C3 节日/主题图集 — 节日名 → 推荐标签映射

配合 HolidayCalendar（在线国假数据）与内容安全过滤，在签到背景/发图时
把当天命中的节日标签作为首选尝试，失败自动回退用户配置标签。
"""

from __future__ import annotations

from datetime import date

# 国假名称（holiday-cn 数据源）与内置常见节日 → 推荐图片标签
FESTIVAL_TAGS: dict[str, str] = {
    "元旦": "新年",
    "春节": "新年",
    "除夕": "新年",
    "情人节": "情人节",
    "妇女节": "花",
    "植树节": "森林",
    "清明节": "春天",
    "劳动节": "劳动",
    "青年节": "青春",
    "儿童节": "童趣",
    "端午节": "端午",
    "七夕": "七夕",
    "中秋节": "中秋",
    "国庆节": "国庆",
    "圣诞节": "圣诞",
    "平安夜": "圣诞",
}

# 日期特判（非国假但常见应景节日），在线数据缺失时兜底
_DATE_TAGS: dict[tuple[int, int], str] = {
    (2, 14): "情人节",
    (7, 7): "七夕",
    (12, 24): "圣诞",
    (12, 25): "圣诞",
}


def festival_tag_for(date_key: str, holiday_name: str = "") -> str:
    """返回当天命中的节日标签；未命中返回空串。

    Args:
        date_key: YYYY-MM-DD
        holiday_name: 在线节假日名称（可为空）
    """
    name = str(holiday_name or "").strip()
    tag = FESTIVAL_TAGS.get(name, "")
    if tag:
        return tag
    try:
        day = date.fromisoformat(date_key)
    except ValueError:
        return ""
    return _DATE_TAGS.get((day.month, day.day), "")
