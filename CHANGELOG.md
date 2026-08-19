# 更新日志

本文件遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与 [语义化版本](https://semver.org/lang/zh-CN/)。

移植自 [shitianyaa/astrbot_plugin_get_px](https://github.com/shitianyaa/astrbot_plugin_get_px)（原插件版本基线 v3.5.1，MIT 许可；本插件以 GPL-3.0 发布，版权见 LICENSE）。

## [1.0.0] - 2026-08-19

### 新增（Stage 1-5 整体交付）

- **发图**：`/p [标签] [数量]`，Lolicon 主源 + Pixiv 可选回退、内容安全过滤、多自然日去重（按群/私聊/标签/源隔离）、合并转发（仅 OneBot 11，超出 `forward_threshold` 自动合并、失败降级逐条）、请求频率限制
- **自然语言触发**（可选）：`来三张初音ミク图` / `来一份图`（`auto_trigger_enabled`）
- **签到系统**：金币/好感/连签/加持/成就/称号/生日（含 QQ 生日读取）/节假日/全局事件/主题商店、排行（今日/月榜/连签/累计）、备份导出/导入
- **签到卡渲染**（Stage 4）：接入 Qingci 框架 HTML 渲染服务（`bot.html_renderer`），8 套主题（米白/蓝/红/黄/春/夏/秋/冬）自包含模板（字体 base64 内联），渲染质量档位（省流量 960x540 / 清晰 1248x702 / 极致 1728x972）；渲染不可用自动降级纯文本
- **WebUI 管理中心**（Stage 5）：插件管理页「拾光集管理中心」入口——群排行/趋势、成员数值编辑、内容安全词与作品黑名单、签到备份导出/导入（17 个管理端点，经框架插件 Web API 挂载）
- **纯文本签到**：直接发送「签到」触发（`^(?!/)签到$`）

### 适配说明

- 事件层经 `_event.py` EventAdapter 适配 Qingci MatcherContext（v12 消息段发送、合并转发封装）
- 配置 32 项（`Config` 类，WebUI 表单由类型注解生成）；AI 问候走 `self.llm.chat()`
- 命令注册基于 SDK `on_command`（子指令组）/ `regex` 规则；群聊默认 at 触发
