# Systems

> Part of odysseus/.project-knowledge/ | Last updated: 2026-07-24

| System | Status | Details |
|--------|--------|---------|
| Authentication | ✅ Active | bcrypt, session cookies, 2FA (TOTP), API bearer tokens, per-user privileges |
| Database | ✅ Active | SQLite via SQLAlchemy ORM (30+ tables) |
| AI / LLM | ✅ Active | OpenAI-compatible client, multiple providers, streaming, tool calling |
| Web Search | ✅ Active | Pluggable provider registry (10 providers): SearXNG, DuckDuckGo, Brave, Google PSE, Tavily, Serper, Bing, Search1API, Firecrawl, Exa. Per-provider API key (settings.json) with env-var fallback |
| Agent | ✅ Active | Tool-using agent with MCP, web, files, shell, memory, skills |
| Memory / Skills | ✅ Active | Persistent memory + skills, vector + keyword retrieval, ChromaDB + fastembed |
| RAG (Personal Docs) | ✅ Active | ChromaDB-backed semantic document search |
| Email | ✅ Active | IMAP/SMTP multi-account, AI triage, auto-reply, urgency detection. Frontend redesigned to a 3-pane webmail layout (folder sidebar / message list / reading pane) 2026-07-24, see [[features]] and [[history]] |
| Calendar | ✅ Active | CalDAV sync, local-first, .ics import/export |
| Notes/Tasks | ✅ Active | Notes with reminders, checklists, cron-style scheduled tasks |
| Cookbook (Model Mgmt) | ✅ Active | Hardware scan, model download, vLLM/llama.cpp serving |
| Deep Research | ✅ Active | Multi-step web research with visual report generation |
| MCP (Model Context Protocol) | ✅ Active | Built-in MCP servers: browser, email, memory, image gen, RAG |
| Webhooks | ✅ Active | Outgoing webhook management |
| Task Scheduler | ✅ Active | Cron-style in-process scheduler |
| Background Jobs | ✅ Active | Monitor for long-running tasks |
| Image Generation | ⚠️ Partial | Diffusion model integration present |
| TTS / STT | ✅ Active | Text-to-speech and speech-to-text providers |
| Gallery | ✅ Active | Photo album management, EXIF, tags |
| Contacts | ✅ Active | CardDAV contacts sync |
| Vault | ✅ Active | Encrypted secure storage |
| Shell | ✅ Active | User-facing command execution (admin-gated) |
| Companion App | ✅ Active | Mobile companion pairing endpoints |
| Codex / Claude Integration | ✅ Active | External AI code editor bridge |
| RSS Feed Reader | ✅ Active | RSS/Atom feed reader with AI summaries (incl. YouTube transcript fallback), OPML import/export, 3-pane UI, YouTube channel support, keyboard nav, drag-to-reorder/move, optional auto-refresh (`refresh_due_feeds` scheduled task, off by default) |
| Notifications (ntfy) | ✅ Active | Push notification support |
| Docker Deployment | ✅ Active | Docker Compose with GPU overlays |
| PWA | ✅ Active | Service worker, manifest.json |
| Test Suite Governance | ✅ Active | Taxonomy auto-tagging + focused runner + order-diagnostic (see below) |
| Security CI | ✅ Active | gitleaks/actionlint/zizmor/dependency-review/hadolint (blocking) + pip-audit/Trivy/CodeQL (advisory), see below |
| Backup & Restore | ✅ Active | `scripts/odysseus-backup` — see [[history]] |

---

## Auth & Threat Model Internals

> Source: `THREAT_MODEL.md`. Supplements the one-line "Authentication" row above.

- bcrypt password hashing + 7-day session tokens in `data/sessions.json` (`core/atomic_io.py` for atomic writes).
- TOTP 2FA with 8 single-use backup codes, checked after password verification and before session issuance.
- Reserved usernames `internal-tool` / `api` / `demo` / `system` (`core/auth.py:RESERVED_USERNAMES`) — cannot be registered by real users.
- **`internal-tool` loopback is security-critical**: `core/middleware.py:require_admin` treats `current_user == "internal-tool"` as the loopback identity and grants admin unconditionally. Startup generates `INTERNAL_TOOL_TOKEN` via `secrets.token_hex(32)` (never persisted, never sent to clients); loopback requests carry `X-Odysseus-Internal-Token` or a pre-set `current_user`. `src/tool_security.py:owner_is_admin_or_single_user` gates agent-issued admin tool calls even when the agent runs inside a non-admin user's session.
- Orphan-session re-check runs on every `validate_token` call.
- **Prompt-injection hardening** lives in `src/prompt_security.py`: `untrusted_context_message(label, content)` wraps external content as **user-role data**, never system-role instruction, plus a `UNTRUSTED_CONTEXT_POLICY` system-prompt preamble. Surfaces required to use this wrapper: web search results, fetched URLs, read emails, saved memories, skill text, notes, any external tool output.
- Security headers (`core/middleware.py:SecurityHeadersMiddleware`): `X-Frame-Options: DENY` + `frame-ancestors 'none'` (except sandboxed tool-render iframes), `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, nonce-based CSP `script-src`. `style-src 'unsafe-inline'` is **intentionally** kept (inline styles/JS-set style attrs) — visual-risk only, accepted tradeoff.
- Roles/capabilities: non-admin gets chat/browser/documents/research/image-gen/memory. Admin-only: shell/Python execution, file read-write, email send/read, MCP tools, calendar management, token/webhook management, model serving, vault, settings. Defaults in `core/auth.py:DEFAULT_PRIVILEGES`; enforcement in `src/tool_security.py:NON_ADMIN_BLOCKED_TOOLS`; any `mcp__`-prefixed tool name is blocked for non-admins.
- Known gaps (tracked, not yet fixed) — see [[roadmap]] for the linked issues.

## Test Suite Governance

> Source: `tests/README.md`, `tests/TESTING_STANDARD.md`.

- `tests/_taxonomy.py` auto-tags tests by filename token into `area_*`/`sub_*` pytest markers (areas: security, routes, services, cli, js, helpers, unit, uncategorized).
- `tests/run_focus.py --area <area> [--sub-area X] [--fast] [--last-failed] [--durations]` — focused runner.
- `tests/run_order_report.py` — order-sensitivity diagnostic (seeded shuffle), **report-only, not a CI gate**.
- Shared helper library under `tests/helpers/` (`cli_loader`, `import_state`, `sqlite_db`, `db_stubs`), each with an explicit "do not stretch to X" scope note in its docstring.
- Target refactor (issue #2523, in progress — see [[roadmap]] for status): move toward `tests/unit/`, `cli/`, `js/`, `security/`, `routes/`, `services/`, `integration/` directories and a CI-hardening track (pytest-randomly → fix order-deps → coverage reporting → blocking gate → pytest-xdist).

## Security CI

> Source: `docs/security-ci.md`.

- **Blocking**: gitleaks (secret scan), actionlint + zizmor (workflow security), dependency-review, hadolint (Dockerfile).
- **Advisory only**: pip-audit, Trivy (container scan), CodeQL (via GitHub's default code-scanning setup — a dynamic workflow, **not** checked into the repo; don't add a duplicate checked-in CodeQL workflow while default setup is active, it conflicts).
- `.github/dependabot.yml` opens weekly PRs for Python/npm/Docker-base-image/pinned-GitHub-Actions updates.
- Branch protection requires: the status checks above + CODEOWNERS review.
