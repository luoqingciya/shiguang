# 拾光集（shiguang）

Qingci-Bot 安全插画发图 + 签到插件，由 AstrBot 插件「画境拾珍」（astrbot_plugin_get_px v3.5.1）移植。

## 功能

- **发图**：`/p [标签] [数量]`，Lolicon 主源 + Pixiv 可选回退、内容安全过滤、
  多自然日去重、合并转发（OneBot 11）/ 逐条发送、频率限制
- **自然语言触发**（默认关闭，`auto_trigger_enabled` 开启）：`来三张初音ミク图` / `来一份图`
- **签到**：金币/好感/连签/加持/成就/称号/生日/节假日/全局事件/主题商店、排行、备份
  - `/签到` 每日签到
  - `/签到帮助` 指令帮助
  - `/签到我的 状态|生日查看|生日设置|生日清除|成就|称号查看|称号佩戴`
  - `/签到排行 今日|月榜|连签|累计`
  - `/签到商店 查看|加持|主题列表|主题查看|主题购买|主题切换|刷新背景`
  - `/签到管理 预览|导出|事件查看|事件添加|事件删除`（仅管理员）
- **纯文本签到**：直接发送「签到」（`^(?!/)签到$`）

## 安装

```bash
# 从插件市场安装（待上架）
# 或手动克隆到 plugins/ 目录
```

插件依赖自动安装：`pixivpy-async`、`aiohttp`、`Pillow`、`lunar-python`、`jinja2`。

## 配置

插件配置位于 `config.yaml` 的 `plugins.shiguang` 节（WebUI「插件管理 → 配置」可视化编辑）：

| 配置项 | 默认 | 说明 |
|---|---|---|
| `pixiv_refresh_token` | 空 | Pixiv 回退源 token（可选） |
| `lolicon_api_url` | `https://api.lolicon.app/setu/v2` | Lolicon 主源地址 |
| `max_count` | 5 | 单次最大发送数量 |
| `dedupe_days` | 1 | 图片按自然日去重天数（0=关闭） |
| `request_timeout` | 30 | 下载超时（秒） |
| `image_quality` | `original` | 图片质量（original/large/medium） |
| `forward_threshold` | 1 | 超过该数量用合并转发（仅 OneBot 11） |
| `auto_trigger_enabled` | false | 自然语言触发开关 |
| `checkin_enabled` | true | 签到功能开关 |
| `rate_limit_seconds` | 3 | 请求频率限制（秒） |

完整配置项见 `_conf_schema.json`（WebUI 表单来源）。

## 说明

- **触发语义**：Qingci 群聊默认 `trigger_mode: at`，命令需 @bot；纯文本「签到」规则为 `^(?!/)签到$` 不受影响
- **签到卡渲染**：HTML 签到卡依赖框架 HTML 渲染服务（Playwright，`qingci-bot-ce[render]`），
  渲染不可用时自动降级纯文本签到
- **数据**：签到 SQLite（`data_root()/plugins/shiguang/checkin.sqlite3`）与发图去重索引与主库隔离，
  插件卸载不删除数据（备份前请谨慎）

## 许可

GPL-3.0-or-later。移植自 [shitianyaa/astrbot_plugin_get_px](https://github.com/shitianyaa/astrbot_plugin_get_px)（MIT）。
