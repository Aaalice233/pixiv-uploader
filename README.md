# Pixiv Uploader

[简体中文](README.md) · [English](README_EN.md)

以 **Pixiv** 为核心的本地作品发布工作台。目前支持 Pixiv 与 Civitai，可完成图片整理、标签识别、文案生成、内容处理、定时发布和任务追踪。

> 本项目 fork 自 [1756141021/civitai-post-splitter](https://github.com/1756141021/civitai-post-splitter)，现由 [Aaalice233](https://github.com/Aaalice233) 独立维护。

## ✨ 功能概览

- **发布工作台**：集中管理待发布图片、平台状态、任务进度和运行日志
- **Pixiv 发布**：自动填写标题、说明、标签、年龄分级和原创/二创选项
- **Civitai 发布**：支持发布图片，也可把已有多图帖子拆成单图帖子
- **智能标签**：支持 PixAI tagger，以及 CL / WD14 tagger 回退链
- **LLM 文案**：通过 OpenAI 兼容接口生成 Pixiv 日文标题与说明
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

基础依赖不包含体积较大的 Tagger 和打码运行库；需要时由菜单向导按需安装。

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

浏览器打开 [http://localhost:7788](http://localhost:7788)。

首次使用建议按以下顺序操作：

1. 打开 **设置 → 账号**，完成 Pixiv / Civitai 登录或 API key 配置
2. 在 **设置 → Pixiv 处理** 中选择打码和水印策略
3. 如需自动文案，在 **设置 → LLM 文案** 中填写服务商、Base URL、API key 与模型
4. 把图片放入 `upload/`，或直接拖进发布窗口
5. 选择发布平台并创建任务
6. 在任务区查看实时阶段、进度、错误和运行日志

登录与发布期间不要关闭程序打开的浏览器窗口。账号信息保存在本机浏览器 profile 中，不会上传到本项目的服务器。

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
- `manifest` 会记录每个平台的准备结果与发布状态，便于定位问题

## 🏷️ Tagger

优先级为：

```text
PixAI tagger → CL / WD14 tagger → metadata / 文件名候选
```

通过 CLI 菜单 **[6]** 或 Web UI 设置页配置模型。PixAI 自动下载需要：

```powershell
.venv/Scripts/python.exe -m pip install huggingface_hub
```

PixAI v0.9 模型约 1.27 GB，请预留磁盘空间。

## ✍️ LLM 文案

Pixiv 文案生成支持 OpenAI 兼容接口，也支持 Anthropic 和 Google Gemini 配置。每个人设可独立配置提示词、内容模式和默认行为。

敏感配置保存在本机 `config.json`。该文件已被 Git 忽略，请不要主动提交 API key、Cookie 或其他凭据。

## 💧 水印

在 **设置 → Pixiv → 水印** 中可以选择文字或图片水印。水印只写入 Pixiv 清洗后的发布副本，不修改 `upload/` 中的原图：

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
