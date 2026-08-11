# Changelog

## Unreleased

### Added
- **LLM 反推多级重试**：对限流、超时、网络故障和 5xx 使用遵循 `Retry-After` 的指数退避与随机抖动；空响应或无效 JSON 进入结构修复轮次，主模型耗尽后可依次切换最多 3 个后备模型。支持总时间预算、取消感知、自适应缩小预览图、稳定错误码、去凭据日志及任务中心实时重试状态。
- **真实分阶段任务进度**：发布流程改为领域代码显式上报读取元数据、安全检查、标签识别、LLM 文案、平台填写/提交/验证和文件收尾等阶段；按动态平台能力和多图工作量加权聚合，显示当前图片、阶段序号与成功/失败计数，失败或取消不再伪装成 100%。
- **日志一键复制**：运行记录页可将当前显示的全部日志按“时间 / 来源 / 内容”一次性复制到剪贴板，并提供成功或权限失败反馈。

### Changed
- **Web 信息架构重构**：侧边栏统一为发布工作台、Civitai 拆分、任务中心、运行记录和设置等页面导航，并通过可恢复的 URL hash 保留当前位置；设置从独立弹窗改为完整页面，内部按通用、Pixiv 处理、LLM 文案、定时发布和系统维护分类。
- **维护操作归位设置**：R-18 打码模型安装/检查与项目更新移动到“设置 → 系统维护”，使用独立维护状态卡反馈输出和结果，不再作为发布任务出现在任务中心；更新成功会明确提示重启服务。
- **项目独立为 Pixiv Uploader**：仓库更名并迁移到 `Aaalice233/pixiv-uploader`，应用品牌、包元数据、启动提示和中英文文档同步更新；原项目保留为 `upstream`。
- **Web UI 全面应用化重构**：用 `frontend/src/flow-app.jsx` + `frontend/public/flow.css` 重建发布工作台、任务、日志、设置和响应式交互；采用无边框边缘阴影设计，支持深浅主题。
- **完整界面多语言**：新增简体中文 / English 语言包、系统语言检测、即时切换与持久化；任务元数据、日期数字、动态状态、可访问性文案和 API 错误均通过稳定翻译边界呈现，并在前端构建前自动校验语言包完整性。
- **图文水印完整接入**：Pixiv 发布副本可选择文字或图片水印；支持透明 PNG、资源导入/预览/删除、位置、大小、透明度、边距、字体、字型和颜色设置，原图保持不变。
- **平台范围收敛为 Civitai / Pixiv**：删除 X (Twitter) 与小红书的发布模块、浏览器 profile、路由、设置、模板、依赖和旧界面；CLI、Web API 与 Scheduler 对未知 target 统一显式拒绝。
- **Pixiv 文案配置简化**：LLM 人设只服务 Pixiv，移除旧账号抽象与跨平台 `manifest.copy`，结果直接写入 `manifest.pixiv`。
- **Web 任务状态完善**：任务支持明确的 queued/running/waiting_input/success/failed/canceled 状态、阶段文案、结构化错误和确认输入。
- **项目层级与模块边界整理**：Python 业务代码统一收拢到 `pixiv_uploader/` 包，前端拆分为 `src/`、`public/`、`dist/`，根目录旧命令保留为薄兼容入口。
- **运行状态统一隔离**：manifest、日志、缓存、规则覆盖与水印资源集中到被忽略的 `runtime/`；首次启动安全迁移旧目录和配置，不覆盖冲突数据。
- **默认资源与用户数据分离**：只读种子文件放入 `pixiv_uploader/resources/`，151,262 条词典改为无损 gzip（约 6.4 MiB → 2.4 MiB）并直接读取；误提交的本机回归历史与提示音媒体从仓库移除。
- **基础依赖瘦身**：`requirements.txt` 只保留运行必需项，Tagger 与 R-18 打码依赖继续由对应向导按需安装。

### Fixed
- **Tagger 旧路径失效**：集中管理 HainTag 模型设置，运行前验证模型与映射文件，并自动发现当前项目或 HainTag 数据目录中的可用模型；迁移项目后不再扫描旧仓库的绝对路径。
- **LLM 大图请求超时**：生成文案前将图片转换为最长边 1536 px 的 JPEG 预览，不再把十余 MiB 的发布原图直接编码进请求；降低上传耗时和内存占用，同时按实际内容统一 MIME 类型。
- **Pixiv 浏览器关闭误报**：浏览器窗口关闭后立即输出单一、可操作的 `browser_closed` 错误，停止无意义的截图、HTML dump、字段填写与重试；任务结束时不再关闭通过 CDP 复用的登录窗口。
- **任务日志归属与汇总**：日志来源改为按任务标记 `publish` / `pixiv` / `civitai` 等真实模块；单平台任务不再显示“双站”汇总，失败与取消分别计数。

## 2026.05.19

### Added
- **版本管理**：新建 `version.py` 作为版本号单一源（日期版本 `YYYY.MM.DD`）。CLI header 和 Web UI 标题栏显示当前版本号。`/api/status` 返回 `version` 字段。
- **NAI alpha channel 隐写读取**：`StandaloneMetadataReader` 新增 `_read_stealth_pnginfo()`，从 RGBA 图片 alpha 通道 LSB 提取 NAI 隐写元数据（magic `stealth_pngcomp` + gzip JSON）。当 PNG text chunk 中无 Comment 时自动回退到隐写读取，解决 NAI 图片经处理后 Comment 被剥掉导致元数据丢失的问题。

### Fixed
- **自动更新 upstream tracking**：`_check_updates()` 现在在检查前验证 `@{u}` 是否可用，缺失时自动尝试 `git branch --set-upstream-to=origin/main main`，修复无 upstream tracking 时更新检查静默失败的问题。
- **Scheduler AI 标签开关**：`_scheduler_fire()` 构建 params 时补读 `ai_tags_by_platform`，修复定时发布忽略 AI 标签开关设置的问题。
- **Tagger rating → NSFW 判定**：WD14 tagger rating 分类（general/sensitive/questionable/explicit）现在被三个 tagger bridge 正确提取并返回 `rating_scores` 字段。当 tagger 判定为 explicit/questionable（>0.5）时自动升级 `age_restriction` 至 r18。
- **NAI Comment chunk 解析**：`StandaloneMetadataReader` 现在解析 PNG Comment chunk 中的 NAI JSON 元数据（prompt / uc / 生成参数），检测条件为 `"prompt" in data and "uc" in data` 避免误判。
- **nsfw_blocked_targets 重算**：censor / rating 升级 age_restriction 后重新计算 `nsfw_blocked_targets`，修复初始 all_ages 被升级为 r18 后 xhs 等平台未被拦截的问题。

## 2026-05-16 (2)

### Added
- **Scheduler LLM 反推支持**：定时自动发布现在可配置 LLM 反推（`llm_reverse` / `llm_persona` / `llm_content_mode`）。launcher CLI 配置菜单新增询问步骤；Web UI Scheduler Dialog 新增 LLM checkbox + 人设/内容模式选择；`_scheduler_fire()` 从 sched 配置读取并传入 params；`_sched_default()` 补充 LLM 字段默认值。
- **Web UI 打标配置新增 PixAI 模型目录**：`/api/tagger-config` GET/POST 均支持 `pixai_model_dir` 字段；TaggerSetupDialog 新增 PixAI 目录输入框（含 `model.onnx` 存在检测），优先级说明移到标题描述，对话框改名为「打标器 配置」。
- **Manifest `tagger.details`**：`manifest["pixiv"]["tagger"]` 新增 `details` 字段，回传 tagger 推理异常信息，方便事后 debug。

## 2026-05-16

### Added
- **PixAI Tagger v0.9 集成**：新建 `pixiv/pixai_tagger.py`（`PixAITaggerBridge`），支持 `deepghs/pixai-tagger-v0.9-onnx` ONNX 模型。CLIP 风格归一化（mean=std=0.5，BCHW layout），读取 `preprocess.json` / `thresholds.csv` / `selected_tags.csv`，返回与 `StandaloneTaggerBridge` 相同 schema（含空 `copyright` 组保持接口兼容）。
- **Tagger 优先链**：`_make_bridges()` 拆分 metadata reader 和 tagger bridge 选择逻辑。新优先链：PixAI → CL/WD14 → None，HainTag 退出 tagger 自动优先（metadata reader 保持不变）。
- **Launcher 下载菜单**：`[6]` 改为「配置 / 下载 Tagger 模型」，进入子菜单可选：配置向导 / 自动下载 PixAI（`huggingface_hub.snapshot_download`）/ 查看手动下载地址。
- **Setup wizard 支持双 tagger**：`setup_tagger.py` 新增 PixAI 配置步骤（`step2_pixai_model_dir` / `step3_pixai_verify`），`main()` 现在询问配置 PixAI / CL / 两者，互不干扰。
- **Manifest `tagger_type` 字段**：manifest `pixiv.tagger` 新增 `tagger_type: "pixai"|"cl"`，旧 manifest 向前兼容（key 缺失默认 cl）。

### Fixed
- `_write_haintag_settings`（`setup_tagger.py` 和 `launcher.py`）：`else` 分支改为 `existing.update(settings)`，修复 fresh 用户（无 HainTag `{"settings":{}}` 嵌套格式）配置多个 key 时后写覆盖前写的问题。预存在问题，PixAI + CL 双配置时首次暴露。

## 2026-05-15

### Fixed
- 账号面板登录状态判断：Pixiv / Civitai / 小红书 改回"profile 目录是否存在"判断，与登录前的老逻辑一致。原 `.session_valid` 标记文件方案依赖 `while context.pages` 循环结束后写文件，浏览器关闭时 `context.pages` 访问抛异常会被外层 `except` 吞掉，touch 永远跑不到，导致登录完状态不更新。

### Removed
- `.session_valid` 标记文件相关读/写/删全部清理（dead code）。

## 2026-05-14 (4)

### Added
- **X (Twitter) publishing**: new `x/` module mirrors pixiv module shape. Playwright over persistent profile with cookies.json import (Cookie-Editor JSON), stealth init script for X automation-detection bypass, Ctrl+Enter post shortcut, sensitive-media auto-label hook. Default template `en_sfw` per X traffic research (2 hashtags sweet spot, entity + template core).
- **小红书 (xhs) publishing**: new `xhs/` module. Web 版 publishing flow (`creator.xiaohongshu.com/publish/publish`) with dropdown-driven topic insertion (xhs requires picking topics from suggestion list, plain `#xxx` text isn't registered as a topic). Auto-ticks AI synthesis declaration checkbox per GB45438-2025 compliance. NSFW images (r18/r18g) are hard-rejected before publish.
- **Universal `manifest.copy` area**: LLM reverse output now lives in `manifest.copy.{title,caption}.{ja,en,zh}` so X/xhs/pixiv all read from one place. Pixiv legacy fields kept for back-compat. New `apply_llm_result_to_copy_block` projects platform-specific LLM fields onto the universal area.
- **`PLATFORM_RULES` table** in `civitai_splitter.py`: drives per-platform `needs_sanitize`, `needs_censor`, `needs_copy`, `max_age`. Replaces the old `needs_pixiv_pipeline = pixiv or x` hack. civitai stays image-only (no sanitize/censor/LLM); pixiv/x/xhs share the sanitized artifact.
- **LLM reverse account `max_nsfw_level`** (sfw / r18 / r18g): per-account NSFW capability flag. cmd_upload skips reverse when image age exceeds account ceiling instead of asking the API to look at content it'll refuse.
- **LLM reverse on-demand**: only runs when targets include a platform that needs copy (civitai-only uploads skip the LLM call entirely).
- **Censor preset system** (`pixiv/censor.json` `preset` field): `off` / `japan` (Pixiv 标准) / `strict` levels. Default `japan` covers genital area + bodily fluids (no nipples — matches Pixiv platform compliance). `strict` adds nipples. UI label displays "Pixiv 标准" for the `japan` preset. New `/api/censor-preset` endpoint and Web UI selector under settings.
- **Web UI multi-target selector**: `ImagePickerDialog` and `SchedulerDialog` now show 4 checkboxes (Civitai / Pixiv / X / 小红书). Selection persisted to `localStorage` under `civitai-splitter:upload-targets`.

### Changed
- `TARGETS` set in `civitai_splitter.py` now includes `x` and `xhs`. `--targets` accepts these.
- `cmd_upload` web-server bridge (`web_server.py`) `cmd=2` / `cmd=3` paths consolidated into one upload entry; targets are driven by `params.targets` instead of hardcoded per-cmd defaults.
- `pixiv/support.py` `build_pixiv_payload` now exposes `entity_tags` (character / copyright / franchise / identity tags as a flat list) so platform modules can pick them without parsing the full payload.

### Removed
- `x-collect-tags` subcommand and the `hot_tags.json` / `hot_tags_auto.json` / Danbooru reverse-index machinery in `x/support.py`. Replaced by a 2-tag picker driven by template `core` + `social` fields, matching the X 2-hashtag sweet-spot research (3+ tags drop engagement by 17%).

## 2026-05-14 (3)

### Fixed
- `HainTagTaggerBridge._load_settings()` now falls back to root-level JSON keys when no `settings` wrapper exists, matching all other settings loaders. This prevents tagger `model_dir` from silently reading as empty when the path was saved via the Web UI.
- Added `_model_dir` property to `HainTagTaggerBridge` so the tagger-probe check in `cmd_upload` no longer misreports "未配置 model_dir" when haintag is installed.
- "随机 1-5" button in `ImagePickerDialog` now passes the current sort mode to the backend, so it picks from the most-recently-modified (or name-sorted) images when a non-random sort is active.

### Added
- Main web page now accepts file drag-and-drop directly (no need to open ImagePickerDialog): dragging images from Explorer onto the browser window shows a blue overlay and saves files to `upload/` via `/api/add-upload-files`; a toast confirms the count added.

## 2026-05-14 (2)

### Added
- Added `--sort` parameter to `upload` command: `random` (default), `name_asc`, `name_desc`, `time_asc`, `time_desc`. Specifying a count now reliably picks the first N images by the chosen rule instead of random sampling.
- Added manual drag-and-drop ordering in the Web UI image picker (`手动排序` mode): images reordered via drag handles; unselected images shown below for one-click append.
- CLI `_ask_upload_params` now prompts for sort order after count.
- Scheduler config includes a `sort` field persisted to `config.json`; timed uploads use the saved rule. Old configs without `sort` transparently default to `random`.
- `/api/images` now returns `mtime` for client-side time-based sorting.

## 2026-05-14

### Added
- Added Pixiv LLM reverse inference for generating title and caption copy through an OpenAI-compatible vision API.
- Added persona/account configuration with SFW/NSFW content modes for Pixiv copy generation.
- Added Web UI controls for configuring LLM reverse inference and enabling it during Pixiv upload selection.

### Changed
- Pixiv upload manifests now record LLM reverse inference status, selected persona/account, content mode, generated copy, and fallback errors without exposing API keys.

## 2026-05-13

### Added
- Pixiv tag generation now records `metadata_entity_hits` in manifests and rule-fit compare output so metadata-derived fanart detections are visible during tuning.

### Changed
- Pixiv tag generation now recognizes Danbooru-style metadata entities such as `hatsune miku`, `devil janai mon \(vocaloid\)`, and known franchise tags, then routes them through the existing Danbooru→Pixiv JP mapping chain.
- Pixiv fanart tag ordering now places作品 and角色 tags before required and generic tags so prompt-derived entities are not pushed out by the 10-tag cap.
- Metadata-driven fanart detection now stays strict to character/franchise-shaped tokens and no longer promotes generic feature tags like hair color or clothing details into character entities.

## 2026-05-12

### Fixed
- Web UI task cancel now propagates into launcher update checks and upload internals instead of waiting until the whole command returns, so queued/setup/update/upload tasks stop sooner and finish in a real `canceled` state.
- Civitai and Pixiv publish flows now keep cancellation cooperative before the irreversible publish click, but stop rewriting successful post-click completion into a misleading canceled result.

## 2026-05-11

### Fixed
- Pixiv tag generation now maps generic `dress` to `ドレス` instead of ambiguous `ワンピース`, preventing Pixiv from resolving the tag to `ONE PIECE`.

## 2026-05-10

### Added
- Added scheduler support in both the launcher and Web UI, including persisted interval/count/target settings and live scheduler status updates.
- Added browser shutdown handling from the Web UI so the local server can stop itself after the page closes and active tasks finish.
- Added Pixiv rule-fit tooling for collecting high-traffic Pixiv samples, downloading reference images, comparing local generated tags against Pixiv tags, and writing summary reports.
- Added `synonym_tags` to Pixiv general tag configuration so canonical tags can also emit high-value aliases such as BlueArchive / Arknights / WutheringWaves forms.
- Added expanded Pixiv tag mappings, selling-point rules, alias overrides, popularity data, and validation cases.

### Changed
- Pixiv tag generation now keeps generic VTuber tags only when WD14 also identifies a specific character.
- Pixiv tag generation now groups standard WD14/Danbooru tags into Pixiv-native candidate sets, rejects ambiguous one-piece terms without source proof, and ranks content tags by Pixiv popularity counts instead of tagger score proxies.
- Pixiv age detection now promotes explicit adult candidates such as nipples / pussy / pubic hair to R-18 and keeps the R-18 tag synchronized when censoring forces the rating.
- Pixiv tag generation now gives WD14 tagger output stricter category-aware handling, uses the 151k Danbooru→JP table behind user overrides, expands synonyms before the 10-tag cap, and preserves forced R-18 / original tags.
- Civitai safety checks now match multi-word school/minor phrases from filenames and metadata instead of only exact split tokens.
- Pixiv and Civitai login flows now launch persistent Chrome without automation and sandbox default args, and the Pixiv account switch action immediately opens the login page.
- Web scheduler state is broadcast through SSE, restored on stream connection, and refreshed by the frontend when the next scheduled fire time passes.
- WD14 tagger setup copy now explains the haintag bridge, standalone tagger fallback, and model directory expectations more clearly.

### Notes
- `config.json` is still private runtime state and must not be committed.
- `pixiv/rule_fit/` contains generated rule-fit samples, manifests, and reports; keep only deliberate fixtures under version control.
