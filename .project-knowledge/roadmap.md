# Roadmap

> Part of odysseus/.project-knowledge/ | Last updated: 2026-07-24
> Forward-looking only. Check this before starting any task — know what's in flight.

## Current Goal

Sidebar categorization (AI & Knowledge / Personal / Appearance) landed and is committed (`b205f82`). The Email 3-pane webmail redesign (folder sidebar + persistent list + persistent reading pane, collapsible/resizable) is built, verified live, and committed+pushed (`236ef91`). See Active TODOs below for what's still open (installing the systemd service, the RSS feature-proposal issue). See [[sessions]] and [[history]] for the full write-up.

---

## Known Bugs / Issues

(Detected from ROADMAP.md and code analysis, 2026-06-08)
- [ ] Fresh install smoke tests across Linux, macOS, Windows, Docker, WSL
- [ ] Integration audit — which integrations actually work vs. need setup docs
- [ ] Cookbook reliability across different machines, GPUs, shells
- [ ] Agent prompt/context bloat for smaller local models
- [ ] Skill/tool prompt-injection audit needed
- [ ] Email performance: IMAP folder select/fetch, cache invalidation bottlenecks
- [ ] CSS cleanup (single monolithic style.css)
- [ ] Tour/tutorial scaffolding needs shared helper
- [ ] Accessibility pass: keyboard nav, focus, contrast, reduced motion
- [ ] Better degraded-state reporting for ChromaDB, SearXNG, email, ntfy
- [ ] Dead code pass for old routes, stale feature flags, unused UI states
- [ ] Provider setup/probing audit for all LLM providers
- [ ] Offline/CDN vendor asset bundling

**Search & Deep Research improvements** *(planned 2026-06-09, partially fixed 2026-06-12)*
- [ ] **Surface the actually-used search provider in the UI** — a key-requiring provider (e.g. Exa) with no API key returns `[]` *silently* (not an error), so `_build_provider_chain` falls through to the fallback (default DuckDuckGo) on every query. Today the only signal is logs + the report's stats `Search:` field. Add a visible cue (warn/badge when the selected provider can't run; show which provider actually carried the run). Refs: `services/search/core.py:91-116`, `services/search/providers.py`, `src/deep_research.py:575-582,920`.
- [ ] **Expose / raise the deep-research length cap** — `research_max_tokens` (default 16384) caps report length and forces the synthesis step to compress; conflicts with "zero compression / fully developed" requests. Surface in the research UI with a note on the depth trade-off. Refs: `src/settings.py:90`, `src/deep_research.py:746`.
- [ ] **Chat auto web-search uses the whole message as one query** — `comprehensive_web_search(message, ...)` passes the raw user message verbatim as the query, so long/structured prompts search badly. Derive a focused (LLM-condensed) query, or skip auto-search for long messages and rely on the agent `web_search` tool (model-written queries). Refs: `src/chat_processor.py:277-285`.
- [ ] **Add server-side "Export to PDF"** for research reports / documents / chat answers — today PDF export is browser-print only (`window.print()`), and `/api/document/{id}/export-pdf` is form-fill only (requires a linked source PDF). No server-side markdown/HTML→PDF engine exists. Add a real report→PDF download. Refs: `src/visual_report.py:897`, `routes/document_routes.py:1384-1423`.
- [ ] **In-product guidance: chat vs deep research** — users conflate the two. Deep research = web-grounded summarizer pipeline (own template + token cap); chat/agent + `web_search` tool = model-led (like Perplexity/Opus). Add a tooltip/hint clarifying when to use each.

**RSS Feed Reader** *(added 2026-06-11, updated 2026-07-20)*
- [ ] **MCP server** — expose feed read/search/subscribe to the AI agent via MCP
- [ ] **YouTube embed video Error 153 persists** — in-page overlay embed (`www.youtube.com/embed/VIDEO_ID?autoplay=1`) gives YouTube's internal "Video player configuration error" for at least some videos (Diego Woods channel, possibly shorts). Workaround: "Watch on YouTube" link below the embed opens video directly on YouTube (counts views, new tab). Root cause is YouTube-side (embedding disabled per-video or per-channel), not CSP or origin.
- [x] ~~Keyboard shortcuts — j/k navigate articles, m read/unread, s star~~ — done 2026-07-20, see [[history]]
- [x] ~~Dedup UI — feedparser returns empty content/summary for YouTube entries~~ — done 2026-07-20, see [[history]]
- [x] ~~Group deletion — no UI for it~~ — done 2026-07-20, see [[history]]
- [x] ~~Drag-and-drop reorder feeds / move between groups~~ — done 2026-07-20, see [[history]]
- [x] ~~Can't drop a feed directly onto a collapsed group~~ — done 2026-07-20, see [[history]]
- [x] ~~TTS button relies on `window.aiTTSManager`~~ — done 2026-07-20, see [[history]]
- [x] ~~Per-feed fetch interval — `fetch_interval` column exists but not exposed in UI~~ — done 2026-07-20, see [[history]]
- [x] ~~Auto-refresh — need to wire into existing `task_scheduler`~~ — done 2026-07-20, see [[history]]
- [x] ~~Infinite scroll — article list is paginated but no scroll trigger~~ — done 2026-07-20, see [[history]]
- [x] ~~CSP scoped to HTTPS-images~~ — investigated 2026-07-20, closed as not safely implementable, see [[history]]

**One-shot LLM calls and reasoning-model leakage** *(added 2026-07-20)*
- [ ] **`query_llm()` has no generic reasoning-output handling** — only `routes/feed_routes.py`'s two RSS summarize endpoints call it today, and both now carry an explicit "no reasoning/commentary" system-prompt instruction as a workaround (see [[history]]). The underlying gap is in `llm_call()` itself: `_THINKING_MODEL_PATTERNS` (`src/llm_core.py:1233`) is a fixed name-list used to decide when to parse structured/`<think>`-tagged reasoning out of a response; a model outside that list (e.g. a hosted alias like `big-pickle` on OpenCode Zen) that reasons in plain prose with no separating tag/field will leak its full chain-of-thought into any *non-streaming* caller's result. Any future one-shot `query_llm()` consumer needs the same prompt-level workaround, or `llm_call()` needs a real fix (e.g. a "no visible reasoning" request-level flag, or a heuristic strip pass) so callers don't have to know about this per-model quirk themselves.

---

**Security (`THREAT_MODEL.md` "Known Gaps", cross-referenced to real issues)** *(added 2026-07-19)*
- [ ] **No shell/filesystem sandbox** for the agent's `bash`/`read`/`write` tools — see #1058 sandbox proposal.
- [x] ~~SSRF via `base_url` param on `/api/v1/chat` for chat-scoped tokens~~ — confirmed fixed 2026-07-20, see [[history]]. PR #1039 itself closed unmerged, but equivalent hardening landed via a different commit path (`validate_public_http_url()`, `src/url_security.py:81`).
- [ ] **`src/search/` vs `services/search/` — partially answered.** THREAT_MODEL.md confirms `core.py`/`providers.py` under `src/search/` alias `services/search` via `sys.modules` (not dead code), **but** `analytics.py`/`cache.py`/`content.py`/`query.py`/`ranking.py` are still independent copies that can drift from the real `services/search/` implementation. Tracked as #1058. (This replaces the earlier "fully unconfirmed" note — the aliasing is real, the drift risk on the 5 non-aliased files is the remaining gap.)
- [ ] **Coarse token scopes** — only `chat`/`admin` scope tiers exist, no per-capability grant. See [[schema]] access rules.

**Refactor plan** (`specs/architecture-runtime-inventory.md`, 2026-06-16 snapshot for #4082/#4071 — priorities 1–2 already executed, see [[structure]]) *(added 2026-07-19)*
- [ ] Priority 3: split `src/agent_loop.py` (2,961 lines) → `src/agent/loop.py` + submodules (#3266)
- [ ] Priority 4: layer `src/` into `pkg/domain/infra/api` — after routes/tools are stable
- [ ] Priority 6: split `core/database.py` (2,265 lines, 28 classes, 102 importers) — **explicitly planned last**, highest blast radius
- [ ] Priority 7/8: modularize `static/style.css` (36,653 lines, #2617) and `static/js/document.js` (9,776 lines) — not yet started
- [ ] Fix the documented cross-layer smell: 8 function-body imports inside `src/tool_implementations.py` reach into `routes/*.py`
- [ ] Open questions (unresolved): is #2538 the canonical behavior-map baseline; should compat shims (`__init__.py` re-exports from the 2026-07-19 split) be temporary or permanent; should an ADR track these refactor decisions

**Test suite refactor** (issue #2523, in progress — see [[systems]] for what's already built) *(added 2026-07-19)*
- [ ] Move CLI tests (28 files, listed in `tests/LAYOUT_INVENTORY.md`, issue #3712) into `tests/cli/` — not yet executed
- [ ] Split oversized test files (issue #3983, `tests/OVERSIZED_TEST_SPLIT_PLAN.md`): `test_model_routes.py` (1,778 lines / 139 tests) and `test_security_regressions.py` (1,224 / 92) are top candidates but deferred as high-risk; safer first targets are `test_provider_classification.py`, `test_provider_endpoints.py`, `test_llm_core_temperature.py`
- [ ] CI-hardening track: pytest-randomly → fix order-dependent tests → coverage reporting → make it a blocking gate → pytest-xdist — none of these stages are done yet

**ROADMAP.md backlog not yet listed above** *(added 2026-07-19)*
- [ ] Cookbook: hardware-tiered model presets for Deep Research; rank cookbook models by hardware fit; improve error feedback/logging UX; SGLang cross-platform support
- [ ] Better AI-assisted Notes/Todos integration
- [ ] Modal/window positioning fragility; mobile `@media` override discoverability
- [ ] More endpoint/provider-probing tests (Anthropic, Gemini, Groq, xAI, OpenRouter, OpenAI, DeepSeek)
- [ ] Better scheduler defaults/visibility
- [ ] Admin-tool security hardening docs
- **Explicit non-goal right now**: no more themes (per ROADMAP.md — don't propose new theme work unprompted)

---

## Active TODOs

- [ ] Confirm `pip-audit`/Trivy findings aren't silently ignored — both are advisory-only in CI (see [[systems]] Security CI), so nothing blocks on them today. *(added 2026-07-19)*
- [ ] **User needs to run `./install-service.sh` themselves** to actually install the "always on" systemd service (now executable — see [[history]]) — requires sudo, left for the user to run, not yet confirmed done. *(added 2026-07-24)*
- [ ] GitHub issue [#5688](https://github.com/odysseus-dev/odysseus/issues/5688) — proposed the RSS backlog batch as a new feature per `CONTRIBUTING.md`'s no-bulk-agent-PR policy (one issue, not multiple PRs). Awaiting maintainer response before opening any PR. *(added 2026-07-24)*
