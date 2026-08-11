# Pixiv Uploader

[简体中文](README.md) · [English](README_EN.md)

A local, **Pixiv-first** publishing workspace for artwork. It currently supports Pixiv and Civitai, with image preparation, tagging, copy generation, content processing, scheduling, and task tracking in one application.

> Forked from [1756141021/civitai-post-splitter](https://github.com/1756141021/civitai-post-splitter) and now independently maintained by [Aaalice233](https://github.com/Aaalice233).

## ✨ Features

- **Publishing workspace** — manage pending images, platform status, task progress, and activity logs
- **Pixiv publishing** — fills titles, captions, tags, age ratings, and original/fan-art settings
- **Civitai publishing** — publishes images and can split existing multi-image posts into individual posts
- **Smart tagging** — PixAI tagger with a CL / WD14 fallback chain
- **LLM copy generation** — generates Japanese Pixiv titles and captions through supported model APIs
- **R-18 processing** — mosaic, Gaussian blur, and black-bar censor modes
- **Text and image watermarks** — configurable position, size, opacity, margin, fonts, and transparent image marks
- **Scheduled publishing** — automatically processes the queue at randomized intervals
- **Safe retries and deduplication** — tracks results per image and per platform
- **Bilingual interface** — Simplified Chinese and English with system detection and live switching
- **Light and dark themes** — responsive layouts for desktop and mobile viewports

## 🌐 Supported platforms

| Platform | Publishing method | Auto-tagging | LLM copy | R-18 censoring | Watermarks |
|---|---|---:|---:|---:|---:|
| Pixiv | Browser automation | ✓ | ✓ | ✓ | ✓ |
| Civitai | API + browser | — | — | — | — |

The current build accepts only `pixiv` and `civitai` as publishing targets. Platform capabilities are kept behind explicit boundaries so more destinations can be added later.

## 🧰 Requirements

- Windows 10 / 11
- Python 3.10+
- Chrome
- Git
- Node.js 18+ (only required for frontend development)

## 📦 Installation

```powershell
git clone https://github.com/Aaalice233/pixiv-uploader.git
cd pixiv-uploader

py -3 -m venv .venv
.venv/Scripts/python.exe -m pip install --upgrade pip
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/patchright.exe install chromium
```

The base dependency set excludes the large Tagger and censor runtimes; their setup wizards install them only when requested.

R-18 auto-censoring is optional:

```powershell
.venv/Scripts/python.exe -m pip install ultralytics opencv-python
```

Then launch `run.bat` and choose **[4] Install / verify R-18 auto-censor** to check or install the model.

## 🚀 Quick start

### Web UI

Double-click:

```text
run_web.bat
```

Or start it manually:

```powershell
.venv/Scripts/python.exe web_server.py
```

Open [http://localhost:7788](http://localhost:7788) in your browser.

Recommended first-time setup:

1. Open **Settings → Accounts** and configure the Pixiv / Civitai login or API key
2. Choose censoring and watermark behavior under **Settings → Pixiv processing**
3. If copy generation is needed, configure the provider, Base URL, API key, and model under **Settings → LLM copy**
4. Place images in `upload/`, or drag them into the publish dialog
5. Select the target platforms and create a publishing task
6. Follow live stages, progress, errors, and logs in the task workspace

Do not close browser windows opened by the application while login or publishing is in progress. Account sessions stay in local browser profiles and are not uploaded to a project-owned server.

### CLI menu

Double-click `run.bat`, or run:

```powershell
.venv/Scripts/python.exe launcher.py
```

The menu provides:

```text
[1] Split a Civitai post
[2] Publish to Civitai + Pixiv
[3] Publish to Pixiv only
[4] Install / verify R-18 auto-censor
[5] Check for / pull updates
[6] Configure / download a tagger model
[7] Switch the Pixiv account
[8] Switch the Civitai account
[9] Configure / start scheduled publishing
[Q] Quit
```

### Direct CLI

```powershell
# Publish to Civitai and Pixiv
.venv/Scripts/python.exe civitai_splitter.py upload --targets civitai,pixiv --count 2

# Publish only to Pixiv, sorted by file name
.venv/Scripts/python.exe civitai_splitter.py upload --targets pixiv --sort name_asc

# Split one or more Civitai posts
.venv/Scripts/python.exe civitai_splitter.py split 123456
```

`--sort` accepts `random`, `name_asc`, `name_desc`, `time_asc`, and `time_desc`.

## 🖼️ Image lifecycle

```text
upload/  →  preparation and platform processing  →  done/
                         │
                         └─ runtime/
                            ├─ manifests/   publishing manifests and platform state
                            ├─ progress/    deduplication and resume records
                            ├─ tmp/         temporary sanitized / censored artifacts
                            └─ logs/        runtime logs
```

- The source moves to `done/` after all selected targets succeed
- If one platform fails, the image remains available for retry and completed targets are skipped
- The manifest records preparation and publishing results for each platform to aid diagnosis

## 🏷️ Taggers

Tagger priority:

```text
PixAI tagger → CL / WD14 tagger → metadata / filename candidates
```

Configure models from CLI menu **[6]** or the Web UI settings. Automatic PixAI download requires:

```powershell
.venv/Scripts/python.exe -m pip install huggingface_hub
```

The PixAI v0.9 model is approximately 1.27 GB; reserve enough disk space before downloading it.

## ✍️ LLM copy generation

Pixiv copy generation supports OpenAI-compatible endpoints, Anthropic, and Google Gemini configurations. Each persona can keep its own prompt, content mode, and defaults.

Sensitive settings are stored in the local `config.json`. It is ignored by Git; never commit API keys, cookies, or other credentials.

## 💧 Watermarks

Choose a text or image watermark under **Settings → Pixiv → Watermark**. Watermarks are applied only to cleaned Pixiv publishing copies; files in `upload/` stay unchanged.

- Text watermarks support imported fonts, font faces, colors, strokes, position, size, opacity, and margin
- Image watermarks support PNG, JPEG, and WebP, preserving alpha from transparent PNG files
- Imported fonts and images stay locally in `runtime/watermark/fonts/` and `runtime/watermark/images/`
- Removing an active watermark asset safely disables that configuration

## ⚙️ Configuration and data

| Path | Purpose |
|---|---|
| `config.json` | Local settings, API keys, scheduler, and LLM configuration |
| `runtime/civitai/safety.json` | Local Civitai safety rules |
| `runtime/pixiv/censor.json` | Local Pixiv censor presets and detection classes |
| `runtime/pixiv/*.json` | Local tag overrides, age rules, popularity cache, and validation data |
| `runtime/watermark/config.json` | Active text or image watermark configuration |
| `runtime/watermark/fonts/` | Locally imported fonts |
| `runtime/watermark/images/` | Locally imported watermark images |
| `pixiv_uploader/resources/pixiv/` | Versioned read-only Pixiv defaults and compressed dictionary |
| `pixiv_uploader/resources/civitai/` | Versioned read-only Civitai defaults |

Legacy runtime data from the repository root and `pixiv/` is migrated into `runtime/` on first launch without overwriting existing destination files.

## 🛠️ Frontend development

```powershell
npm ci
npm run check:frontend
```

`check:frontend` performs:

1. Chinese / English catalog and placeholder validation
2. Static JSX copy and untranslated Han-text checks
3. i18n unit tests
4. The production esbuild bundle

Run the Python test suite with:

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

See [docs/development.md](docs/development.md) for architecture and maintenance notes, and [CHANGELOG.md](CHANGELOG.md) for version history.

## ⚠️ Important notes

- Pixiv and Civitai operations rely on browser automation and may need updates when either site changes
- Verify copyright, platform rules, AI-generated content labels, and age ratings before publishing
- Use reasonable publishing intervals; do not use this project for spam, harassment, or bypassing platform restrictions
- This project is not affiliated with or endorsed by Pixiv, Civitai, or their operators

## 📄 License

Released under the [MIT License](LICENSE), with the original upstream copyright and license notice retained.
