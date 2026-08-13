# Pixiv Uploader

[简体中文](README.md) · [English](README_EN.md)

A local, **Pixiv-first** publishing workspace for artwork. It currently supports Pixiv and Civitai, with image preparation, tagging, copy generation, content processing, scheduling, and task tracking in one application.

> Forked from [1756141021/civitai-post-splitter](https://github.com/1756141021/civitai-post-splitter) and now independently maintained by [Aaalice233](https://github.com/Aaalice233).

## ✨ Features

- **Unified publishing workspace** — create tasks, watch live progress, inspect per-image results, cancel, retry failures, and remove records on one page
- **Pixiv publishing** — fills titles, captions, tags, age ratings, and original/fan-art settings
- **Civitai publishing** — publishes local images as Civitai works
- **Smart tagging** — PixAI and CL / WD14 local taggers, plus LLM-generated visual tags routed through the same Pixiv normalization and popularity-ranking pipeline
- **LLM copy and tags** — generates Japanese Pixiv titles, captions, and visual tags with request retries, response repair, and fallback-model failover
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

The base dependency set excludes the large Tagger and censor runtimes; install them only when needed through the CLI wizard or **Settings → System maintenance**.

R-18 auto-censoring is optional:

```powershell
.venv/Scripts/python.exe -m pip install ultralytics opencv-python
```

Then launch `run.bat` and choose **[3] Install / verify R-18 auto-censor** to check or install the model.

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

Open [http://localhost:7788](http://localhost:7788) in your browser. The sidebar contains only **Publishing workspace, Activity log, and Settings**; environment operations such as installing the R-18 censor model or updating the project live under **Settings → System maintenance** and never appear as publishing tasks.

Recommended first-time setup:

1. Open **Settings → General** and configure the Pixiv / Civitai login or API key
2. Choose censoring and watermark behavior under **Settings → Pixiv processing**
3. For automatic copy and visual tags, configure the provider, Base URL, API key, model, and optional retry policy under **Settings → LLM copy & tags**
4. Place images in `upload/`, or drag them into the publish dialog
5. Select the target platforms in the **Publishing workspace** and create a task
6. Stay in the **Publishing workspace** to follow batch progress, per-image results, and errors, then inspect or copy full logs from **Activity log**

Do not close browser windows opened by the application while login or publishing is in progress. Account sessions stay in local browser profiles and are not uploaded to a project-owned server.

### Pixiv sign-in, challenges, and automatic resume

- **First sign-in** — click Pixiv sign-in under **Settings → General → Publishing accounts**, then complete the flow only on Pixiv. The app verifies the real submission page and file input; once verified, the browser closes automatically. No terminal Enter key or Web “Continue” button is required. A Profile that exists but has never passed this check is shown as “Verification pending,” not signed in.
- **Long-lived session** — sign-in and publishing both use system Chrome with one stable persistent Profile. Each Pixiv batch launches one browser Context and reuses it across images. Keep the device, Profile, and network egress stable, and do not open that Profile in an external Chrome instance at the same time.
- **Expired session** — the task moves to “Action needed” and opens Pixiv sign-in. After you sign in on Pixiv, the app verifies the submission page again and automatically resumes the current image. The wait limit is 15 minutes and remains cancelable from the Publishing workspace.
- **CAPTCHA** — for a challenge before submission, the app performs exactly one automatic submit after you solve it. If the challenge appears after Submit was already clicked, complete it on Pixiv and click Submit once only when Pixiv explicitly asks. The app never submits a second time automatically. The CAPTCHA limit is 10 minutes, and the whole Pixiv queue pauses while waiting.
- **Adaptive cooldown** — normal success adds `0.8–1.4×` jitter around `--delay` (about 8–14 seconds by default). CAPTCHA or HTTP 429 raises the next cooldown to 2–5, 5–10, or 15–30 minutes and honors a longer `Retry-After` when provided. Three risk-free successes or 24 hours without a new risk signal lower the level. Risk and pending cooldown survive application restarts.
- **Safe archival and termination** — an image counts as complete only after every selected target is confirmed and the source has moved into `done/`; confirmed uploads do not remain in `upload/`, and an archival failure is reported explicitly. Closing the browser, timing out, or canceling stops the Pixiv batch and keeps unconfirmed images. If Submit was clicked but success cannot be confirmed, the manifest records `maybe_posted` and automatic retry is forbidden until you check the Pixiv artworks page.

The account card reports “Not set up / Verification pending / Verifying / Signed in / Sign-in required / In use / Verification failed,” along with last verification, risk level, and cooldown. The Publishing workspace shows the current sign-in, CAPTCHA, or cooldown reason and remaining time in real time.

### CLI menu

Double-click `run.bat`, or run:

```powershell
.venv/Scripts/python.exe launcher.py
```

The menu provides:

```text
[1] Publish to Civitai + Pixiv
[2] Publish to Pixiv only
[3] Install / verify R-18 auto-censor
[4] Check for / pull updates
[5] Configure / download a tagger model
[6] Switch the Pixiv account
[7] Switch the Civitai account
[8] Configure / start scheduled publishing
[Q] Quit
```

### Direct CLI

```powershell
# Publish to Civitai and Pixiv
.venv/Scripts/python.exe civitai_splitter.py upload --targets civitai,pixiv --count 2

# Publish only to Pixiv, sorted by file name
.venv/Scripts/python.exe civitai_splitter.py upload --targets pixiv --sort name_asc
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
- If Pixiv accepted a Submit click but the outcome is uncertain, the image stays in place with `maybe_posted` and is never resubmitted automatically
- The manifest records preparation results, publishing status, and actionable error codes for each platform

## 🏷️ Taggers

Tagger priority:

```text
PixAI tagger → CL / WD14 tagger → metadata / filename candidates
                                      + LLM visual tags (when enabled)
                                      ↓
                       Pixiv normalization / dedup / ranking (max 10)
```

Configure models from CLI menu **[5]** or the Web UI settings. Automatic PixAI download requires:

```powershell
.venv/Scripts/python.exe -m pip install huggingface_hub
```

The PixAI v0.9 model is approximately 1.27 GB; reserve enough disk space before downloading it. The app validates configured directories and discovers valid models under the current project's `models/` or HainTag data directory; stale absolute paths are never executed.

## ✍️ LLM copy and visual tags

Pixiv generation supports OpenAI-compatible endpoints, Anthropic, and Google Gemini configurations. Each persona can keep its own prompt, content mode, and defaults. The model must return complete bilingual copy and at least six distinct, usable visual keywords; missing fields, duplicate padding, or reserved-only tags trigger a structure-repair round. Returned visual keywords are sanitized to remove hashtags, URLs, duplicates, and the program-managed `オリジナル` / `オリジナルイラスト` / `AIイラスト` markers. They are then merged with local tagger, metadata, and filename candidates before Pixiv normalization, Japanese alias mapping, deduplication, and popularity ranking; no more than ten final tags are kept. Automatic content tags therefore still work with LLM enabled when no local tagger model is installed.

Persona reference examples expose every output field from the active platform schema: Japanese and Chinese titles, both captions, and visual tags. An example is sent to the model only after every required field and at least six distinct tags are complete; unfinished drafts are marked in the UI and safely ignored.

Vision requests use a JPEG preview with a 1536 px maximum edge to reduce latency and memory usage for large images; the publishing source remains unchanged. Generation uses three recovery levels: transient network, rate-limit, and server failures are retried with exponential backoff; empty or malformed JSON responses enter a repair round; and only then does the app move through configured fallback models. Authentication and permission failures stop immediately. **Settings → LLM copy & tags → Multi-level retry and failover** exposes the per-request timeout, retry and repair counts, total time budget, and up to three fallback models. The Publishing workspace reports request, wait, repair, and failover activity in real time.

Sensitive settings are stored in the local `config.json`. It is ignored by Git; never commit API keys, cookies, or other credentials.

## 💧 Watermarks

Choose a text or image watermark under **Settings → Pixiv processing → Watermark**. Watermarks are applied only to cleaned Pixiv publishing copies; files in `upload/` stay unchanged.

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
| `runtime/pixiv/session.json` | Last verified Pixiv session result and timestamp |
| `runtime/pixiv/risk_state.json` | Pixiv risk level, safe-success count, and restart-safe cooldown |
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
