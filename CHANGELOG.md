# 变更记录

本文件遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与 [语义化版本](https://semver.org/lang/zh-CN/)。

## [1.0.2] - 2026-08-21

### Fixed

- **WebUI「数据管理 → 下载备份」报服务器内部错误**：`plugin_api/_web.py` 的 `send_file` 移植为同步函数（直接返回 `FileResponse`），但 `api.py` 的 `checkin_export` 沿用 quart 风格 `await send_file(...)` —— `await` 非协程对象抛 `TypeError`，被 `internal_error` 捕获返回 500。现改为 `async def send_file`，保持 `await` 调用不变，导出下载恢复正常

## [1.0.1] - 2026-08-21

### Fixed

- **市场版本同步（CE-4）**：`PLUGIN_VERSION` 改为从 `plugin.json` 读取作为权威来源（版本统一 `1.0.1`），规避插件多处方言漂移导致的持久"可更新"死循环
- **降低顶层依赖面，修复打包（EXE）环境加载失败**：`Pillow` 与 `lunar-python` 由顶层导入改为惰性导入（`pixiv/downloader.py`、`checkin/cache.py`、`checkin/content.py`）——依赖未装齐时插件仍可加载并出现在插件列表，相关功能（图片校验/尺寸、农历节日）按需降级，不再因缺包导致整个插件加载失败

### Performance

- **签到卡渲染模板编译缓存**：Jinja2 模板按内容缓存编译结果，避免每次签到卡渲染重复编译
- **签到背景去重查询缓存**：同一背景源 60s 内的已用作品 ID 走内存缓存，减少 SQLite 全量查询
- **黑名单缩略图分批加载**：WebUI 每次批量请求 16 张，避免单次请求体量过大
- **签到备份导出流式读取**：快照导出改为游标迭代，降低大数据量下的内存占用

### Changed

- **签到背景逻辑拆分为独立服务**：背景下载/恢复/占用管理从 `CheckinArtworkMixin`（约 830 行）拆至 `CheckinBackgroundService`，降低 Mixin 职责复杂度
- **配置访问统一为类型化快照**：新增 `ShiguangSettings`，`_cfg_*` 优先读取类型化配置快照，集中声明全部配置 key
- **签到流程锁改为显式字典**：`weakref.WeakValueDictionary` 中的锁可能在无强引用时被 GC 提前回收导致串行失效，改为显式 `dict` 并带容量上限（仅逐出当前未持有的锁）
- **上传临时文件兜底清理**：残留的 `.upload-*` 临时文件在备份修剪时自动清理（超过 1 小时）

## [1.0.0] - 2026-08-19（首个发布）

由 AstrBot 插件「画境拾珍」（astrbot_plugin_get_px v3.5.1，MIT）移植至 Qingci-Bot 生态。

### Added

- **发图**：`/p [标签] [数量]`——Lolicon 主源 + Pixiv 可选回退、内容安全过滤（内置安全词 + 自定义词 + 作品黑名单）、多自然日去重、合并转发（OneBot 11）/ 逐条发送、频率限制
- **自然语言触发**（可选）：`来三张初音ミク图` / `来一份图`
- **签到系统**：金币/好感/连签/加持/成就/称号/生日/节假日/全局事件/主题商店、群排行、备份导出导入
- **签到卡渲染**：8 套主题 HTML 签到卡（字体 base64 自包含），接入框架 HTML 渲染服务（Playwright）；渲染不可用时自动降级纯文本
- **WebUI 管理中心**：群排行/趋势、成员数值编辑、内容安全词与黑名单管理、签到备份导入导出（插件管理页入口）
- **纯文本签到**：直接发送「签到」
- 独立仓库，迁移业务层测试 181 用例
