# 🤖 项目代理规范

## 🚀 Release 发布职责

- 项目版本唯一来源是 `pixiv_uploader/version.py` 中的 `__version__`。
- Git 标签统一为 `v{version}`，GitHub Release 标题统一为 `Pixiv Uploader v{version}`；版本号可使用 `1.0`、`1.2.3` 或 `1.2.3-beta.1`，不要在 `version.py` 中写前缀 `v`。
- 发布必须通过 `.github/workflows/release.yml` 完成，不手工上传附件或绕过版本、更新日志校验。
- 正式发布只允许从 `main` 执行。发布前工作区内容必须全部提交并推送，不能从本机未提交文件构建 Release。
- 用户负责发布前功能测试；代理不代跑测试或浏览器验证。Workflow 只做版本、更新日志、标签和发布产物的完整性校验。

## 📝 更新日志原则

Release 更新日志面向最终用户，不是开发报告：

- 使用 Markdown；每个标题必须以 emoji 开头，例如 `## ✨ 新功能`、`## 🐛 问题修复`。
- 先写用户能感知到的结果，避免罗列文件名、函数名、内部模块、测试数量、提交哈希或重构过程。
- 同一小节最多显示 6 条；超出部分合并为一句概括，避免信息轰炸。
- 默认只展示 `feat`、`fix`、`perf` 和 breaking change：
  - `feat` → `## ✨ 新功能`
  - `fix` → `## 🐛 问题修复`
  - `perf` → `## ⚡ 体验优化`
  - `type!:` 或正文含 `BREAKING CHANGE:` → `## ⚠️ 重要变化`
- `chore`、`ci`、`docs`、`test`、`style`、`build` 和纯 `refactor` 默认不进入用户更新日志。
- 提交标题应使用 `type(scope): 中文用户可读描述`。需要进入 Release 的改动，提交标题本身就必须说清用户获得了什么或什么问题被修复。
- 可在提交标题加入 `[skip release notes]`，显式排除不适合面向用户展示的提交。

## 🧮 更新日志计算

`scripts/release_tools.py` 是版本校验和 Release Notes 生成的唯一实现：

1. 优先选取当前发布提交可达的、低于当前版本的最高 `v*` 版本标签作为计算起点。
2. 首次发布没有旧标签时，使用 `.github/release-config.json` 的 `initial_base`；该提交是项目从上游拆分为 Pixiv Uploader 前的边界，不得随意修改。
3. 只读取起点到发布提交之间 first-parent 上的非 merge 提交，解析 Conventional Commit 标题。
4. 去掉 `type(scope):` 技术前缀，按用户可见分类去重、限量并生成 Markdown。
5. 自动追加简短的下载、升级和校验说明；有上一版本时追加 GitHub Compare 链接。
6. Release Notes 不直接复制完整 `git log`，也不使用 GitHub 默认的全量提交列表。

本地只预览元数据或日志时可运行：

```powershell
.venv/Scripts/python.exe scripts/release_tools.py validate --version 1.0 --to-ref HEAD
.venv/Scripts/python.exe scripts/release_tools.py notes --version 1.0 --to-ref HEAD --output release-notes.md
```

`release-notes.md` 是临时生成物，不应提交；正式发布由 Workflow 在干净的 GitHub runner 中重新计算。

## 📋 发布前准备

1. 确认即将发布的用户可见改动已经使用 `feat` / `fix` / `perf` 等规范提交。
2. 把 `pixiv_uploader/version.py` 更新为目标版本。
3. 在 `CHANGELOG.md` 顶部保留空的 `## 🧪 Unreleased`，并新增 `## 🚀 {version} - YYYY-MM-DD`。版本小节继续使用带 emoji 的用户向标题和简短描述。
4. 更新 README 或开发文档中受影响的公开行为；不要把凭据、本机 `config.json`、`runtime/`、模型或上传图片提交进仓库。
5. 提交并推送 `main`，再启动发布 Workflow。

## 📦 Workflow 行为

`.github/workflows/release.yml` 支持两种入口：

- 推荐：在 `main` 上手动执行 `workflow_dispatch`，输入版本号。
- 兼容：推送合法的 `v*` 标签后自动执行；标签对应版本仍必须与 `version.py` 和 `CHANGELOG.md` 一致。

Workflow 会按顺序：

1. 校验版本格式、`version.py` 和 `CHANGELOG.md` 版本标题一致；
2. 自动计算面向用户的 Markdown 更新日志；
3. 用 `git archive` 生成 `pixiv-uploader-v{version}.zip`，只包含已提交文件；
4. 生成同名 `.sha256` 校验文件；
5. 创建或核对 annotated tag；
6. 使用 GitHub 内置 `GITHUB_TOKEN` 发布 Release 和两个附件。

发布包不会包含被 Git 忽略的 `config.json`、`runtime/`、`.venv/`、`node_modules/`、模型、待上传图片和本机凭据。

## ▶️ 正式发布命令

以 `1.0` 为例：

```powershell
gh workflow run release.yml `
  --repo Aaalice233/pixiv-uploader `
  --ref main `
  -f version=1.0 `
  -f draft=false `
  -f prerelease=false
```

需要预发布时使用带后缀的版本并设置 `prerelease=true`；需要人工审核正文时设置 `draft=true`。`from_ref` 通常留空，只有修复错误的历史边界时才显式覆盖。

发布后检查：

```powershell
gh run list --repo Aaalice233/pixiv-uploader --workflow release.yml --limit 1
gh release view v1.0 --repo Aaalice233/pixiv-uploader
```

## 🛡️ 失败与重跑

- 同名 Release 已存在时 Workflow 必须失败，禁止静默覆盖。
- 同名标签已存在但指向其他提交时必须失败，禁止移动已公开版本标签。
- Release 创建失败但标签已经正确推送时，可以直接重跑同一版本；Workflow 会复用指向同一提交的标签。
- 标签指错提交时不要强推覆盖。先停止发布、确认是否已有用户下载，再决定发布新的修订版本。
- Release 对外通知后不要修改同版本附件或更新日志；任何用户可见修正都发布新版本。
- 若本次发布尚未对外通知、确认附件下载数为 0，且仅发现自动生成正文缺失，可在发布交付前修正正文；仍禁止移动标签或替换附件。
