# Stack

> Part of odysseus/.project-knowledge/ | Last updated: 2026-07-24

## Tech Stack

| Category        | Details                                      |
|-----------------|----------------------------------------------|
| Language        | Python 3.11+                                 |
| Runtime         | uvicorn (ASGI) / Docker Compose              |
| Framework       | FastAPI                                      |
| Database        | SQLite                                       |
| ORM / Query     | SQLAlchemy                                   |
| Auth            | bcrypt + session tokens + TOTP 2FA (pyotp) + API bearer tokens |
| Frontend        | Vanilla JS SPA (no framework), ES modules, CSS |
| Vector Store    | ChromaDB (standalone service)                |
| Embeddings      | fastembed (ONNX, local) + optional OpenAI-compatible API endpoint |
| Search          | Pluggable provider registry — SearXNG, DuckDuckGo, Brave, Google PSE, Tavily, Serper, Bing, Search1API, Firecrawl, Exa |
| Styling         | Vanilla CSS                                  |
| Testing         | pytest + pytest-asyncio                      |
| Key Libraries   | httpx, pydantic, beautifulsoup4, pypdf, caldav, icalendar, python-dateutil, croniter, cryptography, bcrypt, mcp, qrcode, nh3, markdown |
| Notifications   | ntfy (self-hosted)                           |
| Container       | Docker Compose (Odysseus + ChromaDB + SearXNG + ntfy) |
| Deployment      | Docker Compose (recommended), native Linux/macOS/Windows, systemd service |

---

## Dev Commands

| Command | What It Does |
|---------|-------------|
| `python -m uvicorn app:app --host 127.0.0.1 --port 7000` | Run dev server natively |
| `docker compose up -d --build` | Build and start all services |
| `docker compose logs --tail=120 odysseus` | View app logs |
| `pytest` | Run tests |
| `python setup.py` | First-time setup (creates admin, initializes DB) |
| `powershell -ExecutionPolicy Bypass -File .\launch-windows.ps1` | Windows one-command launcher |
| `./build-macos-app.sh` | Build macOS app bundle |
| `./install-service.sh` | Install systemd service (fills `odysseus-ui.service` template, `chmod +x` — was missing execute bit, fixed 2026-07-24) |
| `./scripts/run-odysseus.sh` | Convenience launcher — runs the app on a fixed port (24950) with `APP_PORT` set, for pairing with the systemd service *(added 2026-07-24)* |
| `pip install -r requirements.txt` | Install Python dependencies |
| `pip install -r requirements-optional.txt` | Install optional dependencies |
| `uv pip compile requirements.txt -o requirements.lock && uv pip sync requirements.lock` | Optional: pin a reproducible, platform-specific lockfile (gitignored) — `requirements.txt` is intentionally unpinned |
| `./venv/bin/python -m pytest` | Run tests using the project venv (system `python3`/`pytest` is missing pinned deps like `nh3`) |
| `./venv/bin/python tests/run_focus.py --area <area> [--sub-area X] [--fast] [--last-failed]` | Focused test runner (areas: security/routes/services/cli/js/helpers/unit/uncategorized, auto-tagged by `tests/_taxonomy.py`) |
| `./venv/bin/python tests/run_order_report.py` | Report-only test order-sensitivity diagnostic (seeded shuffle, not a CI gate) |

**Platform notes:**
- **Windows**: `launch-windows.ps1` binds `127.0.0.1` by default and does **not** read `APP_BIND`/`ODYSSEUS_HOST` from `.env` — pass `-BindHost 0.0.0.0` explicitly for LAN access. Cookbook/shell tool needs Git for Windows (`bash.exe`); vLLM/SGLang require Linux/WSL2; Ollama is the easiest local-model path.
- **macOS**: `start-macos.sh` / `build-macos-app.sh` serve on port **7860**, not 7000 (AirPlay conflicts with 7000/5000 on macOS). Docker Desktop can't reach Metal GPU, so Cookbook is CPU-only when run in Docker on Apple Silicon — run natively for GPU serving.
- **GPU overlays**: `COMPOSE_FILE=docker-compose.yml:docker/gpu.nvidia.yml` (or `gpu.amd.yml`); helper diagnostics `scripts/check-docker-gpu.sh` / `check-docker-amd-gpu.sh`. Standalone `docker-compose.gpu-nvidia.yml` / `gpu-amd.yml` exist for stack UIs (Portainer/Coolify) that don't honor `COMPOSE_FILE`. `docker/host-docker.yml` (raw Docker-socket access) is high-trust and opt-in only — never mounted by default.
- **Known gotchas**: `.env` saved with a UTF-8 BOM on Windows can break `AUTH_ENABLED` (app tolerates it via `utf-8-sig`, but re-save without BOM if auth behaves oddly); clipboard API is blocked over plain-HTTP LAN/Tailscale URLs (needs HTTPS or localhost); ntfy needs `NTFY_BIND`/`NTFY_BASE_URL` set to reach phones; a co-installed `chromadb-client` package silently forces HTTP-only fallback and breaks the full `chromadb` package — fix via forced reinstall of `chromadb`.
- **Optional deps** (`requirements-optional.txt`, not installed by default): `faster-whisper` (local STT), `ddgs` (DuckDuckGo search provider), `PyMuPDF` (PDF render/forms — **AGPL-3.0**, license-relevant), `markitdown` (Office/EPUB→Markdown).
- Built-in MCP servers (e.g. `@playwright/mcp`) only auto-start if already npx-cached — skipped with a log message otherwise, so a fresh install never blocks on an npx download.

---

## Environment Variables

| Variable | Used In | What It Enables |
|----------|---------|----------------|
| `LLM_HOST` | `src/constants.py` | Default LLM server host |
| `LLM_HOSTS` | `src/constants.py` | Additional LLM hosts for model discovery |
| `OLLAMA_BASE_URL` | `app.py` | Ollama endpoint override |
| `LM_STUDIO_URL` | `app.py` | LM Studio endpoint override |
| `OPENAI_API_KEY` | `src/constants.py` | OpenAI API access |
| `RESEARCH_LLM_ENDPOINT` | research handler | Research-specific LLM endpoint |
| `LLM_CA_BUNDLE` | `src/tls_overrides.py` | Custom CA cert bundle for LLM endpoints |
| `SEARXNG_INSTANCE` | `src/constants.py` | SearXNG URL for web search |
| `SEARXNG_SECRET` | SearXNG config | SearXNG cookie/CSRF secret |
| `DATA_BRAVE_API_KEY` | `services/search/providers.py` | Brave Search key (env fallback; primary: `brave_api_key` setting) |
| `GOOGLE_API_KEY` | `services/search/providers.py` | Google PSE key (env fallback; also needs `google_pse_cx`) |
| `TAVILY_API_KEY` / `SERPER_API_KEY` | `services/search/providers.py` | Tavily / Serper search keys (env fallback) |
| `BING_API_KEY` / `SEARCH1API_API_KEY` / `FIRECRAWL_API_KEY` / `EXA_API_KEY` | `services/search/providers.py` | Keys for Bing, Search1API, Firecrawl, Exa providers (env fallback) |
| `DATABASE_URL` | `core/database.py` | Database connection (default: SQLite) |
| `ODYSSEUS_DATA_DIR` | `src/constants.py` | Override data directory path |
| `AUTH_ENABLED` | `app.py` | Enable/disable auth |
| `APP_BIND` | Docker Compose | Host bind address |
| `APP_PORT` | Docker Compose | Host port |
| `APP_PUBLIC_URL` | webhooks, email | Public URL of the instance |
| `LOCALHOST_BYPASS` | `app.py` | Dev-only auth bypass for loopback |
| `SECURE_COOKIES` | `routes/auth_routes.py` | Mark session cookies Secure |
| `ODYSSEUS_ADMIN_PASSWORD` | setup | Pre-seed admin password |
| `ALLOWED_ORIGINS` | `app.py` | CORS allowed origins |
| `CHROMADB_HOST` | `src/chroma_client.py` | ChromaDB host |
| `CHROMADB_PORT` | `src/chroma_client.py` | ChromaDB port |
| `EMBEDDING_URL` | `src/embeddings.py` | Embedding API endpoint |
| `EMBEDDING_API_KEY` | `src/embeddings.py` | Embedding API key |
| `EMBEDDING_MODEL` | `src/embeddings.py` | Embedding model name |
| `FASTEMBED_MODEL` | `src/embeddings.py` | Local ONNX embedding model |
| `FASTEMBED_CACHE_PATH` | `src/constants.py` | fastembed cache directory |
| `CLEANUP_INTERVAL_HOURS` | `src/constants.py` | Cleanup interval |
| `ODYSSEUS_INPROCESS_POLLERS` | email pollers | Enable in-process email polling |
| `ODYSSEUS_INPROCESS_TASKS` | `app.py` | Enable in-process task scheduler |
| `ODYSSEUS_SCRIPT_HOST` | task scheduler | SSH host for remote script execution |
| `ODYSSEUS_CHAT_UPLOAD_MAX_BYTES` | upload limits | Chat attachment size cap (default 10MB) — all `*_UPLOAD_MAX_BYTES` vars validate as positive integers and fail-fast at startup if malformed |
| `ODYSSEUS_GALLERY_UPLOAD_MAX_BYTES` / `ODYSSEUS_GALLERY_TRANSFORM_MAX_BYTES` | upload limits | Gallery upload cap (100MB) / gallery transform cap (25MB) |
| `ODYSSEUS_MEMORY_IMPORT_MAX_BYTES` | upload limits | Memory import cap (10MB) |
| `ODYSSEUS_PERSONAL_DOC_MAX_BYTES` | upload limits | Personal doc upload cap (25MB) |
| `ODYSSEUS_EMAIL_COMPOSE_MAX_BYTES` | upload limits | Email compose attachment cap (25MB) |
| `ODYSSEUS_STT_AUDIO_MAX_BYTES` | upload limits | STT audio upload cap (25MB) |
| `ODYSSEUS_ICS_MAX_BYTES` | upload limits | ICS calendar import cap (10MB) |
| `ODYSSEUS_MAIL_ATTACHMENTS_DIR` | `src/constants.py` | Mail attachments directory |
| `REQUEST_HARD_TIMEOUT` | `app.py` | Request timeout in seconds |
| `COMPOSE_FILE` | Docker | GPU Compose overlay selection |
| `RENDER_GID` | Docker | AMD GPU render group ID |
