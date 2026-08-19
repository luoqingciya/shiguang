from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEFAULT_CHECKIN_THEME_ID = "default"


@dataclass(frozen=True)
class CheckinTheme:
    theme_id: str
    code: str
    name: str
    version: int
    description: str
    price: int
    enabled: bool = True
    kicker: str = ""
    seal: str = ""
    placeholder_title: str = ""

    @property
    def free(self) -> bool:
        return self.price <= 0

    @property
    def template_version(self) -> str:
        return f"{self.theme_id}:{self.version}"

    def template_dir(self, plugin_root: Path) -> Path:
        return plugin_root / "templates" / "checkin_themes" / self.theme_id

    def preview_path(self, plugin_root: Path) -> Path:
        return self.template_dir(plugin_root) / "preview.png"


CHECKIN_THEMES: dict[str, CheckinTheme] = {
    "default": CheckinTheme(
        theme_id="default",
        code="00",
        name="米白",
        version=1,
        description="默认米白主题",
        price=0,
    ),
    "blue": CheckinTheme(
        theme_id="blue",
        code="01",
        name="浅蓝",
        version=1,
        description="浅蓝主题",
        price=1500,
    ),
    "red": CheckinTheme(
        theme_id="red",
        code="02",
        name="红黑",
        version=1,
        description="红黑主题",
        price=1500,
    ),
    "yellow": CheckinTheme(
        theme_id="yellow",
        code="03",
        name="黄黑",
        version=1,
        description="黄黑主题",
        price=1500,
    ),
    "spring": CheckinTheme(
        theme_id="spring",
        code="04",
        name="新柳",
        version=1,
        description="新柳入春的青绿古风主题",
        price=1500,
        kicker="新柳入卷 · 春日签到",
        seal="春册",
        placeholder_title="春山未远",
    ),
    "summer": CheckinTheme(
        theme_id="summer",
        code="05",
        name="荷风",
        version=1,
        description="荷风送香的青碧夏日主题",
        price=1500,
        kicker="荷风送香 · 夏日签到",
        seal="夏册",
        placeholder_title="荷塘未满",
    ),
    "autumn": CheckinTheme(
        theme_id="autumn",
        code="06",
        name="丹枫",
        version=1,
        description="丹枫入卷的赭金秋日主题",
        price=1500,
        kicker="丹枫入卷 · 秋日签到",
        seal="秋册",
        placeholder_title="秋山未远",
    ),
    "winter": CheckinTheme(
        theme_id="winter",
        code="07",
        name="寒梅",
        version=1,
        description="疏梅映雪的墨蓝冬日主题",
        price=1500,
        kicker="疏梅映雪 · 冬日签到",
        seal="冬册",
        placeholder_title="雪意未浓",
    ),
}


def resolve_checkin_theme(value: object) -> CheckinTheme | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    normalized = raw.lower()
    direct = CHECKIN_THEMES.get(normalized)
    if direct is not None:
        return direct
    if normalized.isdigit():
        normalized = normalized.zfill(2)
    for theme in CHECKIN_THEMES.values():
        if normalized == theme.code or raw == theme.name:
            return theme
    return None


def get_checkin_theme(theme_id: object) -> CheckinTheme:
    return resolve_checkin_theme(theme_id) or CHECKIN_THEMES[DEFAULT_CHECKIN_THEME_ID]
