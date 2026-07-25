# Project Structure

> Part of odysseus/.project-knowledge/ | Last updated: 2026-07-24 (read [[roadmap]] for the full refactor-slice plan and its open questions)

## Structural Changes Since 2026-06-25 (upstream merge)

Several former monoliths were split into domain packages upstream. Old entry points are kept as thin backward-compat shims (10-20 lines, re-exporting from the new location) so existing imports still work — check the shim first, then follow the re-export.

- **`src/tool_implementations.py`** (was ~205KB) → now a ~5KB facade. Real implementations moved to **`src/tools/`** (domain modules: `system.py`, `cookbook.py`, `calendar.py`, `contacts.py`, `image.py`, `notes.py`, `research.py`, `search.py`, `vault.py`, `_common.py`).
- **Admin `manage_*` agent tools** (endpoints/mcp/webhooks/tokens/settings) moved to **`src/agent_tools/`** package: `admin_tools.py`, `bg_job_tools.py`, `document_tools.py`, `filesystem_tools.py`, `interaction_tools.py`, `model_interaction_tools.py`, `session_tools.py`, `subprocess_tools.py`, `web_tools.py`.
- **`src/model_capability_readers/`** (new) — per-provider model capability schema readers: `base.py`, `generic_openai.py`, `google.py`, `google_ai_studio_mapping.py`, `llamacpp.py`, `lmstudio.py`, `ollama.py`, `openai.py`, `openrouter.py`.
- **`src/search/`** (new) — `core.py`, `providers.py`, `query.py`, `ranking.py`, `cache.py`, `analytics.py`. Relationship to the existing `services/search/` (our 10-provider registry, in active use) is unconfirmed — see [[roadmap]].
- **Route domains split into subpackages**, each with a thin shim at the old path: `routes/contacts_routes.py` → `routes/contacts/contacts_routes.py`, `routes/gallery_routes.py` → `routes/gallery/gallery_routes.py`, `routes/history_routes.py` → `routes/history/history_routes.py`, `routes/memory_routes.py` → `routes/memory/memory_routes.py`, `routes/research_routes.py` → `routes/research/research_routes.py`.
- **`specs/architecture-runtime-inventory.md`** (2026-06-16 snapshot, Phase 0 draft for #4082/#4071) is the source refactor plan behind the above splits. At that snapshot: `src/` had 95 flat files, `routes/` 54, `core/` 10. Largest files then: `src/tool_implementations.py` (4,032 lines — since split), `routes/email_routes.py` (3,245), `routes/cookbook_routes.py` (2,969), `src/agent_loop.py` (2,961, **not yet split** — planned target `src/agent/loop.py` + submodules, #3266), `core/database.py` (2,265 lines, 28 classes, 102 importers — explicitly planned as the **last** thing to split, highest blast radius). Frontend size snapshot: `static/style.css` 36,653 lines, `static/js/document.js` 9,776, `slashCommands.js` 6,498, `settings.js` 5,266, `emailLibrary.js` 5,217, `notes.js` 5,124, `chat.js` 4,985, `app.js` 4,090 — none split yet (frontend CSS/JS modularization is priority 7/8 in the plan, tracked as #2617 for CSS). A documented code smell: 8 function-body (not top-level) imports inside `src/tool_implementations.py` reach into `routes/*.py` — a cross-layer violation not yet fixed.
- **`static/js/MODULE_SUMMARY.md`** documents the ~90-module ES6 frontend by subsystem (core/ui, chat pipeline, model/config, session/sidebar, memory/RAG, document/editor, research UI, gallery/email/calendar/tasks/notes, cookbook, compare mode, utilities), plus the SSE event-dispatch table: `delta`, `tool_start`, `tool_progress`, `tool_output`, `agent_step`, `doc_stream_open`/`doc_stream_delta`, `research_progress`/`research_sources`/`research_done`, `web_sources`, `model_info`, `fallback`, `metrics`, `message_saved`, `budget_exceeded`, `rounds_exhausted`, `teacher_takeover`, `skill_saved`. Check this file before adding a new frontend module or SSE event type.

## File Tree

```
.
├── app.py                    # FastAPI entry point — routes, middleware, lifespan
├── core/                     # Core infrastructure
│   ├── auth.py               # AuthManager: bcrypt, sessions, 2FA, user/privilege CRUD
│   ├── database.py           # SQLAlchemy models (30+ tables) + engine + migrations
│   ├── models.py             # Pure data containers (ChatMessage, Session dataclasses)
│   ├── session_manager.py    # Session CRUD + message persistence
│   ├── middleware.py         # SecurityHeadersMiddleware, require_admin helper
│   ├── constants.py          # Base paths, env var defaults, agent output caps
│   ├── exceptions.py         # Custom exception types
│   ├── atomic_io.py          # Atomic JSON file writes
│   └── platform_compat.py    # Platform-specific compatibility
├── src/                      # Application logic
│   ├── llm_core.py           # LLM API client (~97KB) — OpenAI-compatible provider abstraction
│   ├── agent_loop.py         # Agent main loop (~170KB)
│   ├── tool_implementations.py # All agent tool implementations (~205KB)
│   ├── tool_schemas.py       # Tool JSON schemas (~82KB)
│   ├── tool_index.py         # RAG-based tool selection (~42KB)
│   ├── tool_execution.py     # Tool execution pipeline (~68KB)
│   ├── tool_parsing.py       # Tool call parsing (~20KB)
│   ├── ai_interaction.py     # AI interaction tools: debates, pipelines, UI control (~76KB)
│   ├── builtin_actions.py    # Built-in agent actions (~107KB)
│   ├── chat_handler.py       # Chat response handler
│   ├── chat_processor.py     # Chat processing pipeline
│   ├── deep_research.py      # Multi-step deep research engine
│   ├── research_handler.py   # Research orchestration
│   ├── memory.py             # Memory management
│   ├── memory_vector.py      # Vector memory (ChromaDB for memories)
│   ├── memory_provider.py    # Memory provider abstraction
│   ├── personal_docs.py      # Personal document management
│   ├── rag_manager.py        # RAG manager for personal docs
│   ├── rag_vector.py         # Vector RAG (ChromaDB for document search)
│   ├── rag_singleton.py      # RAG singleton accessor
│   ├── mcp_manager.py        # MCP server management (~29KB)
│   ├── mcp_oauth.py          # MCP OAuth flow
│   ├── builtin_mcp.py        # Built-in MCP server registration
│   ├── model_discovery.py    # LLM endpoint/model discovery
│   ├── model_context.py      # Model context window management
│   ├── endpoint_resolver.py  # Endpoint URL resolution
│   ├── config.py             # App configuration
│   ├── settings.py           # App settings CRUD
│   ├── embeddings.py         # Embedding provider abstraction
│   ├── embedding_lanes.py    # Embedding lane management
│   ├── chroma_client.py      # ChromaDB HTTP client wrapper
│   ├── task_scheduler.py     # Cron-style scheduled task engine (~110KB)
│   ├── event_bus.py          # In-process event bus
│   ├── webhook_manager.py    # Outgoing webhook manager
│   ├── upload_handler.py     # File upload handling (~28KB)
│   ├── api_key_manager.py    # API key management
│   ├── preset_manager.py     # Preset configuration manager
│   ├── auth_helpers.py       # Auth helper utilities
│   ├── session_actions.py    # Session action utilities
│   ├── session_search.py     # Session search
│   ├── integrations.py       # Third-party integrations (~21KB)
│   ├── secret_storage.py     # Fernet-encrypted secret storage
│   ├── prompt_security.py    # Prompt injection guardrails
│   ├── topic_analyzer.py     # Topic analysis
│   ├── context_compactor.py  # Context window compaction
│   ├── context_budget.py     # Context budget management
│   ├── bg_jobs.py            # Background job management
│   ├── bg_monitor.py         # Background job monitor
│   ├── assistant_log.py      # Assistant activity logging
│   ├── chatgpt_subscription.py # ChatGPT subscription device-flow
│   ├── copilot.py             # GitHub Copilot device-flow
│   ├── caldav_sync.py        # CalDAV sync engine
│   ├── caldav_writeback.py   # CalDAV write-back
│   ├── document_processor.py # Document processing
│   ├── document_actions.py   # Document action helpers
│   ├── email_thread_parser.py # Email thread parsing
│   ├── teacher_escalation.py # Teacher escalation workflow
│   ├── visual_report.py      # Deep research visual report rendering (~71KB)
│   ├── url_safety.py         # URL safety checks
│   ├── url_security.py       # URL security validation
│   ├── text_helpers.py       # Text processing utilities
│   ├── user_time.py          # User timezone handling
│   ├── rate_limiter.py       # Rate limiting
│   ├── readiness.py          # Readiness check
│   ├── chat_helpers.py       # Chat helper utilities
│   ├── agent_tools.py        # Agent tool interfaces
│   ├── agent_runs.py         # Agent run management
│   ├── action_intents.py     # Action intent detection
│   ├── goal_based_extractor.py # Goal-based data extraction
│   ├── generated_images.py   # Generated image utilities
│   ├── pdf_forms.py          # PDF form filling
│   ├── pdf_form_doc.py       # PDF form document handling
│   ├── pdf_runtime.py        # PDF runtime checks
│   ├── markitdown_runtime.py # Office document -> Markdown conversion
│   ├── tls_overrides.py      # TLS configuration overrides
│   ├── upload_limits.py      # Upload size limits
│   ├── settings_scrub.py     # Settings sanitization
│   ├── request_models.py     # API request validation models
│   ├── app_helpers.py        # App helper utilities
│   ├── app_initializer.py    # Component initialization
│   ├── copilot.py            # GitHub Copilot integration
│   ├── cookbook_serve_lifecycle.py # Cookbook serve lifecycle management
│   ├── clean_server.sh       # Cleanup script
│   ├── database.py           # Database initialization
│   ├── exceptions.py         # Exception types
│   └── constants.py          # Constants
├── routes/                   # API route handlers (55+ files)
│   ├── auth_routes.py        # Auth endpoints (login, signup, 2FA, users, integrations)
│   ├── chat_routes.py        # Chat endpoints (~78KB)
│   ├── session_routes.py     # Session CRUD (~56KB)
│   ├── document_routes.py    # Document editor endpoints (~76KB)
│   ├── email_routes.py       # Email endpoints (~155KB)
│   ├── email_pollers.py      # Email background polling (~69KB)
│   ├── email_helpers.py      # Email helper utilities (~61KB)
│   ├── calendar_routes.py    # Calendar/CalDAV endpoints (~63KB)
│   ├── cookbook_routes.py    # Cookbook model management (~128KB)
│   ├── cookbook_helpers.py   # Cookbook helpers (~52KB)
│   ├── model_routes.py       # Model/probe endpoints (~101KB)
│   ├── shell_routes.py       # Shell execution endpoints (~52KB)
│   ├── task_routes.py        # Scheduled task endpoints (~51KB)
│   ├── note_routes.py        # Notes/todos endpoints (~41KB)
│   ├── skills_routes.py      # Skills management (~76KB)
│   ├── memory_routes.py      # Memory endpoints (~24KB)
│   ├── history_routes.py     # Chat history endpoints (~29KB)
│   ├── gallery_routes.py     # Image gallery endpoints (~81KB)
│   ├── mcp_routes.py         # MCP server management (~28KB)
│   ├── research_routes.py    # Deep research endpoints (~31KB)
│   ├── contacts_routes.py    # CardDAV contacts (~32KB)
│   ├── codex_routes.py       # Codex integration (~38KB)
│   ├── webhook_routes.py     # Webhook management (~16KB)
│   ├── upload_routes.py      # File upload endpoints (~13KB)
│   ├── assistant_routes.py   # Personal assistant endpoints (~14KB)
│   ├── backup_routes.py      # Backup/export/import
│   ├── compare_routes.py     # Model comparison (~11KB)
│   ├── chatgpt_subscription_routes.py # ChatGPT subscription device-flow
│   ├── copilot_routes.py     # GitHub Copilot device-flow
│   ├── hwfit_routes.py       # Hardware fit ("What Fits?") (~14KB)
│   ├── personal_routes.py    # Personal document management (~13KB)
│   ├── embedding_routes.py   # Embedding model management (~14KB)
│   ├── api_token_routes.py   # API token CRUD
│   ├── admin_wipe_routes.py  # Admin danger-zone wipes
│   ├── cleanup_routes.py     # Session cleanup
│   ├── search_routes.py      # Web search
│   ├── preset_routes.py      # Preset management
│   ├── prefs_routes.py       # User preferences
│   ├── diagnostics_routes.py # System diagnostics
│   ├── tts_routes.py         # Text-to-speech
│   ├── stt_routes.py         # Speech-to-text
│   ├── signature_routes.py   # Reusable image stamps
│   ├── vault_routes.py       # Secure vault
│   ├── editor_draft_routes.py # Image editor drafts
│   ├── font_routes.py        # Font management
│   ├── workspace_routes.py   # Workspace management
│   ├── feed_routes.py        # RSS Feed Reader CRUD, articles, OPML, AI summarize, refresh
│   ├── emoji_routes.py       # Emoji SVG proxy
│   └── device_flow.py        # OAuth device-flow helpers
├── services/                 # Service modules
│   ├── feed/                 # RSS/Atom feed reader: fetcher, discovery, OPML, full-content, YouTube resolver
│   ├── search/                # Web search service — pluggable provider registry (PROVIDER_REGISTRY + PROVIDER_FUNCTIONS in providers.py)
│   ├── memory/                # Memory extraction + skill management
│   ├── research/              # Research orchestration
│   ├── docs/                  # Document service
│   ├── hwfit/                 # Hardware fit scoring (llmfit-based)
│   ├── shell/                 # Shell execution service
│   ├── tts/                   # Text-to-speech service
│   ├── stt/                   # Speech-to-text service
│   ├── youtube/                # YouTube transcript handler
│   └── faces/                  # Face detection service
├── mcp_servers/              # Built-in MCP servers
│   ├── email_server.py       # Email MCP server
│   ├── memory_server.py      # Memory MCP server
│   ├── rag_server.py         # RAG MCP server
│   └── image_gen_server.py   # Image generation MCP server
├── companion/                # Companion app endpoints
│   ├── routes.py             # /ping, /info, /models, /pair endpoints
│   └── pairing.py            # Companion pairing logic
├── static/                   # Frontend SPA
│   ├── index.html            # Main app shell
│   ├── login.html            # Login page
│   ├── app.js                # Main app bundle
│   ├── style.css             # Styles (~large, all CSS in one file)
│   ├── manifest.json         # PWA manifest
│   ├── sw.js                 # Service worker
│   └── js/                   # ES modules (80+ files)
│       ├── chat.js, chatRenderer.js, chatStream.js
│       ├── cookbook*.js      # Cookbook UI modules
│       ├── calendar.js, calendar/
│       ├── email*.js         # Email UI modules
│       ├── notes.js          # Notes/todos UI
│       ├── feedReader.js     # RSS Feed Reader UI
│       ├── gallery*.js       # Gallery UI
│       ├── admin.js          # Admin settings
│       ├── compare/          # Model comparison UI
│       └── ...               # 70+ more modules
├── scripts/                  # Utility scripts
│   ├── check-docker-gpu.sh   # NVIDIA GPU Docker diagnostic
│   ├── check-docker-amd-gpu.sh # AMD GPU Docker diagnostic
│   ├── odysseus-mail         # CLI mail poller (for cron)
│   ├── hf_download.py        # HuggingFace model download helper
│   ├── run-odysseus.sh       # Fixed-port (24950) launcher, pairs with the systemd service (added 2026-07-24)
│   └── ...
├── integrations/             # External integration plugins
│   ├── claude/               # Claude Code integration (SKILL.md + API script)
│   └── codex/                # Codex plugin integration (plugin.json + SKILL.md)
├── config/                   # Bundled service configs
│   └── searxng/settings.yml  # SearXNG default config
├── docker/                   # Docker support files
│   ├── entrypoint.sh         # Entrypoint (drops privileges via gosu)
│   ├── gpu.nvidia.yml        # NVIDIA GPU Compose overlay
│   └── gpu.amd.yml           # AMD GPU Compose overlay
├── docker-compose.yml        # Main Compose file
├── Dockerfile                # Container image (Python 3.12-slim)
├── pyproject.toml            # Pytest config
├── requirements.txt          # Core Python dependencies
├── requirements-optional.txt # Optional dependencies (whisper, DuckDuckGo, PyMuPDF, markitdown)
├── package.json              # Node deps (Anthropic SDK, bombadil linter)
├── build-macos-app.sh        # macOS app bundler
├── launch-windows.ps1        # Windows one-command launcher
├── install-service.sh        # systemd service installer
├── odysseus-ui.service       # systemd unit file
├── .env.example               # Environment variable reference
├── README.md                  # Project documentation
├── ROADMAP.md                 # Known issues and future work
├── CONTRIBUTING.md            # Contribution guide
├── ACKNOWLEDGMENTS.md         # Third-party acknowledgments
└── LICENSE                    # MIT license
```

## Key Files

| File | Purpose |
|------|---------|
| `app.py` | FastAPI entry point: lifespan, middleware (CORS, security, auth, timeout), static file serving, all route registrations |
| `core/database.py` | All SQLAlchemy models (30+ tables), engine config, encrypted text column |
| `core/auth.py` | AuthManager: user CRUD, password hashing (bcrypt), session tokens, 2FA (TOTP), privileges |
| `src/llm_core.py` | LLM API client: OpenAI-compatible provider abstraction, streaming, tool calling |
| `src/agent_loop.py` | Main agent loop: tool selection, execution, continuation |
| `src/tools/` | Agent tool implementations by domain (was `src/tool_implementations.py`, now a facade — see Structural Changes above) |
| `src/agent_tools/` | Admin/session/subprocess/document/web/filesystem agent tools by domain |
| `src/task_scheduler.py` | Cron-style scheduled task engine (110KB) |
| `src/builtin_actions.py` | Built-in agent actions (107KB) |
| `src/ai_interaction.py` | Debates, pipelines, self-managing AI (76KB) |
| `static/app.js` | Main frontend application bundle |
