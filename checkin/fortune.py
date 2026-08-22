"""D2 签到运势 — 今日运势文案

离线确定性模板：按「用户 + 日期」生成稳定的运势文案（同一用户当天多次签到
结果一致），无网络依赖；如需接入 LLM 生成，可在此处扩展为可选远程调用，
失败时仍回退离线模板（与 greeting.py 的容错策略一致）。
"""

from __future__ import annotations

import hashlib

from .models import CheckinProfile

# (签级, 文案) — 长度控制在 20 字内，避免撑爆卡片副标题
_FORTUNE_POOL: tuple[tuple[str, str], ...] = (
    ("上上签", "诸事顺遂，宜收金币"),
    ("大吉", "欧气满满，随手见喜"),
    ("上签", "今日宜打卡，忌偷懒"),
    ("中吉", "稳扎稳打，好事渐近"),
    ("中签", "积跬步，至千里"),
    ("小吉", "微光渐亮，好运酝酿中"),
    ("中平", "平淡是真，坚持最贵"),
    ("末吉", "明日再战，胜券在握"),
)


def generate_fortune(profile: CheckinProfile, date_key: str) -> str:
    """生成用户当天的确定性运势文案（空 profile 或日期时返回空串）。"""
    user_id = getattr(profile, "user_id", "")
    if not user_id or not date_key:
        return ""
    seed = f"shiguang-fortune|{date_key}|{user_id}"
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    level, text = _FORTUNE_POOL[int.from_bytes(digest[:8], "big") % len(_FORTUNE_POOL)]
    return f"今日运势 {level}：{text}"
