# Systems

> Part of odysseus/.project-knowledge/ | Last updated: 2026-07-27

| System | Status | Details |
|--------|--------|---------|
| Authentication | ✅ Active | bcrypt, session cookies, 2FA (TOTP), API bearer tokens, per-user privileges |
| Database | ✅ Active | SQLite via SQLAlchemy ORM (30+ tables) |
| AI / LLM | ✅ Active | OpenAI-compatible client, multiple providers, streaming, tool calling |
| Web Search | ✅ Active | Pluggable provider registry (10 providers): SearXNG, DuckDuckGo, Brave, Google PSE, Tavily, Serper, Bing, Search1API, Firecrawl, Exa. Per-provider API key (settings.json) with env-var fallback |
| Agent | ✅ Active | Tool-using agent with MCP, web, files, shell, memory, skills. Runs on the host as the real user (not a sandbox). `sudo` is supported: the bash tool prompts the browser for a password (`src/sudo_auth.py` + `/api/agent/sudo/*` + `static/js/sudoPrompt.js`), held in memory only. See [[history]] |
| Memory / Skills | ✅ Active | Persistent memory + skills, vector + keyword retrieval, ChromaDB + fastembed |
| RAG (Personal Docs) | ✅ Active | ChromaDB-backed semantic document search |
| Email | ✅ Active | IMAP/SMTP multi-account, AI triage, auto-reply, urgency detection. Frontend redesigned to a 3-pane webmail layout (folder sidebar / message list / reading pane) 2026-07-24; new-mail pulse banner added 2026-07-25 (`GET /api/email/unread-state`'s `latest` field). See [[features]] and [[history]] |
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

## Adding a New Agent Tool — Registration Checklist

> A tool JUST added to `FUNCTION_TOOL_SCHEMAS` will not actually reach the model in most turns and will be denylisted in Plan Mode — the pipeline has ~7 registration points, discovered while wiring `get_home_weather`/`get_homelab_updates` (2026-07-25, see `src/tools/ithaca.py` for a worked example of a simple read-only tool).

1. **Schema** — `src/tool_schemas.py`: add the OpenAI-style entry to `FUNCTION_TOOL_SCHEMAS`.
2. **Implementation** — new or existing `src/tools/<domain>.py`: `async def do_<name>(content, owner=None) -> Dict`. Wrap the WHOLE body in `try: ... except Exception as e: logger.error(...); return {"error": str(e), "exit_code": 1}` (every sibling — `calendar.py`, `notes.py` — does this; a tool that only catches its own expected exception type lets a raw network/DB failure propagate unhandled). Re-export it from `src/tool_implementations.py`.
3. **Dispatch** — `src/tool_execution.py`: import the `do_*` function and add an `elif tool == "<name>":` branch in `_execute_tool_block_impl` (or register in `TOOL_HANDLERS` in `src/agent_tools/__init__.py` for the newer class-based style).
4. **Recognized-tool set** — `src/agent_tools/__init__.py`'s `TOOL_TAGS`: without this, the fenced/XML tool-call parsers (`tool_parsing.py`) silently drop the call even if native function-calling delivers it fine.
5. **RAG selection** — `src/tool_index.py`'s `BUILTIN_TOOL_DESCRIPTIONS` (a richer description than the schema one, embedded for retrieval) — **this is the actual gate that decides whether the tool's schema gets sent to the model at all**; `_relevant_tools` (RAG + keyword hits + `ALWAYS_AVAILABLE`) filters `FUNCTION_TOOL_SCHEMAS` down before every API call (`agent_loop.py` ~3910). Add keyword-hint `frozenset(...): {tool_names}` entries too if there's an obvious trigger phrase.
6. **Prompt guidance** — `src/agent_loop.py`'s `TOOL_SECTIONS`: a one-liner (`"- \`name\` — description"`) for simple/no-arg tools, or a fenced example block for tools with a non-obvious arg shape. Read from here into `_assemble_prompt()`'s "## Available tools" / "## Additional tools" text.
7. **Plan Mode allowlist** — `src/tool_security.py`'s `PLAN_MODE_READONLY_TOOLS`: fail-closed by design (every tool in `FUNCTION_TOOL_SCHEMAS` is denylisted in Plan Mode unless explicitly allowlisted here) — a genuinely read-only tool left out is silently unusable in Plan Mode. Only add mutating tools if there's a corresponding `_PLAN_MODE_KNOWN_MUTATORS` reason not to.
8. **Turn routing — the two gates that decide whether the schema is ever sent.** Points 1–7 make a tool *callable*; these decide whether the model is even offered it, and BOTH default to "no". Missing either gives the identical symptom: the tool looks fully wired, RAG retrieval returns it, and the LLM still answers from training data — often claiming it "doesn't have real-time access". Discovered 2026-07-26 for `get_home_weather`/`get_homelab_updates`, the third time this bit (see also the `contacts` domain and the api_call/`integrations` domain, each with its own regression test).
   * **`src/action_intents.py`'s `_ROUTING_PATTERNS`** — chat mode is the UI default, and `routes/chat_routes.py`'s `chat_mode == "chat"` branch calls the LLM with **`tools=None`**. The turn only reaches the agent path if `classify_tool_intent` matches, so with no pattern the tool is unreachable regardless of everything else. Append new patterns at the END of the tuple: first match wins, so appending cannot change an existing category. They must not steal coding turns — auto-escalation withholds bash/python/read_file/write_file for every category except `shell`/`workspace`.
   * **`src/agent_loop.py`'s `_classify_agent_request` domains** — `low_signal = not continuation and not domains`, and a low-signal *first* turn takes the `_direct_low_signal` path, which replies from a bare user message with **no system prompt and no tools** (visible in metrics as `direct_low_signal: true`, `input_tokens` in the tens, `tool_calls: 0`). Short questions like "is it raining" match no domain and land here even after chat→agent promotion. Add a domain, then add the SAME key to `_DOMAIN_TOOL_MAP` (seeds the tools deterministically, independent of embedding retrieval) **and** to `_DOMAIN_RULES` — `_domain_rules_for_tools` does `_DOMAIN_RULES[domain]` and raises KeyError otherwise.
9. **Secrets, if any settings the tool reads are user-configurable API keys**: name the setting `*_api_key`/`*_token`/`*_secret`/`*_password`-suffixed in `src/settings.py`'s `DEFAULT_SETTINGS` — `src/settings_scrub.py:is_secret_key()` and `admin_tools.py`'s `do_manage_settings` `_is_secret()` both auto-mask/protect by name shape, no extra code needed.

Tests worth writing: the tool's own logic (mock the fetch/impl layer), `"<name>" in TOOL_TAGS`, `FUNCTION_TOOL_SCHEMAS` has exactly one entry, (if read-only) `"<name>" not in plan_mode_disabled_tools()` — see `tests/test_plan_mode.py` — and both routing gates: `classify_tool_intent(<phrasing>).needs_tools` for the phrasings users actually type, plus the domain/`_DOMAIN_TOOL_MAP`/`_DOMAIN_RULES` trio (`tests/test_tool_rag_ithaca_domain.py` also guards `set(_DOMAIN_TOOL_MAP) - set(_DOMAIN_RULES)` being empty for every domain).

**Verify a new tool end-to-end, not by inspection.** Registration, RAG retrieval and the schema list can all look correct while a routing gate silently drops the turn. Drive a real chat-mode request against a scratch instance and assert on the tool event plus `input_tokens` — a full agent prompt is thousands of tokens, the direct path is tens.

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
