# Features & Workflows

> Part of odysseus/.project-knowledge/ | Last updated: 2026-08-08

## Features

- **Chat** — Chat with any local or API model (vLLM, llama.cpp, Ollama, OpenRouter, OpenAI, GitHub Copilot). Streaming, tool calling, multi-model sessions.
- **Agent** — Tool-using agent with web search, file operations, shell, MCP servers, memory, skills. Built on opencode agent framework.
- **Cookbook** — Scan hardware, recommend compatible models, click to download and serve. VRAM-aware, fit scoring, vLLM/llama.cpp serving.
- **Deep Research** — Multi-step research runs: gather, read, synthesize sources into visual reports.
- **Model Comparison** — Blind A/B model comparison with side-by-side output and synthesis.
- **Documents** — Multi-tab editor with markdown/HTML/CSV, syntax highlighting, AI edits and suggestions.
- **Memory & Skills** — Persistent memory and evolving skills with vector + keyword retrieval. Import/export.
- **Email** — IMAP/SMTP inbox with AI triage: urgency detection, auto-tag, auto-summary, auto-reply drafts. Multi-account. Layout redesigned 2026-07-24 into a 3-pane webmail view: a folder sidebar (per-folder unread badges via IMAP `STATUS ... (UNSEEN)`, `GET /api/email/folders`'s new `unread_counts` field) that's collapsible — collapses to a 40px icon-only rail (icons + active state + unread dots + tooltips stay reachable, matching the main app sidebar's own collapse behavior) rather than hiding entirely — and drag-resizable, a persistent message list, and a persistent reading pane that populates on click without the list disappearing (previously an in-place expanding-card layout). Collapses to a single back-navigable pane on mobile. Reading-pane action row (2026-07-26) is icon-only with tooltips, and Reply/Reply All/Reply with AI are folded into one dropdown off a single Reply button (4 top-level buttons instead of up to 6) — see [[history]]. All prior functionality preserved: compose, reply/reply-all/forward, AI reply, translate, tags, bulk select, attachments, account switching, the Scheduled virtual folder. A pulse banner (top-right, sender + subject, auto-dismiss) now also fires on newly-arrived mail, alongside the existing sidebar breathing-dot indicator — see [[systems]]. See [[history]] for the redesign write-up.
- **Notes & Tasks** — Google Keep-style notes with reminders, checklists, cron-style scheduled tasks. ntfy/browser/email notification channels.
- **Calendar** — Local-first calendar with CalDAV sync (Radicale, Nextcloud, Apple, Fastmail). Agent-aware.
- **Image Generation & Gallery** — AI image generation, gallery with albums, EXIF, tags, search.
- **Image Editor** — Server-backed image editing drafts with tools.
- **Contacts** — CardDAV contacts sync and management.
- **MCP Servers** — Built-in MCP servers for browser, email, memory, RAG, image generation.
- **Webhooks** — Outgoing webhooks with event selection and secret signing.
- **API Tokens** — Scoped bearer tokens for external integrations.
- **Vault** — Encrypted secure storage for sensitive data.
- **Signatures** — Reusable image stamps.
- **Workspace** — Workspace/organization management.
- **Shell** — Command execution within agent (admin-gated).
- **Presets** — Preset model/endpoint configurations.
- **Backup & Restore** — Export/import user data (memories, presets, skills).
- **Integrations** — Third-party provider integration management (LLM providers, etc.).
- **Companion** — Mobile companion app pairing and info endpoints.
- **Codex / Claude Integration** — External AI code editor bridge via scoped API tokens.
- **PWA** — Installable as progressive web app with service worker.
- **2FA** — Two-factor authentication via TOTP.
- **Emoji SVG Proxy** — Same-origin lazy-cached Twemoji SVGs for chat rendering.
- **TTS/STT** — Text-to-speech and speech-to-text (optional local Whisper STT).
- **RSS Feed Reader** — 3-pane RSS/Atom feed reader with AI summaries (including YouTube transcript-based summaries when `feedparser` leaves content empty), article thumbnails, star/read tracking, OPML import/export, YouTube channel URL resolution, j/k/m/s keyboard shortcuts + Prev/Next navigation, drag-to-reorder/move feeds between groups (including into collapsed groups), per-feed refresh interval, optional auto-refresh via the task scheduler, infinite-scroll article list, working TTS playback, grid/list view toggle (server-persisted per-user, syncs across devices). **AI-integrated for chat**: four purpose-built agent tools (`src/tools/feed.py`), following Ithaca's small-tools shape rather than a generic `manage_X` dispatcher — `list_rss_feeds` (feeds/groups + unread counts), `get_rss_articles` (filtered article list), `summarize_rss_articles` (article or group digest), `mark_rss_article` (read/star). Registered across the full tool pipeline (schema, dispatch, `TOOL_TAGS`, RAG keyword hints, `TOOL_SECTIONS`/`_DOMAIN_RULES` prompt guidance, `_DOMAIN_TOOL_MAP`/`_ROUTING_PATTERNS` turn-routing, Plan Mode read-only allowlist for the three non-mutating tools) — see the "Adding a New Agent Tool" checklist in [[systems]]. *(added: 2026-06-11, updated 2026-07-20, AI tools + server-side view pref added 2026-08-08)*
- **llama.cpp Auto-Detection** — server discovery now identifies llama.cpp servers and labels them as local providers in the model picker. *(added: upstream, 2026-06-25)*
- **Admin: Share Defaults Toggle** — admins can choose whether their default model/endpoint is shared with all users. *(added: upstream, 2026-06-25)*
- **Chat Padding Toggle** — UI setting to toggle padding around the chat area. *(added: upstream, 2026-06-25)*
- **Gemma 4 12B/QAT Cookbook Entries** — Gemma 4 12B and QAT variants added to hardware fit catalog, RTX 3050 bandwidth data added. *(added: upstream, 2026-06-25)*
- **Backup & Restore** — `scripts/odysseus-backup` CLI: snapshot/list/verify/restore `data/` safely while the app is running. Secrets are included in the tarball (it's a full data backup, not sanitized); restore requires explicit confirmation since it replaces `data/`. See [[history]]. *(docs: `docs/backup-restore.md`)*
- **Agent Migration (spec/tooling, not yet wired into the UI)** — scriptable, source-neutral manifest builder (`scripts/agent_migration_manifest.py`) for importing another AI agent's memories/skills/conversations/archives into Odysseus, including recognizing ChatGPT `conversations.json` exports. See [[integrations]]. *(docs: `docs/agent-migration.md`)*

## Maintainer / Contributor Tooling

> Not user-facing features — internal scripts for repo maintainers.

- **PR Blocker Audit** (`scripts/pr_blocker_audit.py`) — offline/live PR-overlap and duplicate-detection triage tool over `gh pr list`/`gh api`, with terminal/Markdown/JSON output. Single-file script by design, pending settled tooling conventions. *(docs: `docs/pr-blocker-audit.md`)*

---

## Workflows

**User Registration / First Boot**
1. First boot: no users → setup mode
2. `POST /api/auth/setup` (or first request auto-creates admin with printed password)
3. Admin logs in → changes password → configures settings
4. Optional: enable open signup, create additional users with privileges

**Chat Flow**
1. User sends message via `POST /api/chat/send` (or streaming variant)
2. ChatProcessor determines mode (chat/agent/research)
3. Messages persisted to `chat_messages` via SessionManager
4. LLM called with context + tools (if agent mode)
5. Response streamed back and persisted
6. Optional: memory extraction, tool execution, skill evaluation

**Agent Tool Execution**
1. Agent receives user request → LLM generates tool calls
2. Tool calls parsed via `tool_parsing.py`
3. Each tool executed via `tool_execution.py` calling into `tool_implementations.py`
4. Results fed back to LLM for next iteration
5. Loop continues until task complete or max turns reached

**Deep Research**
1. User submits research query (optionally with `category` for format override)
2. ResearchHandler spawns multi-step research job via `DeepResearcher`
3. Pipeline: classify category → plan (sub-questions + key topics + success criteria) → loop[generate queries → search → fetch + extract → synthesize → stop?] → final report (via `FINAL_REPORT_PROMPT` with STRUCTURE CHECK at top)
4. Visual report generated via `visual_report.py` (HTML with sources, stats, findings)
5. Report available for viewing/export at `/api/research/report/{session_id}`

**Email Triage**
1. Background email pollers fetch new mail from IMAP
2. AI analyzes urgency → tags → auto-reply drafts
3. Notifications sent via ntfy/browser for urgent mail
4. User can view, reply, manage from email UI

**Cookbook Model Download & Serve**
1. Hardware scan detects GPU/CPU/RAM/VRAM
2. Model recommendations based on hardware fit
3. User clicks download → background job via tmux
4. After download → serve via vLLM or llama.cpp
5. Model available in chat model selector

**Scheduled Tasks**
1. Task scheduler evaluates cron expressions
2. On match, executes action (built-in or user-defined)
3. Actions include: send email, run script, webhook, agent run, etc.
4. Results logged, notifications sent

**Feed Reader — Add Feed from URL**
1. User enters URL in "Add Feed" modal
2. Frontend calls `POST /api/feeds/discover` → `discover_feeds()` auto-detects RSS/Atom feeds
3. If the URL is a YouTube channel/handle/playlist URL, `resolve_youtube_feed()` resolves it to `feeds/videos.xml`
4. User selects which discovered feed to add
5. Frontend calls `POST /api/feeds` to create the feed (sets YouTube favicon if applicable)
6. Optionally, `POST /api/feeds/{id}/refresh` fetches the feed immediately

**Feed Reader — Read and Navigate Articles**
1. Click feed in sidebar → `_loadArticles()` fetches articles (paginated, filterable by unread/starred/feed)
2. Click article in list → `_openReader()` shows reader view (title, date, full content or summary); list view snapshots into `_readerNavList` at this point so navigation stays stable even if a background list refresh happens mid-session
3. Toolbar: Prev/Next, back, star/unstar, AI summarize, full-content fetch, open original, mark read
4. Keyboard shortcuts while the reader is open: `j`/`k` next/prev article, `m` mark read, `s` star
5. Reader view is inside the main RSS modal with drag/dock/fullscreen support

**Feed Reader — Drag to Reorder / Move Feeds Between Groups**
1. Grab a feed's drag handle (`.rss-feed-drag-handle`) and drag vertically — same gesture for both actions
2. Dropping within the same group's section reorders it there; dropping in a different group's section (or the synthetic "Ungrouped" section) moves it there
3. `_onFeedListReordered` infers each feed's new group from the nearest preceding group header in final DOM order, diffs against in-memory state, and persists via `PUT /api/feeds/{id}` (`group_id` + `sort_order`) — same per-feed-call pattern as batch move
4. Built on the shared `dragSortModule` (`static/js/dragSort.js`), also used by Models/Sessions/Gallery — no changes to that shared module were needed
5. Collapsed groups can't receive a dropped feed (no rendered drop space) — must expand first

## Ithaca Hub (dashboard homepage)

**A fullscreen, navigable dashboard screen** — a manifest-driven extension (`extensions/ithaca/`), with `backend.py` as pure route wiring over dedicated modules: `weather.py` (the one built-in tile), `tile_schema.py`/`tiles.py` (user-defined tiles), `ai_tile_builder.py` (LLM tile generation), `tile_packaging.py` (export/import). Frontend under `extensions/ithaca/static/`: `index.js` (workspace descriptor + Weather rendering), `ithacaCommon.js` (shared `esc`/`api` helpers), `tileRenderer.js` (generic viz rendering, lazy-loads vendored Chart.js for bar/line/pie), `tileBuilder.js` (Add Tile / Import Tile modals), `tileLayout.js` (drag-to-move, drag-corner-to-resize).
1. Open via the "Ithaca" sidebar entry, the temple rail button, or the `/ithaca` deep link — a real history entry, so browser Back returns to the chat; Esc or the header "Chat" button also closes it.
2. Opening collapses the wide sidebar to the icon rail; closing restores it.
3. **Weather is the only persistent built-in tile** — live OpenWeatherMap current + 3-hourly forecast via `/api/ithaca/weather` (10-min cache, auto-refreshes while open). Every other tile is user-defined:
   - **Manual**: `POST /api/ithaca/tiles` with a raw SQL query + viz type against a connected Postgres DB.
   - **AI-generated** (`extensions/ithaca/ai_tile_builder.py`, single-shot, not the general agent loop): given a connection + free-form context notes + an NL ask, introspects the live schema, asks the configured LLM for a *minimal* JSON draft (title/query/viz_type/label_field/value_field only — everything else assembled in Python), auto-fixes unquoted multi-word `AS` aliases via regex, sanity-runs the query before returning it, and derives table columns from the actual result set rather than asking the model to enumerate them. Tuned for small local models: temperature 0, a one-shot example, explicit snake_case-alias rule.
   - **Imported**: a portable `.tile.json` package (config + a non-secret connection hint, never credentials) from another instance, rebound to a local connection on import — never auto-matched by id.
4. **Layout**: every tile (Weather included) is drag-to-move (via its header) and drag-corner-to-resize, pixel-based against the CSS grid. Persisted separately from tile content in `data/ithaca/layout.json` (`GET`/`PUT /api/ithaca/layout`), so it applies uniformly to the built-in and user-defined tiles.
5. **Data sources**: `core/external_db.py` — admin-configured read-only DB connections (`ExternalDbConnection` in `core/database.py`, Fernet-encrypted credentials), Settings > Integrations > "Database Connections" card. **Multi-dialect** (2026-08-08): Postgres, SQLite, or MySQL, selected via `ExternalDbConnection.kind` — a per-dialect adapter seam in `core/db_dialects.py` (`get_adapter(kind)`) handles URL construction, read-only enforcement (Postgres: `SET LOCAL statement_timeout` + `SET TRANSACTION READ ONLY` per transaction; SQLite: `mode=ro` + `PRAGMA query_only` on connect; MySQL: `SET SESSION TRANSACTION READ ONLY` + `max_execution_time` on connect, since MySQL can't issue a read-only `START TRANSACTION` once SQLAlchemy owns the transaction) and schema introspection, so `core/external_db.py` itself stays dialect-independent policy (statement-shape check, engine caching, row limits, error mapping). `TileDataSource.type` is kept as a separate portable authoring hint from `kind`'s query-time dialect selection, so exported tile packages stay connection-agnostic.
6. **Table tiles**: client-side sort (header click, type-aware: number/date/string auto-detected) and per-column filter (dropdown for low-cardinality columns, substring match otherwise) — no backend changes, works against the already-returned result set. JSON(B)-valued columns render as `key: value` prose (preferring a nested "version" field, else the first primitive field) instead of `[object Object]`, and their filter dropdown is built from the union of object keys across rows rather than whole-blob equality.
7. Env: `OPENWEATHER_API_KEY/LAT/LON/UNITS` only (see `.env.example`) — or the same 4 keys in **Settings > Integrations > "Ithaca Hub"** (admin-only card), which take priority when non-empty (`extensions/ithaca/weather.py`'s `_setting_or_env`).
8. **AI-integrated for chat**: two read-only agent tools (`src/tools/ithaca.py`) — `get_home_weather` (same cache as the tile) and `query_ithaca_tile` (no tile_id lists the user's custom tiles; a tile_id runs one and returns its live data as text — never accepts arbitrary SQL from the agent, only a saved/vetted tile id). Registered across the full tool pipeline: `FUNCTION_TOOL_SCHEMAS` (tool_schemas.py), `TOOL_TAGS`/dispatch (agent_tools/__init__.py, tool_execution.py), RAG description + keyword hints (tool_index.py), prompt guidance (agent_loop.py TOOL_SECTIONS), and the Plan Mode read-only allowlist (tool_security.py). RSS's `src/tools/feed.py` (four tools: `list_rss_feeds`/`get_rss_articles`/`summarize_rss_articles`/`mark_rss_article`) follows this exact same small-tools pattern — see the RSS Feed Reader entry above and the "Adding a New Agent Tool" checklist in [[systems]].
