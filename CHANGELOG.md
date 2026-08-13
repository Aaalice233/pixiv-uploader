# Changelog

## 🧪 Unreleased

### 🐛 问题修复
- Pixiv 标签现在会逐项确认添加成功后再输入下一项，不再因清空输入框导致已输入标签被删到只剩一个。
- 人机验证出现后自动鼠标漂移会立即停止；浏览器关闭或拟人点击被遮挡时也会正确终止或启用可靠兜底。

### ⚡ 体验优化
- Pixiv 投稿不再使用风险等级和额外风险冷却；遇到限流时会直接停止批次并保留未确认图片。
- Pixiv 与 Civitai 的浏览器操作更接近真人：鼠标沿带抖动的自然轨迹移动并点击，日文标题、中日文简介和中英日标签按文字类型使用不同的逐字节奏，连续操作会逐渐放缓并偶尔走神。
- 用户设置的投稿间隔现在始终作为最低值，两个平台只在其上增加随机停顿与偶发长休息，并在发布工作台显示可取消的等待倒计时。
- Pixiv 会在确认登录有效后浏览首页，相邻投稿之间偶尔过渡浏览首页或刚发布的作品页，不再直奔投稿页秒提交。

## 🚀 1.2 - 2026-08-13

### ✨ 新功能
- 发布工作台现已集中创建发布、查看实时任务与逐图结果，并支持取消、失败重试和移除记录。
- 自动发布状态始终显示在工作台顶部，可直接进入定时发布设置。

### ⚠️ 重要变化
- 已移除将 Civitai 多图帖重新发布为多个单图帖的功能；正常发布图片到 Civitai 不受影响。

### 🐛 问题修复
- Pixiv 投稿前出现人机验证时不再因时间计算异常中断，完成验证后可继续发布。
- Pixiv 投稿页现在能准确识别隐藏的图片上传控件，避免误判为未登录。
- 多图发布失败或取消时会完整保留未处理图片，并明确显示每张图片的结果和可重试状态。

### ⚡ 体验优化
- 发布任务可点击整行展开或收起详情，操作按钮和详情内容不会误触折叠。
- 设置页滚动和底部保存区域更加稳定，长配置页面操作更顺畅。

## 🚀 1.1 - 2026-08-12

### 🐛 问题修复
- 创建发布任务时可单独或批量删除已导入图片；删除只清理项目 `upload/` 副本，并阻止误删正在发布的文件。
- 多图发布进度现在明确按整批图片聚合，并持续显示当前图片、总图片数和已处理数量。
- Pixiv 登录不再暴露远程调试自动化标记，账号状态改为通过真实投稿页验证，避免把空 Profile 误报为已登录。
- Pixiv 已点击投稿但结果不确定时不再自动重试，并保留原图供人工核对，避免生成重复作品；确认发布成功的原图会可靠移出 `upload/`。

### ⚡ 体验优化
- Pixiv 登录过期或出现 CAPTCHA 时只需在 Pixiv 页面完成必要操作，任务会自动检测并续跑，无需按 Enter 或点击“继续”。
- Pixiv 批次复用稳定浏览器会话，并根据 CAPTCHA、限流和连续成功结果自动调整可取消的发布冷却；重启后仍遵守未结束的冷却。
- 设置页和任务中心会实时显示真实会话状态、验证/冷却原因、风险等级、最后验证时间和剩余时间。

## 🚀 1.0 - 2026-08-11

### ✨ 新功能
- 全新 Pixiv Uploader 工作台统一管理发布、Civitai 拆分、任务、日志和设置，支持桌面端与移动端。
- LLM 可自动生成日文文案和视觉标签，并提供分级重试、响应修复与后备模型切换。
- 发布任务按真实阶段显示进度、当前图片和结果统计；运行日志支持一键复制。
- Pixiv 支持本地智能标签、R-18 自动处理以及可配置的文字和图片水印。

### 🎨 体验改进
- 设置按用途重新归类，模型安装和更新检查不再混入发布任务。
- 支持简体中文与 English、深浅主题、页面地址恢复和浏览器前进后退。
- 运行数据与程序文件分离，升级项目时保留本机配置和历史记录。

### 🐛 问题修复
- LLM 生成的视觉标签现在会真正进入 Pixiv 投稿，并与本地标签统一整理，最多保留 10 个。
- 修复旧模型路径、大图请求超时、浏览器关闭误报以及单平台日志归属错误。
- 投稿完成后使用可靠信号确认结果，并尽可能记录真实 Pixiv 作品链接。

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
