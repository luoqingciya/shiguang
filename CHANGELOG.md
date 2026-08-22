# 变更记录

本文件遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与 [语义化版本](https://semver.org/lang/zh-CN/)。

## [1.0.4] - 2026-08-22

### Fixed

- **签到管理「事件添加」命令 100% 失效**：`cmd_checkin_event_add` 把整个 `ctx.args`（如 `"年度 12-25 圣诞节"`）当作 `event_type` 传入，`date_value`/`name` 恒为空串，被 `_handle_checkin_event_admin` 的 `not name` 校验拦截，静默返回用法提示。现按 `<年度|单次> <日期> <名称>` 拆 3 个 token 后按位传入，事件添加恢复可用
- **签到卡片「加持剩余天数」恒显示 0**：`_checkin_profile_from_record` 将 `boost_start_date`/`boost_until_date` 硬编码为空写入卡片快照，`boost_remaining_days` 恒返回 0，图片卡永远不显示"加持剩余 N 天"（与纯文本展示不一致）。快照改为保留真实 profile 的加持起止日期（含管理员预览与重复签到路径）
- **未确认生日用户每次签到都调 QQ API**：`qq_birthday_checked=0` 且读取非 definitive 时不标记，每次签到（含重复查卡）最多 +3s `get_stranger_info`。现失败/未公开按天退避（进程级缓存当天尝试日期），当天不再重复查询
- **Pixiv 客户端每次 `/p` 无条件重建（连接泄漏）**：Pixiv-only 场景每次搜索都重建客户端且旧 aiohttp session 未 close。`_init_client` 增加幂等保护（已有可用客户端直接复用），重建前调度关闭旧 session
- **Pixiv 401/403/429 无错误分类**：token 失效/无权限/限流混为一般错误且被无谓重试。现按 HTTP 状态码分类并给出明确提示（重新配置 token / 检查账号状态 / 稍后再试），确定性错误不再重试
- **下载器 SSRF 缺口**：直连 URL 默认跟随重定向，`lolicon_api_url` 若指向内网/恶意服务可被利用访问内网并发送到群里。下载前预检目标主机（私网/回环/保留网段拒绝），重定向后对最终 URL 二次校验
- **插件 Config 为"死配置"**：`class Config` 定义在模块级，CE 框架取 `type(plugin).Config`（类内属性），WebUI 插件配置表单拿不到 schema；`_conf_schema.json` 在 CE 全库无读取点。迁移为插件类内 pydantic `BaseModel`（含全部字段描述与默认值，兼容历史 `_conf_schema.json`），删除死文件 `_conf_schema.json`，WebUI 配置表单恢复
- **`send_as_forward` 无配置入口**：`_forward_threshold()` 回退读取该字段但 Config/schema 均未声明。已补声明（历史兼容开关，未配置 `forward_threshold` 时生效）

### Performance

- **签到背景选择/恢复加总时间预算**：背景下载最多尝试 5 页 × 8 候选，无预算时最坏数十分钟并长时间持有同一用户的流程锁。现加 90s 总预算（`asyncio.wait_for`），超时走纯文本兜底、占用自动释放
- **多图下载加总时间预算**：`max_count=20` + 每张重试时最坏阻塞数十分钟。逐张以剩余时间预算包裹（`asyncio.wait_for`，兼容 Python 3.10），超预算中止剩余下载、已成功部分照常发送
- **合并转发双层重试收敛单层**：`search.py` 外层 3 次 × `_send_forward` 内层 3 次最坏 9 次尝试约 20s 退避。现收敛为事件层 `_send_forward` 单层重试，外层失败直接降级逐条发送
- **候选数按需请求**：用户只要 1 张也向上游要 5/20 张浪费配额。现取 `max(count, 3)`（封顶 `max_count`）
- **群排行 today/month 用 SQL 下推日期过滤**：原先拉该群全部历史 presence 行做 Python 聚合，大群（10 万行级）每次排行全量读入内存。today/month 改为 `WHERE date_key BETWEEN` 下推，streak/total 保持全量
- **读写锁拆分（纯读不再持全局锁）**：签到/购买/排行/备份共用一把 `asyncio.Lock`，大群排行、备份导出等长读会阻塞所有用户写入。排行与备份导出等纯读操作去掉锁（WAL 模式读不阻塞写），导出改用单连接 `BEGIN` 保证快照一致；SQLite busy timeout 提到 10s
- **签到背景图 data URL 跨档缓存**：渲染降档最多 3 档，每档重建视图模型都会对同一背景图重复「读盘 + base64」。以 `(path, mtime_ns, size)` 为键缓存成功结果，同一次签到的后续档位直接命中
- **重复签到跳过内容组装**：已持久化问候的重复签到直接进入查卡/发卡，跳过成就解锁、生日读取等无谓 DB 写事务与外部调用

### Security

- **插件配置 API 脱敏 + 空值保护**：CE 侧敏感字段（token/secret/password/api_key 等）在 WebUI 不回显明文；保存时空值不覆盖已有密钥。另新增 `on_config_update` 钩子，配置保存后热生效，无需重载插件
- **关键 Web 写操作审计留痕**：`checkin-import`（覆盖全库）、`checkin-members/update`（篡改金币）、安全词增删、作品黑名单增删均记录操作 + 调用方地址，供事后排查

### Changed

- **下载器仅对 Pixiv 系主机附带 Pixiv Referer**：非 Pixiv CDN（Lolicon 反代等）携带 Pixiv Referer 可能被个别 CDN 拒绝
- **生日时间戳显式按北京时间解析**：9/10 位时间戳不再依赖宿主机时区
- **购买加持余额读取与扣款并入同一事务**：`_purchase_boost_sync` 在 `BEGIN IMMEDIATE` 内完成读余额 + 扣款，消除绕过全局锁（Web 直连/多实例）时的 TOCTOU 超扣
- **备份导入耗时告警**：锁内导出/导入超过 2s 记录耗时与用户数，便于评估大库导入对签到的影响
- **`render_html` timeout 参数签名探测**：框架签名变化时仍能正常渲染，不再因 `TypeError` 被兜底吞掉而静默降级纯文本
- **移除死代码**：`components_to_segments` 公开包装器、`get_message_str` 等无引用方法；`_Files._load` 跳过非文件表单字段（避免文本字段 500）；黑名单缩略图读取加 1 MiB 上限；排行时间用 `fromisoformat` 解析

## [1.0.3] - 2026-08-21

### Fixed

- **`/签到帮助` 回复原始字典文本**：Matcher handler 返回图片链（v12 段数组）会被框架 `str()` 化为字典文本。`cmd_checkin_help` 改为 `event.send` 主动发送图片并返回 `None`（与 `/签到` 主流程一致），帮助图恢复
- **QQ 生日读取报错「当前事件不支持 call_action」**：`_fetch_qq_birthday` 原从 `event.bot` 取 Bot，但 `EventAdapter` 从不暴露 `bot` 属性（恒为 None）导致生日查询永远走不到。改为走 `event._connection().call_api("get_stranger_info", ...)` 既有连接通道，并修正误导性告警文案；签到问候的 QQ 生日数据恢复可用
- **子命令不触发**：`签到管理 / 签到排行 / 签到我的 / 签到商店` 等在 `on_load` 内注册 `on_command(subcommands=...)`，SDK 旧版子指令 matcher 只进模块级收集器而被丢弃。配合 SDK 1.13.1（子指令挂 `parent.meta["sub_matchers"]`）+ CE 1.16.2 展开注册，全部子命令恢复触发（插件本体无需改动，随框架升级生效）

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
