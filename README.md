# 🤖 AI Lead Bot

<div align="center">

[![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![aiogram](https://img.shields.io/badge/aiogram-3.31-009EFF?style=flat-square&logo=telegram&logoColor=white)](https://aiogram.dev)
[![Gemini](https://img.shields.io/badge/Gemini%20Flash-Cascade%20Fallback-4285F4?style=flat-square&logo=google&logoColor=white)](https://ai.google.dev)
[![aiosqlite](https://img.shields.io/badge/aiosqlite-0.21.0-003B57?style=flat-square&logo=sqlite&logoColor=white)](https://github.com/omnilib/aiosqlite)
[![Tests](https://img.shields.io/badge/Tests-111%20Passed-success?style=flat-square&logo=pytest&logoColor=white)](https://pytest.org)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED?style=flat-square&logo=docker&logoColor=white)](https://docker.com)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)

**A production-grade, asynchronous Telegram bot that acts as an AI-powered sales consultant & lead generator.**  
Powered by a resilient Google Gemini Flash cascade, dialogue memory, asynchronous SQLite (`aiosqlite`), Google Sheets integration, and 100% inline keyboard UI/UX.

[Features](#-features) · [Tech Stack](#-tech-stack) · [Architecture](#-architecture) · [Quick Start](#-quick-start) · [Testing](#-testing--quality-assurance) · [Docker](#-docker-deployment) · [Configuration](#%EF%B8%8F-configuration)

</div>

---

## ✨ Features

| Feature | Details |
|---|---|
| 🧠 **AI Consultant & Fallback Cascade** | 3-tier model cascade (`gemini-3.8-flash` ➔ `gemini-2.5-flash` ➔ `gemini-flash-latest`) with 1.5 s fast retry on 503 / 429 overloads |
| 💬 **Dialogue Memory** | Remembers the last 8 message pairs per user session for natural multi-turn context |
| 🌐 **Bilingual (UA / EN)** | Ukrainian 🇺🇦 (default) and English 🇬🇧 with interactive inline language selector; stored in SQLite |
| 📋 **Lead Qualification FSM** | 3-step conversational form with phone regex and input validation |
| 📊 **Google Sheets Sync** | Real-time lead capture appended to Google Sheets via service account |
| 🔔 **Admin Alerts & Analytics** | Instant Telegram notification on new leads + `/stats` analytics command |
| ⚡ **Fully Asynchronous** | Non-blocking database operations powered by `aiosqlite` without freezing the event loop |
| 🛡 **Anti-Spam Protection** | In-memory sliding window rate limiter (2 s cooldown) with automated TTL cleanup |
| 🧪 **Comprehensive Test Suite** | 111 automated unit & integration tests covering 100% of business logic |
| 🐳 **Docker Production-Ready** | Multi-stage slim Docker image with persistent SQLite named volume |

---

## 🛠 Tech Stack

| Layer | Technology | Details |
|---|---|---|
| **Language** | Python 3.11+ | Strict typing (PEP 484, PEP 604), modern syntax |
| **Bot Framework** | [aiogram 3.31](https://docs.aiogram.dev/en/latest/) | Async Telegram Bot API, Routers, FSM, Middlewares |
| **AI / LLM** | [Google Gemini API](https://ai.google.dev) via `google-genai` SDK | 3-tier cascade (`3.8-flash` ➔ `2.5-flash` ➔ `flash-latest`) with 1.5s retry |
| **Database** | [aiosqlite 0.21](https://github.com/omnilib/aiosqlite) | Fully async SQLite client for history, user preferences & stats |
| **Lead Storage** | [Google Sheets API](https://developers.google.com/sheets) via `gspread` | Automated service account authentication & row appending |
| **Localization (i18n)** | Custom zero-dependency module | Ukrainian (`uk`) and English (`en`), prompt adapted to A2-B1 English |
| **Testing & Quality** | `pytest`, `pytest-asyncio`, `ruff` | 111 unit & integration tests (100% pass rate) |
| **Deployment** | Docker & Docker Compose | Lightweight container, non-root user, persistent volume for SQLite |

---

## 🏗 Architecture

```
                       User (Telegram)
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                     aiogram Dispatcher                      │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ AntiSpam Middleware (2s cooldown, 120s TTL cleanup)   │  │
│  └───────────────────────────────────────────────────────┘  │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ Routers & Handlers (100% InlineKeyboardMarkup)        │  │
│  │ ├── main.py        - /start, /language, menu & AI     │  │
│  │ ├── lead_form.py   - 3-step FSM lead qualification   │  │
│  │ └── admin.py       - /stats analytics (admin only)    │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
          │                       │                    │
          ▼                       ▼                    ▼
┌──────────────────┐    ┌──────────────────┐    ┌──────────────┐
│  Gemini Cascade  │    │  Google Sheets   │    │   aiosqlite  │
│ 1. 3.8-flash     │    │ (gspread client) │    │  (async DB)  │
│ 2. 2.5-flash     │    └──────────────────┘    └──────────────┘
│ 3. flash-latest  │                                   │
└──────────────────┘                            ┌──────────────┐
                                                │ data/*.db    │
                                                │(Docker Volume│
                                                └──────────────┘
```

### Key Engineering Decisions

- **Resilient Model Cascade (Fallback Chain):** Instead of failing on Gemini API overloads (503 UNAVAILABLE / 429 Too Many Requests), the bot performs 1 fast retry after 1.5s. If overloaded, it immediately transitions down the chain (`gemini-3.8-flash` ➔ `gemini-2.5-flash` ➔ `gemini-flash-latest`).
- **Prompt Engineering & Sanitizer:** System instructions enforce concise, fluff-free responses and international-friendly A2-B1 English. A regex sanitizer (`_clean_markdown`) strips residual Markdown artifacts (`###`, `**`) to ensure sleek Telegram formatting.
- **Knowledge Base Hot-Reload:** `knowledge_base.txt` is cached in memory and dynamically re-read only when its filesystem `mtime` changes, allowing zero-downtime content updates.
- **Asynchronous Database Architecture:** Uses `aiosqlite` for non-blocking database queries, avoiding event-loop blocking while preserving ACID transactions in SQLite.
- **Inline Keyboard UI/UX:** All navigation relies on sleek `InlineKeyboardMarkup`. The bot automatically removes legacy Reply keyboards on startup (`ReplyKeyboardRemove`).

---

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- Telegram Bot Token from [@BotFather](https://t.me/BotFather)
- [Google AI Studio API key](https://aistudio.google.com/app/apikey)
- [Google Cloud Service Account](https://cloud.google.com/iam/docs/service-accounts-create) credentials JSON with Google Sheets API enabled

### 1. Clone & Set Up Environment

```bash
git clone https://github.com/Artem-462/ai_lead_bot.git
cd ai_lead_bot

# Create and activate virtual environment
python -m venv venv

# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

# Install production dependencies
pip install -r requirements.txt

# Or install with development & testing tools
pip install -r requirements-dev.txt
```

### 2. Configure Secrets

```bash
cp .env.example .env
cp credentials.json.example credentials.json
```

Edit `.env` and fill in your tokens:
```env
BOT_TOKEN=your_telegram_bot_token_here
GEMINI_API_KEY=your_gemini_api_key_here
ADMIN_ID=your_telegram_numeric_id
GOOGLE_SHEET_NAME=Leads
GEMINI_MODELS=gemini-3.8-flash,gemini-2.5-flash,gemini-flash-latest
```

Place your real Google Service Account key inside `credentials.json`.

### 3. Set Up Google Sheets

1. Create a Google Sheet named **`Leads`** (or matching `GOOGLE_SHEET_NAME` in `.env`).
2. Share the sheet with the `client_email` found in `credentials.json` with **Editor** permissions.

### 4. Run the Bot

```bash
python main.py
```

---

## 🧪 Testing & Quality Assurance

The project includes an automated test suite with **111 tests** covering all modules:

```bash
# Run complete test suite
python -m pytest

# Run with verbose output
python -m pytest -v

# Run code style and linting checks
ruff check .
```

### Test Coverage Highlights

| Test Module | Tests | Focus Area |
|---|---|---|
| [`tests/test_db.py`](tests/test_db.py) | **20** | `aiosqlite` initialization, dialog logging, `/stats` daily calculation |
| [`tests/test_handlers.py`](tests/test_handlers.py) | **37** | FSM form validation (name, phone regex, request), admin permissions |
| [`tests/test_i18n.py`](tests/test_i18n.py) | **22** | UA/EN string parity, fallback localization, greeting formatting |
| [`tests/test_llm.py`](tests/test_llm.py) | **20** | 3-tier cascade fallback, 1.5s retry on 503/429, prompt rules & Markdown cleanup |
| [`tests/test_main.py`](tests/test_main.py) | **12** | Inline language callbacks, menu navigation, reply keyboard removal |
| **Total** | **111** | **100% Pass Rate** |

---

## 🐳 Docker Deployment

The recommended production deployment method for VPS environments.

### Deploy in 3 Commands

```bash
# 1. Clone repository
git clone https://github.com/Artem-462/ai_lead_bot.git && cd ai_lead_bot

# 2. Add secrets (.env and credentials.json)
cp .env.example .env && nano .env
# Place credentials.json in project root

# 3. Start container in background
docker compose up -d --build
```

### Management Commands

```bash
# View live logs
docker compose logs -f bot

# Restart bot after updating knowledge_base.txt or code
docker compose restart bot

# Stop the container
docker compose down

# Stop and delete data volume (⚠️ deletes SQLite history)
docker compose down -v
```

> **Data Persistence:** SQLite data is stored in the Docker volume mounted at `/app/data/`. All user languages and dialog histories persist across container updates and restarts.

---

## ⚙️ Configuration

### Environment Variables (`.env`)

| Variable | Required | Default | Description |
|---|---|---|---|
| `BOT_TOKEN` | ✅ Yes | — | Telegram Bot API token from @BotFather |
| `GEMINI_API_KEY` | ✅ Yes | — | Google AI Studio API key |
| `ADMIN_ID` | ⬜ No | `""` | Telegram user ID for `/stats` and lead alerts |
| `GOOGLE_SHEET_NAME` | ⬜ No | `Leads` | Google Sheet title to append leads |
| `GEMINI_MODELS` | ⬜ No | `gemini-3.8-flash,gemini-2.5-flash,gemini-flash-latest` | Comma-separated model cascade |

---

## 📁 Project Structure

```
ai_lead_bot/
├── main.py                     # Entry point, router setup, bot polling
├── config.py                   # Environment validation and logging config
├── i18n.py                     # Localization module (UA / EN translations)
├── keyboards.py                # Inline keyboards factory (100% InlineKeyboardMarkup)
├── knowledge_base.txt          # Hot-reloaded business context for Gemini
├── credentials.json.example    # Template for Google service account JSON
├── requirements.txt            # Production dependencies (aiogram, aiosqlite, google-genai)
├── requirements-dev.txt        # Development dependencies (pytest, ruff)
├── pytest.ini                  # Pytest configuration (asyncio_mode = auto)
├── Dockerfile                  # Non-root lightweight production Docker image
├── docker-compose.yml          # Container configuration with persistent SQLite volume
│
├── handlers/
│   ├── __init__.py
│   ├── lead_form.py            # FSM conversational lead capture pipeline
│   └── admin.py                # Admin /stats command handler
│
├── services/
│   ├── __init__.py
│   ├── db.py                   # Async SQLite service (aiosqlite)
│   ├── llm.py                  # Gemini client with fallback cascade & session memory
│   └── sheets.py               # Google Sheets API client (gspread)
│
├── middlewares/
│   ├── __init__.py
│   └── antispam.py             # Rate-limiting middleware with TTL cleanup
│
├── data/                       # Persistent SQLite directory (Docker volume)
│   └── chat_history.db
│
└── tests/                      # Automated test suite (111 tests)
    ├── __init__.py
    ├── conftest.py             # Shared fixtures and isolated mocks
    ├── test_db.py              # Async database operations tests
    ├── test_handlers.py        # Lead form, FSM, and admin handler tests
    ├── test_i18n.py            # Localization and string formatting tests
    ├── test_llm.py             # Model cascade, retries, and prompt rules tests
    └── test_main.py            # Telegram handlers & menu callback tests
```

---

## 🔒 Security Notes

- **Zero Secrets Committed:** `.env` and `credentials.json` are excluded via `.gitignore` and `.dockerignore`.
- **Non-Root Execution:** Docker runs as an unprivileged system user (`botuser`).
- **Read-Only Credentials:** `credentials.json` is mounted as read-only (`:ro`) in Docker.
- **Admin Authentication:** `/stats` is strictly restricted to `message.from_user.id == ADMIN_ID`.

---

## 📝 License

This project is licensed under the **MIT License** — feel free to use and adapt it for your own portfolio and commercial bots.

---

<div align="center">
Built with ❤️ by <a href="https://t.me/artem_murha">Artem</a> · Python · aiogram · Gemini
</div>
