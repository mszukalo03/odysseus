# Odysseus — Knowledge Index

> Last updated: 2026-10-04
> Status: Active
> Stack: FastAPI (Python 3.11+) + SQLite/SQLAlchemy + Vanilla JS SPA + ChromaDB
> Current goal: Vault personas, thinking control, live voice and workspace search (2026-10-04). Branches `fix/vault-persona` → `feat/thinking-control` → `feat/voice-mode` → `feat/workspace-rag` (stacked on `dev`): personas that bind an Obsidian vault side now answer instead of exploring to the step cap (`src/agent_budget.py`, protected instructions in trimming, answer-first project rules, `ODYSSEUS.md` lean manuals, grep count, vault-root path fallback); per-chat thinking level (`src/reasoning_control.py`); hands-free voice mode (`static/js/voiceMode.js`, local-speech routing); workspace-scoped retrieval. Not yet pushed or deployed to the XPS. Vault-side plan and status: `personal/10-projects/software/odysseus/readme.md`.

## What This Project Does
A self-hosted AI workspace — an open-source alternative to ChatGPT/Claude that runs on local hardware. Provides chat with any OpenAI-compatible LLM, an agent with tools (files, shell, web, MCP, memory, skills), deep research, email/calendar/contacts sync, notes/tasks, document editing, image generation, model management ("Cookbook"), RSS feed reader, and more. Privacy-first, local-first.

---

## Files in This Folder

| File | Contents | Load when... |
|------|----------|--------------|
| `stack.md` | Tech stack, dev commands, env vars | Setting up, adding deps, checking env vars |
| `structure.md` | File tree, entry points, key files | Navigating the codebase, adding new files |
| `schema.md` | DB schema (30+ tables) | Any database work |
| `routes.md` | 150+ API routes across 55+ route files | Adding/changing endpoints |
| `systems.md` | Auth, email, search, AI, RSS, etc. — status table | Touching any cross-cutting system |
| `features.md` | User-facing features and workflows | Understanding what's built, adding features |
| `integrations.md` | External systems (SearXNG, ChromaDB, ntfy, CalDAV, CardDAV, IMAP/SMTP, Codex) | Touching any external integration |
| `roadmap.md` | Known bugs, active TODOs, current goal | Starting any task — know what's in flight |
| `history.md` | Fixes and architectural decisions (append-only) | Debugging, reviewing past decisions |
| `sessions.md` | Session-by-session log (append-only) | Reviewing work history |

> No `hooks.md` or `components.md` — this is a vanilla-JS/Python project, not a component-framework frontend.

---

## Context Loading Guide

| Task | Load these files |
|------|-----------------|
| Adding a route | `routes.md` + `schema.md` |
| DB / schema change | `schema.md` + `integrations.md` |
| Fixing a bug | `roadmap.md` + `history.md` |
| Wiring/touching a system (auth, email, search, RSS...) | `systems.md` + `stack.md` |
| Understanding a feature or flow | `features.md` + `routes.md` |
| Touching an external integration (CalDAV, IMAP, ChromaDB...) | `integrations.md` + `schema.md` |
| Merging/syncing with upstream | `roadmap.md` + `sessions.md` |
| General orientation (new session) | This file → then pick by task |
| Full audit | All files |
