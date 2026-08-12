# Pixiv Uploader

[简体中文](README.md) · [English](README_EN.md)

以 **Pixiv** 为核心的本地作品发布工作台。目前支持 Pixiv 与 Civitai，可完成图片整理、标签识别、文案生成、内容处理、定时发布和任务追踪。

> 本项目 fork 自 [1756141021/civitai-post-splitter](https://github.com/1756141021/civitai-post-splitter)，现由 [Aaalice233](https://github.com/Aaalice233) 独立维护。

## ✨ 功能概览

- **页面化工作台**：侧边栏统一切换发布、Civitai 拆分、任务中心、运行记录和设置页面
- **Pixiv 发布**：自动填写标题、说明、标签、年龄分级和原创/二创选项
- **Civitai 发布**：支持发布图片，也可把已有多图帖子拆成单图帖子
- **智能标签**：支持 PixAI、CL / WD14 本地 tagger；启用 LLM 时还会生成视觉标签并接入同一套 Pixiv 规范化与热度排序管线
- **LLM 文案与标签**：生成 Pixiv 日文标题、说明和视觉标签，内置请求重试、响应修复和后备模型故障转移
- **R-18 内容处理**：支持 mosaic、Gaussian blur 和 black bar 三种打码方式
- **图文水印**：支持自定义文字与透明图片水印，可调整位置、大小、透明度和边距
- **定时发布**：按随机时间间隔自动处理发布队列
- **断点与去重**：按图片和平台记录发布结果，失败后可安全重试
- **双语界面**：内置简体中文和 English，支持系统语言检测与即时切换
- **深浅主题**：支持 Dark / Light 主题以及桌面端、移动端布局

## 🌐 当前支持的平台

| 平台 | 发布方式 | 自动标签 | LLM 文案 | R-18 打码 | 图文水印 |
|---|---|---:|---:|---:|---:|
| Pixiv | 浏览器自动化 | ✓ | ✓ | ✓ | ✓ |
| Civitai | API + 浏览器 | — | — | — | — |

当前发布目标仅允许 `pixiv` 和 `civitai`。平台能力已按独立边界组织，后续可以继续扩展新的发布目标。

## 🧰 环境要求

- Windows 10 / 11
- Python 3.10+
- Chrome
- Git
- Node.js 18+（仅修改前端时需要）

## 📦 安装

```powershell
git clone https://github.com/Aaalice233/pixiv-uploader.git
cd pixiv-uploader

py -3 -m venv .venv
.venv/Scripts/python.exe -m pip install --upgrade pip
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/patchright.exe install chromium
```

基础依赖不包含体积较大的 Tagger 和打码运行库；需要时由 CLI 向导或 **设置 → 系统维护** 按需安装。

R-18 自动打码为可选功能：

```powershell
.venv/Scripts/python.exe -m pip install ultralytics opencv-python
```

随后运行 `run.bat`，选择 **[4] 安装 / 检查 R-18 自动打码**，程序会检查并引导安装模型。

## 🚀 快速开始

### Web UI

双击：

```text
run_web.bat
```

或手动启动：

```powershell
.venv/Scripts/python.exe web_server.py
```

浏览器打开 [http://localhost:7788](http://localhost:7788)。侧边栏只负责切换独立页面；R-18 打码模型安装和项目更新等环境操作统一位于 **设置 → 系统维护**，不会混入发布任务。

首次使用建议按以下顺序操作：

1. 打开 **设置 → 通用**，完成 Pixiv / Civitai 登录或 API key 配置
2. 在 **设置 → Pixiv 处理** 中选择打码和水印策略
3. 如需自动文案和视觉标签，在 **设置 → LLM 文案与标签** 中填写服务商、Base URL、API key 与模型，并按需调整多级重试策略
4. 把图片放入 `upload/`，或直接拖进发布窗口
5. 在 **发布工作台** 选择发布平台并创建任务
6. 在 **任务中心** 查看整批总进度、当前图片、总图片数和错误，在 **运行记录** 查看或复制完整日志

登录与发布期间不要关闭程序打开的浏览器窗口。账号信息保存在本机浏览器 Profile 中，不会上传到本项目的服务器。

### Pixiv 登录、验证与自动续跑

- **首次登录**：在 **设置 → 通用 → 发布账号** 点击 Pixiv 登录，只需在 Pixiv 页面完成登录。程序会以真实投稿页和文件上传框验证会话；验证成功后浏览器自动关闭，不需要回到终端按 Enter 或在 Web 页面点“继续”。仅创建了 Profile、但尚未真实验证时，账号卡会显示“待验证”而不是“已登录”。
- **长期复用会话**：登录和投稿都使用系统 Chrome 与同一个稳定的持久 Profile。一个 Pixiv 批次只启动一个浏览器 Context 并跨图片复用；请尽量保持设备、Profile 和网络出口稳定，不要同时用外部 Chrome 打开该 Profile。
- **登录过期**：任务会自动切换为“等待操作”并打开 Pixiv 登录页。用户在 Pixiv 页面登录后，程序会重新验证投稿页并自动继续当前图片；等待上限为 15 分钟，可直接从任务中心取消。
- **CAPTCHA**：投稿前出现验证时，完成验证后程序只会自动点击一次投稿；投稿按钮已经点击后出现验证时，请按 Pixiv 页面要求完成验证，并仅在 Pixiv 明确要求时手动点击一次投稿。程序不会自动二次提交，避免重复作品。CAPTCHA 等待上限为 10 分钟，期间整个 Pixiv 队列暂停。
- **自适应冷却**：普通成功后按 `--delay` 基准加入 `0.8–1.4` 倍抖动（默认约 8–14 秒）。CAPTCHA 或 HTTP 429 会把下一次投稿提升为 2–5、5–10 或 15–30 分钟的风险冷却，并优先遵守更长的 `Retry-After`；连续 3 次无风险成功或 24 小时无新风险会逐级恢复。风险状态会持久化，重启后也不会跳过尚未结束的冷却。
- **安全归档与终止**：确认所有目标发布成功后，原图必须成功移入 `done/` 才算任务完成，不会继续留在 `upload/`；移动失败会显式报错。关闭浏览器、超时或取消会立即停止当前 Pixiv 批次并保留未确认图片。投稿已经点击但结果无法确认时会记录为 `maybe_posted`，禁止自动重试，需先在 Pixiv 作品页人工核对。

设置页会显示 Pixiv 会话的“未建立 / 待验证 / 验证中 / 已登录 / 需重新登录 / 使用中 / 验证失败”、最后验证时间、风险等级和冷却截止时间；任务中心会实时显示登录、CAPTCHA 或冷却原因及剩余时间。

### CLI 菜单

双击 `run.bat`，或执行：

```powershell
.venv/Scripts/python.exe launcher.py
```

菜单提供：

```text
[1] 拆分 Civitai 帖子
[2] 上传到 Civitai + Pixiv
[3] 仅上传到 Pixiv
[4] 安装 / 检查 R-18 自动打码
[5] 检查 / 拉取更新
[6] 配置 / 下载 Tagger 模型
[7] 切换 Pixiv 账号
[8] 切换 Civitai 账号
[9] 配置 / 启动定时发布
[Q] 退出
```

### 直接命令行

```powershell
# 同时发布到 Civitai 与 Pixiv
.venv/Scripts/python.exe civitai_splitter.py upload --targets civitai,pixiv --count 2

# 仅发布到 Pixiv，并按文件名排序
.venv/Scripts/python.exe civitai_splitter.py upload --targets pixiv --sort name_asc

# 拆分一个或多个 Civitai 帖子
.venv/Scripts/python.exe civitai_splitter.py split 123456
```

`--sort` 支持：`random`、`name_asc`、`name_desc`、`time_asc`、`time_desc`。

## 🖼️ 图片生命周期

```text
upload/  →  发布准备与平台处理  →  done/
                  │
                  └─ runtime/
                     ├─ manifests/   发布清单与平台状态
                     ├─ progress/    去重和断点记录
                     ├─ tmp/         Pixiv 清洗 / 打码临时产物
                     └─ logs/        运行日志
```

- 成功完成全部目标后，原图会移动到 `done/`
- 某个平台失败时，图片会保留在待处理状态，重试时跳过已经成功的平台
- Pixiv 已点击投稿但结果不确定时，图片保持原位并标记为 `maybe_posted`，不会自动再次投稿
- `manifest` 会记录每个平台的准备结果、发布状态和可定位错误码，便于定位问题

## 🏷️ Tagger

优先级为：

```text
PixAI tagger → CL / WD14 tagger → metadata / 文件名候选
                                      + LLM 视觉标签（启用时）
                                      ↓
                          Pixiv 规范化 / 去重 / 热度排序（最多 10 个）
```

通过 CLI 菜单 **[6]** 或 Web UI 设置页配置模型。PixAI 自动下载需要：

```powershell
.venv/Scripts/python.exe -m pip install huggingface_hub
```

PixAI v0.9 模型约 1.27 GB，请预留磁盘空间。应用会验证已配置目录，并自动发现当前项目 `models/` 或 HainTag 数据目录中的有效模型；失效的旧绝对路径不会再被执行。

## ✍️ LLM 文案与视觉标签

Pixiv 反推支持 OpenAI 兼容接口，也支持 Anthropic 和 Google Gemini 配置。每个人设可独立配置提示词、内容模式和默认行为。模型必须返回完整的双语文案和至少 6 个不同、可用的视觉关键词；缺字段、重复堆叠或只含保留标记时会自动进入结构修复。视觉关键词会清理 `#`、URL、重复项以及由程序统一维护的 `オリジナル` / `オリジナルイラスト` / `AIイラスト` 标记，再与本地 tagger、metadata 和文件名候选共同进入 Pixiv 标签规范化、日文映射、去重与热度排序；最终严格保留最多 10 个标签。即使没有安装本地 tagger，只要启用了 LLM，仍会自动补充图像内容标签。

人设参考示例会按当前平台输出结构显示日文/中文标题、双语简介和视觉标签。示例只有在所有必填项以及至少 6 个不同标签均完整时才会发送给模型；未完成的草稿会在界面标记并安全忽略，避免半份示例误导生成结果。

视觉请求只发送最长边 1536 px 的 JPEG 预览，以降低大图请求的耗时和内存占用；发布原图不受影响。反推采用三层容错：短暂网络故障、限流和服务端错误先按指数退避重试；空响应或无效 JSON 再进入结构修复；主模型仍失败时才按配置顺序切换后备模型。认证和权限错误立即停止。可在 **设置 → LLM 文案与标签 → 多级重试与故障转移** 调整单次超时、请求次数、修复次数、总时间预算和最多 3 个后备模型；任务中心会显示当前请求、等待、修复或模型切换状态。

敏感配置保存在本机 `config.json`。该文件已被 Git 忽略，请不要主动提交 API key、Cookie 或其他凭据。

## 💧 水印

在 **设置 → Pixiv 处理 → 水印** 中可以选择文字或图片水印。水印只写入 Pixiv 清洗后的发布副本，不修改 `upload/` 中的原图：

- 文字水印支持字体导入、字型、颜色、描边、位置、大小、透明度和边距
- 图片水印支持 PNG、JPEG 与 WebP；透明 PNG 会保留 alpha 通道
- 导入的字体与图片分别保存在本机 `runtime/watermark/fonts/` 和 `runtime/watermark/images/`
- 删除当前正在使用的水印资源时，应用会同步回退到安全的禁用状态

## ⚙️ 主要配置与数据

| 路径 | 作用 |
|---|---|
| `config.json` | 本机设置、API key、Scheduler 和 LLM 配置 |
| `runtime/civitai/safety.json` | 本机 Civitai 内容安全规则 |
| `runtime/pixiv/censor.json` | 本机 Pixiv 打码预设与检测类别 |
| `runtime/pixiv/session.json` | Pixiv 最后一次真实会话验证结果与时间 |
| `runtime/pixiv/risk_state.json` | Pixiv 风险等级、成功计数与跨重启冷却状态 |
| `runtime/pixiv/*.json` | 本机标签覆盖、年龄规则、流量缓存与回归数据 |
| `runtime/watermark/config.json` | 当前文字或图片水印配置 |
| `runtime/watermark/fonts/` | 本机导入的字体文件 |
| `runtime/watermark/images/` | 本机导入的水印图片 |
| `pixiv_uploader/resources/pixiv/` | 随版本发布的只读 Pixiv 默认规则和压缩词典 |
| `pixiv_uploader/resources/civitai/` | 随版本发布的只读 Civitai 默认规则 |

旧版散落在根目录和 `pixiv/` 下的运行数据会在首次启动时自动迁移到 `runtime/`，不会覆盖已存在的新数据。

## 🛠️ 前端开发

```powershell
npm ci
npm run check:frontend
```

`check:frontend` 会依次执行：

1. 中英文语言包键值与插值变量校验
2. JSX 固定文案和遗漏中文检查
3. i18n 单元测试
4. esbuild 生产构建

Python 测试：

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

更多模块约定与维护说明见 [docs/development.md](docs/development.md)，版本变化见 [CHANGELOG.md](CHANGELOG.md)。

## ⚠️ 注意事项

- 本项目使用浏览器自动化完成 Pixiv / Civitai 操作，站点页面更新后可能需要同步适配
- 发布前请自行确认作品版权、平台规则、AI 生成内容标记和年龄分级
- 请合理控制发布频率，不要使用本项目进行垃圾信息、批量骚扰或规避平台限制
- 本项目与 Pixiv、Civitai 及其运营方没有隶属或官方合作关系

## 📄 许可证

本项目基于 [MIT License](LICENSE) 发布，并保留上游项目的版权与许可声明。
