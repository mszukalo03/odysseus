# Routes & Server Actions

> Part of odysseus/.project-knowledge/ | Last updated: 2026-07-19
> 150+ endpoints across 55+ route files. Prefix `/api/auth` on all auth routes, `/api` on others.
> Check here before adding a new route — avoid duplicates.
>
> **Domain subpackage pattern** (added upstream, merged 2026-07-19): `contacts`, `gallery`, `history`, `memory`, and `research` routes now live under `routes/<domain>/<domain>_routes.py` instead of a flat `routes/<domain>_routes.py`. The old flat path still exists as a thin re-export shim — new domains should probably follow the subpackage pattern. See [[structure]].
>
> **Route domain map**: `specs/architecture-runtime-inventory.md` groups the 54 route files into Email, Chat/Agent, Cookbook, Model/LLM, Calendar/Contacts, Documents, Auth, Tasks, Session, Gallery, Memory, Research, MCP, Notes, and an "Other" bucket, with per-domain line counts and complexity ratings — check it before deciding which domain a new route belongs to.

## Companion Bridge API

> Source: `companion/README.md`. Additive LAN-discovery/pairing layer for the mobile companion app — no LLM logic duplication.

| Route | Auth | What It Does |
|-------|------|-------------|
| `GET /api/companion/ping` | Session/token | Health check |
| `GET /api/companion/info` | Session/token | Server identity/capabilities |
| `GET /api/companion/models` | Session/token | Caller's own model endpoints only (owner-filtered), never returns API keys |
| `GET /api/companion/pair` | Admin cookie | Renders the pairing form — never mints a token itself |
| `POST /api/companion/pair` (+ `?format=json`) | Admin cookie | Mints a one-time pairing token |

CSRF posture: minting is POST-only because the `SameSite=Lax` session cookie won't ride a cross-site POST; minting also invalidates the auth middleware's token cache. Source of truth: `token_owner`, `owner_can_see`, `mint_pairing_token`, `pairing.*` — tested in `tests/test_companion_readonly.py` and `tests/test_companion_pairing.py`.

## Claude / Codex Agent Bridge

Canonically `/api/codex/*` (shared by **all** agent integrations — "codex" naming is historic), plus `/api/claude/plugin.zip` and `/api/codex/plugin.zip` for bundle downloads. Tokens are scope-gated server-side per tool surface — a call outside the granted scope 403s until the matching Settings > Integrations toggle is enabled. Setup flow: Settings > Integrations > Add Agent > copy generated token + setup commands > toggle allowed tools > download the zip bundle and extract to `~/.claude/skills/` (Claude) or register via `~/.agents/plugins/marketplace.json` + `codex plugin add odysseus@personal` (Codex). Both explicitly forbid SSH/Docker/direct-DB/MCP-internals access — everything must go through `/api/codex/*`. See [[integrations]].

| Route | Auth Required | What It Does |
|-------|---------------|-------------|
| `GET /` | Session | Serve SPA (index.html) |
| `GET /login` | None | Login page (redirects if auth disabled) |
| `GET /notes`, `/calendar`, `/cookbook`, `/email`, `/memory`, `/gallery`, `/tasks`, `/library` | Session | SPA deep-link routes |
| `POST /api/auth/setup` | None | First-time admin setup |
| `POST /api/auth/signup` | None | User registration |
| `POST /api/auth/login` | None | Login (returns session cookie) |
| `POST /api/auth/logout` | None | Logout (clears session) |
| `GET /api/auth/status` | None | Auth status + current user |
| `POST /api/auth/change-password` | Session | Change password |
| `POST /api/auth/2fa/setup` | Session | Enable 2FA (returns TOTP URI) |
| `POST /api/auth/2fa/confirm` | Session | Confirm 2FA setup |
| `POST /api/auth/2fa/disable` | Session | Disable 2FA |
| `GET /api/auth/2fa/status` | Session | Check 2FA status |
| `GET /api/auth/users` | Admin | List users |
| `POST /api/auth/users` | Admin | Create user |
| `PUT /api/auth/users/{username}/privileges` | Admin | Update user privileges |
| `PUT /api/auth/users/{username}/rename` | Admin | Rename user |
| `DELETE /api/auth/users` | Admin | Delete users |
| `PUT /api/auth/open-signup` | Admin | Toggle open signup |
| `GET /api/auth/features` | None | Get enabled features |
| `POST /api/auth/features` | Admin | Set enabled features |
| `GET /api/auth/settings` | None | Get auth settings |
| `POST /api/auth/settings` | Admin | Set auth settings |
| `GET /api/auth/integrations` | Session | List integrations |
| `GET /api/auth/integrations/presets` | None | Integration presets |
| `POST /api/auth/integrations` | Session | Create integration |
| `PUT /api/auth/integrations/{id}` | Session | Update integration |
| `DELETE /api/auth/integrations/{id}` | Session | Delete integration |
| `POST /api/auth/integrations/{id}/test` | Session | Test integration |
| `GET /api/chat/...` | Session | Chat endpoints (streaming, send, etc.) |
| `GET /api/session/...` | Session | Session CRUD, listing, search |
| `GET /api/document/...` | Session | Document CRUD, versions, search |
| `GET /api/email/...` | Session | Email: list, read, send, search, folders, accounts, attachments, triage |
| `GET /api/calendar/...` | Session | Calendar: config, accounts, calendars, events, sync, .ics |
| `GET /api/cookbook/...` | Session | Cookbook: model scan, download, serve, dependencies, hardware fit |
| `GET /api/model/...` | Session | Model discovery, probing, provider management |
| `GET /api/shell/...` | Session/Agent | Shell command execution (admin-gated) |
| `GET /api/task/...` | Session | Scheduled task CRUD, run, webhook |
| `GET /api/note/...` | Session | Notes/todos CRUD, reminders |
| `GET /api/skills/...` | Session | Skills CRUD, audit, search |
| `GET /api/memory/...` | Session | Memory CRUD, search |
| `GET /api/history/...` | Session | Session history |
| `GET /api/gallery/...` | Session | Gallery: images, albums, upload, edit, search |
| `GET /api/mcp/...` | Admin | MCP server management |
| `GET /api/research/...` | Session | Deep research: run, status, results |
| `GET /api/contacts/...` | Session | CardDAV contacts CRUD, sync |
| `GET /api/codex/...` | API Token | Codex/Claude Code bridge endpoints |
| `GET /api/webhook/...` | Session | Webhook CRUD, test, logs |
| `POST /api/upload` | Session | File upload |
| `GET /api/backup/export` | Session | Export user data |
| `POST /api/backup/import` | Session | Import user data |
| `GET /api/compare/...` | Session | Model A/B comparison |
| `GET /api/copilot/...` | Session | GitHub Copilot device-flow |
| `GET /api/chatgpt-subscription/...` | Session | ChatGPT Subscription device-flow |
| `GET /api/personal/...` | Session | Personal document management |
| `GET /api/embedding/...` | Session | Embedding model management |
| `GET /api/tts/...` | Session | Text-to-speech |
| `GET /api/stt/...` | Session | Speech-to-text |
| `GET /api/signature/...` | Session | Reusable image stamps |
| `GET /api/vault/...` | Session | Secure vault CRUD |
| `GET /api/editor-draft/...` | Session | Image editor draft persistence |
| `GET /api/workspace/...` | Session | Workspace management |
| `GET /api/emoji/...` | None | Emoji SVG proxy (Twemoji) |
| `GET /api/health` | None | Liveness check |
| `GET /api/ready` | None | Readiness check (DB, data dir) |
| `GET /api/version` | None | App version |
| `GET /api/runtime` | None | Runtime info (Docker, Ollama URL) |
| `DELETE /api/wipe/{kind}` | Admin | Danger-zone data wipe |
| `GET /api/tokens` | Session | List API tokens (admin: all; user: own) |
| `POST /api/tokens` | Session | Create API token |
| `PATCH /api/tokens/{id}` | Session | Update API token |
| `DELETE /api/tokens/{id}` | Session | Delete API token |
| `GET /api/auth/assistant/...` | Session | Personal assistant settings, status, run |
| `POST /api/tasks/{id}/webhook/{token}` | None | External webhook trigger (token-authenticated) |
| `GET/POST/PUT/DELETE /api/feeds` | Session | Feed CRUD (list, create, update, delete, groups) |
| `GET /api/feeds/articles` | Session | List articles with filters (feed_id, group_id, read, starred, search, limit, offset) |
| `POST /api/feeds/{id}/refresh` | Session | Force refresh a single feed |
| `POST /api/feeds/refresh-all` | Session | Queue refresh of all feeds (background task) |
| `POST /api/feeds/articles/{id}/read` | Session | Mark article read/unread |
| `POST /api/feeds/articles/{id}/star` | Session | Star/unstar article |
| `POST /api/feeds/articles/read-all` | Session | Mark all articles as read (optionally by feed_id) |
| `POST /api/feeds/articles/{id}/summarize` | Session | AI-generated article summary |
| `POST /api/feeds/articles/{id}/full-content` | Session | Extract full article content via trafilatura |
| `POST /api/feeds/discover` | Session | Discover RSS feeds from a URL |
| `POST /api/feeds/opml/export` | Session | Export feeds as OPML (returns XML) |
| `POST /api/feeds/opml/import` | Session | Import feeds from OPML |
| `POST /api/feeds/groups/{group_id}/summarize` | Session | AI-generated digest of a group's articles |
| `GET /api/companion/ping/info/models/pair` | Session | Companion app endpoints |
