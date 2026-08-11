# Pixiv Uploader — 开发说明

本文只记录当前架构与维护约束。历史变更见根目录 `CHANGELOG.md`。

## 目录职责

```text
pixiv_uploader/                 Python 应用包
  __main__.py                   `python -m pixiv_uploader` 入口
  publishing.py                 拆图、发布、manifest 主流程
  web.py                        Flask API、SSE、任务队列、Scheduler
  cli.py                        本地交互菜单
  watermark.py                  水印配置、资源管理与渲染器
  paths.py                      所有运行目录的唯一声明
  runtime.py                    运行目录创建与旧数据迁移
  pixiv/                        Pixiv 领域模块
    storage.py                  原子 JSON 持久化、运行规则与 manifest
    tagger_settings.py          HainTag 设置与模型目录解析
    support.py                  标签、元数据、浏览器发布与规则拟合
  resources/                    随版本发布、只读的默认数据
    pixiv/                      标签、年龄、打码默认规则及压缩词典
    civitai/                    Civitai 默认安全规则

frontend/
  src/                          React、CSS 与 i18n 源码
  public/index.html             静态入口
  dist/flow-app.js              需要提交的生产构建产物

runtime/                        本机运行数据，整体被 Git 忽略
  manifests/                    每张图片的平台状态
  progress/                     拆分断点与去重记录
  logs/                         日志、失败截图与页面 dump
  tmp/                          可删除的处理中间文件
  pixiv/                        用户可调规则、缓存和回归样本
  civitai/                      用户可调安全规则
  watermark/                    水印配置、字体和图片

scripts/                        维护检查脚本
tests/                          Python 单元与契约测试
docs/                           开发文档
upload/                         待处理原图
done/                           所有目标成功后的原图
models/                         本机下载的模型
config.json                     本机设置、凭据和 Scheduler 状态
```

根目录的 `launcher.py`、`web_server.py`、`civitai_splitter.py` 仅是兼容旧命令的薄入口，不放业务逻辑。包内引用必须使用相对导入，禁止重新引入根目录模块互相导入。

## 运行方式

```powershell
# 交互菜单
.venv/Scripts/python.exe launcher.py

# Web UI
.venv/Scripts/python.exe web_server.py

# 包入口或兼容 CLI
.venv/Scripts/python.exe -m pixiv_uploader upload --targets civitai,pixiv --count 1
.venv/Scripts/python.exe civitai_splitter.py upload --targets civitai,pixiv --count 1
```

`run.bat` 会创建虚拟环境、安装依赖并启动菜单。

## 状态与默认资源

`pixiv_uploader/resources/` 是版本控制下的默认值，运行时不得修改；`runtime/` 是用户状态。首次调用 `ensure_runtime_files()` 时：

1. `ensure_runtime_layout()` 创建统一目录；
2. 将旧版根目录、`pixiv/` 下的运行数据迁移到 `runtime/`；
3. 缺少用户配置时，从 `pixiv_uploader/resources/` 复制默认文件；
4. 不覆盖已经存在的目标文件，冲突留在旧位置供人工判断。

151,262 条 Danbooru 词典保存在 `pixiv_uploader/resources/pixiv/danbooru_jp.json.gz`，通过 `load_json()` 直接读取 gzip，不复制到 `runtime/`。这样保留完整词典，同时显著减小仓库和安装体积。

新增运行路径时先扩展 `RuntimePaths`，再由 `ensure_runtime_layout()` 创建或迁移；不要在业务模块散写 `PROJECT_ROOT / "..."`。

## 发布主流程

1. 从 `upload/` 按排序或前端指定顺序选图；
2. 读取 ComfyUI / A1111 metadata、prompt 和 LoRA token；
3. 运行 PixAI 或 WD14 tagger，并构建 Pixiv payload；
4. 按 `PLATFORM_RULES` 执行平台特有的 sanitize、censor、水印与 LLM 文案；
5. 使用独立浏览器 profile 发布到 Civitai / Pixiv；
6. 持续写入 `runtime/manifests/`；所有目标成功后把原图移入 `done/`。

取消只在可逆阶段立即生效。进入实际 publish 点击后，流程优先完成收尾并记录远端成功结果，避免远端已发布而本地误报取消。

LLM 视觉请求只发送最长边 1536 px 的 JPEG 预览，不得直接 base64 编码发布原图。Pixiv 浏览器通过 CDP 复用登录窗口时，任务结束只断开自动化连接，不关闭用户浏览器；仅关闭由任务自身创建的 persistent context。浏览器会话中途关闭必须记录 `browser_closed` 并立即停止后续 DOM 操作。

## 平台边界

`pixiv_uploader.publishing.PLATFORM_RULES` 是平台能力的单一来源：

| 平台 | needs_sanitize | needs_censor | needs_copy |
|---|:-:|:-:|:-:|
| civitai | ✗ | ✗ | ✗ |
| pixiv | ✓ | ✓ | ✓ |

- `needs_sanitize`：重新编码，移除 EXIF 和 PNG text chunk；
- `needs_censor`：运行自动打码；
- `needs_copy`：允许 LLM 生成 Pixiv 标题与简介。

CLI、Web API、Scheduler 和配置归一化共用同一平台集合。未知平台必须在任务启动前显式失败，不做静默兼容。

## Pixiv 标签系统

标签优先级为：

1. `runtime/pixiv/general_jp.json` 的人工 mappings；
2. `runtime/pixiv/jp_aliases.json` 的人工覆盖；
3. `pixiv_uploader/resources/pixiv/danbooru_jp.json.gz` 主词典；
4. Pixiv live lookup 与 `runtime/pixiv/tag_popularity.json` 缓存。

其他规则：

- `runtime/pixiv/tag_aliases.json`：语义组、过滤和文件名 token；
- `runtime/pixiv/age_rules.json`：文件名模式到年龄分级；
- `runtime/pixiv/validation_cases.json`：本地映射回归样本；完全重复的样本会替换旧记录；
- `runtime/pixiv/rule_fit/`：采样、manifest 与报告。

不要手改压缩主词典。普通翻译修正放入 `jp_aliases.json` 或 `general_jp.mappings`；只有 Pixiv 原生卖点才加入 `selling_points`。

## Tagger 与 metadata bridge

优先链：

```text
PixAITaggerBridge
  ↓ 不可用
StandaloneTaggerBridge / HainTagTaggerBridge
  ↓ 不可用
None（上传继续，缺少 tagger 候选）
```

metadata reader 与 tagger 独立：有 haintag 时使用 `HainTagBridge`，否则使用 `StandaloneMetadataReader`。

所有 tagger bridge 的 `predict_tags(path)` 应维持统一结果：`available`、`status`、`flat_tags`、`groups.general`、`groups.character`、`groups.copyright` 和诊断信息。PixAI 没有 copyright 分类时返回空列表，不改变 schema。

模型目录配置来自 `%APPDATA%/HainTag/settings.json`：

- `pixai_tagger_model_dir`：PixAI v0.9 ONNX；
- `tagger_model_dir`：CL / WD14 ONNX。

Tagger 设置向导：

```powershell
.venv/Scripts/python.exe -m pixiv_uploader.pixiv.setup_tagger
```

## LLM 文案

LLM 仅增强 Pixiv 的 `title_*` 和 `caption_*`，不负责 tag、年龄分级、打码、Civitai 过滤或发布动作。仅选择 Civitai 时整段跳过。

私有配置位于 `config.json.llm_reverse`。`manifest.pixiv.llm_reverse` 记录状态、人设、内容模式和不含凭据的错误摘要。SFW 模式遇到 R-18/R-18G 时应记录 `skipped_content_mode` 并保留规则文案；生成失败同样回落，不阻断上传。

## 自动打码、水印和安全规则

- `runtime/pixiv/censor.json`：`off`、`japan`、`strict` 三档预设；
- `runtime/watermark/config.json`：当前水印；字体和图片分别位于同级 `fonts/`、`images/`；
- `runtime/civitai/safety.json`：Civitai 不安全年龄分级与 minor / school 规则。

水印只修改 Pixiv sanitize 后的临时副本，不得修改 `upload/` 原图或 Civitai 保留 metadata 的副本。新增水印类型应注册到 `WatermarkRendererRegistry`，不要把 renderer 分支写进发布管线。

打码模型安装：

```powershell
.venv/Scripts/python.exe -m pixiv_uploader.pixiv.setup_censor
```

## 任务进度协议

`pixiv_uploader/task_progress.py` 是任务阶段、权重和聚合算法的单一来源。业务流程通过 `progress_callback(stage, **details)` 显式上报，不得再从日志文本（例如 `[1/3]`）推导进度。

任务快照的稳定字段包括：

- `progress`：`0..1` 的整体完成度；仅 `status=done` 可以等于 `1`；
- `stage` / `stage_progress`：稳定阶段 ID 与阶段内完成度；
- `stage_index` / `stage_count`：当前阶段与本任务动态阶段总数；
- `item_index` / `item_name`：当前处理对象，不代表已经成功；
- `current` / `total`：已经处理结束的对象数与总数；
- `succeeded` / `failed` / `canceled`：互斥结果计数；
- `result`：命令返回的结构化批次汇总。

上传任务按目标平台和 LLM 开关动态构造阶段，按“初始化 4% + 所有图片加权阶段 94% + 收尾 2%”聚合。多图任务先完成当前图片的真实阶段，再进入下一张；失败或取消保留在最后到达的阶段和百分比，不伪装成 100%。长耗时步骤只显示活动动画，不用定时器制造虚假数值。

新增阶段时：先在 `STAGE_LABELS` 和对应 `ProgressProfile` 注册稳定 ID 与权重，再在领域边界上报事件，最后同步 `frontend/src/locales.js` 和 `tests/test_task_progress.py`。阶段文案由前端本地化，后端 `stage_label` 只作兼容回退。

## Web 后端约束

- 同一时间只运行一个重型上传任务，防止浏览器 profile、模型内存和运行目录互相争用；
- `TASKS_LOCK` 保护任务状态；SSE 发送快照，不向外暴露线程和取消对象；
- `InterruptedError` 映射为 `canceled`，其他异常保留原始错误并映射为 `failed`；
- 页面关闭后只有在无 SSE 客户端、Scheduler 已取消、任务空闲时才退出；
- Scheduler 状态保存在 `config.json.scheduler`，测试后必须关闭 `enabled`。

前端固定文案必须进入 `frontend/src/locales.js`，组件通过 `frontend/src/i18n.jsx` 的 `useI18n()` 获取。不要在 JSX 或 API 判断中硬编码中文。

## 扩展原则

### 新增平台

1. 在平台集合和 `PLATFORM_RULES` 声明能力；
2. 为 manifest 增加独立平台状态；
3. 接入准备与发布函数；
4. 同步 CLI、Web 校验、Scheduler、i18n 和契约测试；
5. 保证一个平台失败不会抹掉另一个平台已经成功的状态。

### 新增默认规则

1. 修改 `pixiv_uploader/resources/<domain>/` 中的种子文件；
2. 如果已有用户也必须获得新字段，在加载时做 schema merge 或显式版本迁移；
3. 不直接覆盖 `runtime/` 用户修改；
4. 增加资源完整性和迁移测试。

### 新增运行数据

通过 `RuntimePaths` 暴露路径，并明确它属于可删除缓存、可恢复状态还是用户配置。需要跨版本保留的数据必须纳入迁移和测试。

## 验证

前端完整检查：

```powershell
npm ci
npm run check:frontend
```

该命令执行语言包完整性检查、JSX 静态文案检查、i18n 测试和 esbuild 生产构建。

Python：

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

提交前至少确认：

- 根目录兼容入口和 `python -m pixiv_uploader` 均可导入；
- `pixiv_uploader/resources/` 没有被运行过程改写；
- 根目录没有重新生成 `logs/`、`manifests/`、`progress/`、`pixiv/` 等旧状态目录；
- `frontend/dist/flow-app.js` 与 `frontend/src/` 同步；
- `git status` 中不存在缓存、日志、临时媒体或凭据。

## 关键模块

| 符号 | 文件 | 职责 |
|---|---|---|
| `cmd_upload` | `pixiv_uploader/publishing.py` | 上传主流程 |
| `create_upload_manifest` | `pixiv_uploader/publishing.py` | 构建跨平台 manifest |
| `build_pixiv_payload` | `pixiv_uploader/pixiv/support.py` | 构建 Pixiv tag、标题和年龄分级 |
| `create_pixiv_post` | `pixiv_uploader/pixiv/support.py` | Pixiv 浏览器发布 |
| `ensure_runtime_layout` | `pixiv_uploader/runtime.py` | 创建目录和迁移旧状态 |
| `ensure_runtime_files` | `pixiv_uploader/pixiv/storage.py` | 初始化用户规则和资源路径 |
| `WatermarkService` | `pixiv_uploader/watermark.py` | 水印配置、资源与渲染入口 |
| `_arm_scheduler` | `pixiv_uploader/web.py` | Web Scheduler timer |
| `TaskProgressState` | `pixiv_uploader/task_progress.py` | 阶段配置、权重聚合与终态约束 |
| `_TaskProgressController` | `pixiv_uploader/web.py` | 领域进度事件到任务快照 / SSE 的桥接 |
| `api_stream` | `pixiv_uploader/web.py` | SSE 状态流 |
| `PixAITaggerBridge` | `pixiv_uploader/pixiv/pixai_tagger.py` | PixAI ONNX bridge |

## 外部状态

浏览器 profile 保持在用户目录，以便更新代码时不影响登录状态：

- Pixiv：`~/.civitai_splitter_pixiv_chrome`；
- Civitai：`~/.civitai_splitter_chrome`；
- Pixiv rule-fit：`~/.civitai_splitter_pixiv_rule_fit_chrome`。

Pixiv 使用 `https://www.pixiv.net`。Civitai 登录和导航使用 `civitai.red`；发布后的 URL 可能由站点显示为 `civitai.com`。
