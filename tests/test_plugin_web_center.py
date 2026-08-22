"""拾光集 WebUI 管理中心测试（Stage 5）

验证 PluginWebApi 注册的 25 个管理端点挂载到
/api/plugin-web/shiguang/<path>，以及管理页面注册。
"""

import os
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

os.environ.setdefault("QINGCI_TEST", "1")


def _write_config(tmp_path: Path) -> Path:
    from api.auth import set_config_path

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"api_key": "", "bot": {"admin_users": []}}, allow_unicode=True),
        encoding="utf-8",
    )
    set_config_path(cfg_path)
    return cfg_path


async def _build_client(tmp_path: Path):
    """加载 shiguang 插件并挂载到 FastAPI 应用"""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    from bot.testing import TestBot

    _write_config(tmp_path)
    bot = TestBot()
    assert await bot.load_plugin("shiguang")

    from api.server import create_app

    app = create_app()
    bot.plugin_manager.set_web_app(app)
    client = TestClient(app)
    return client, bot


@pytest.fixture
async def web_center(tmp_path):
    # 隔离数据目录：避免测试写入默认 data_root（污染 CE 开发数据 / 残留导致断言失真）
    from bot.paths import data_root, set_data_root

    prev = data_root()
    set_data_root(tmp_path / "data")
    try:
        client, bot = await _build_client(tmp_path)
        with client:
            yield client, bot
    finally:
        set_data_root(prev)


async def test_web_center_routes_and_page(web_center):
    """25 个管理端点已注册，且管理页面已注册"""
    _client, bot = web_center
    plugin = bot.plugin_manager.get("shiguang")
    assert plugin is not None
    paths = {api["path"] for api in plugin._apis}
    assert "overview" in paths
    assert "checkin-ranking" in paths
    assert "checkin-members/update" in paths
    assert "content-safety/terms/add" in paths
    assert "image-blacklist/thumb-data-batch" in paths
    assert "checkin-import" in paths
    assert "checkin-export" in paths
    assert "checkin-stats" in paths
    assert "checkin-season" in paths
    assert "season-settle" in paths
    assert "subscriptions" in paths
    assert "subscriptions/update" in paths
    assert "reminders" in paths
    assert "reminders/update" in paths
    assert "audit-logs" in paths
    assert "config" in paths
    assert len(paths) == 25
    assert plugin._pages and plugin._pages[0]["title"] == "拾光集管理中心"


async def test_overview_endpoint(web_center):
    """概览端点返回成功结构（checkin_store / image_index 已初始化）"""
    client, _bot = web_center
    resp = client.get("/api/plugin-web/shiguang/overview")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert "blacklist_count" in body
    assert "builtin_term_count" in body


async def test_config_endpoint(web_center):
    """config 端点返回 font_url 配置（webui_font_source）"""
    client, bot = web_center
    plugin = bot.plugin_manager.get("shiguang")
    plugin.config["webui_font_source"] = "none"
    resp = client.get("/api/plugin-web/shiguang/config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert "font_url" in body


async def test_checkin_groups_empty(web_center):
    """无签到数据时群列表返回空数组"""
    client, _bot = web_center
    resp = client.get("/api/plugin-web/shiguang/checkin-groups")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["groups"] == []
