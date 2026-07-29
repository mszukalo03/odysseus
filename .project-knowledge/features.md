# Features & Workflows

> Part of odysseus/.project-knowledge/ | Last updated: 2026-07-26

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
- **RSS Feed Reader** — 3-pane RSS/Atom feed reader with AI summaries (including YouTube transcript-based summaries when `feedparser` leaves content empty), article thumbnails, star/read tracking, OPML import/export, YouTube channel URL resolution, j/k/m/s keyboard shortcuts + Prev/Next navigation, drag-to-reorder/move feeds between groups (including into collapsed groups), per-feed refresh interval, optional auto-refresh via the task scheduler, infinite-scroll article list, working TTS playback. *(added: 2026-06-11, updated 2026-07-20)*
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

**A fullscreen, navigable dashboard screen — frontend for the external n8n "Monday" digest workflow** (`static/js/ithaca.js`, `routes/ithaca_routes.py`)
1. Open via the "Ithaca" sidebar entry (top of sidebar), the temple rail button, or the `/ithaca` deep link — a real history entry, so browser Back returns to the chat (popstate keeps screen ↔ URL in sync); Esc or the header "Chat" button also closes it
2. Opening collapses the wide sidebar to the icon rail (same pattern as /email, /notes); closing restores it via `window._restoreSidebarIfRouteCollapsed`
3. Static 3×3 tile grid, letter-addressed slots A–I (future: user-defined tiles, rearrange/resize in-app):
   - **Tile A — Weather**: live OpenWeatherMap current + 3-hourly forecast via `/api/ithaca/weather` (backend proxy, 10-min cache; auto-refreshes every 10 min while open). NOT the digest's weather section.
   - **Tile B — Software Updates**: the digest's markdown table parsed server-side (`/api/ithaca/digest` reads the latest `*-digest.md` from the vault — the same file the n8n workflow PATCHes). App titles link to the git repo; the hosted-on chip runs the admin-only ssh-open action (`POST /api/ithaca/ssh/open`) showing a remote `ls -la` of the app's deploy path in a modal. Per-app repo/ssh mapping lives in `data/ithaca.json` (defaults cover the workflow's six projects).
     - **Secret redaction**: digest bodies are scrubbed (`redact_secrets`) before being served, because `/api/ithaca/digest` returns every heading section verbatim and the digest is a hand-edited vault note that may sit next to the automation's own credentials — a pasted `Bearer <obsidian token>` under `## Notes` was reaching every `ithaca:read` caller (and the agent tools, which share the same build path). Two tiers: exact-match on values this deployment knows are secrets (`obsidian_api_token`, `openweather_api_key`), then narrow shape patterns (`Bearer …`, `Authorization: …`, 48+ hex chars — past a git SHA's 40 so commit hashes in release notes survive). A redaction logs a warning naming the file.
     - **Digest source, in priority order**: `OBSIDIAN_VAULT_PATH` (the vault directory on disk) wins when set; otherwise the Obsidian Local REST API (`OBSIDIAN_API_URL` + `OBSIDIAN_API_TOKEN`). Prefer the vault path: the REST API is a plugin hosted *inside* the Obsidian desktop process, so with that app closed nothing listens on its port and the tile fails with connection-refused/timeouts on every address you try — reading the synced files needs no HTTP, token, or running app. The response's `source` field reports which path served it (`"vault"` / `"api"`). In Docker the vault must be bind-mounted: compose mounts `OBSIDIAN_VAULT_HOST_PATH` read-only *at* `OBSIDIAN_VAULT_PATH`, so setting both to the same absolute path keeps one config valid for containerized and host runs. YAML frontmatter is stripped before splitting sections (`strip_frontmatter`), and `OBSIDIAN_DIGEST_DIR` is containment-checked against the vault root.
   - **Tiles C–I**: reserved placeholders.
4. Env: OPENWEATHER_API_KEY/LAT/LON/UNITS, OBSIDIAN_VAULT_PATH, OBSIDIAN_API_URL/TOKEN, OBSIDIAN_DIGEST_DIR (see .env.example) — or the same 8 keys settable in **Settings > Integrations > "Ithaca Hub"** (admin-only card), which take priority over the env vars when non-empty (`routes/ithaca_routes.py`'s `_setting_or_env`, mirroring `services/search/providers.py`'s provider-key pattern). The two secrets (`openweather_api_key`, `obsidian_api_token`) are masked/write-protected everywhere the app already protects secret-shaped setting names (`settings_scrub.is_secret_key`, `manage_settings` tool).
5. **AI-integrated**: two read-only agent tools expose the same tile data to chat — `get_home_weather` and `get_homelab_updates` (`src/tools/ithaca.py`), sharing the routes' fetch/parse/cache functions so the AI's answer always matches the tile. Registered across the full tool pipeline: `FUNCTION_TOOL_SCHEMAS` (tool_schemas.py), `TOOL_TAGS`/dispatch (agent_tools/__init__.py, tool_execution.py), RAG description + "weather"/"software update" keyword hints (tool_index.py), prompt guidance (agent_loop.py TOOL_SECTIONS), and the Plan Mode read-only allowlist (tool_security.py).
