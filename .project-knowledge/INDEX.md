# Odysseus — Knowledge Index

> Last updated: 2026-07-28
> Status: Active
> Stack: FastAPI (Python 3.11+) + SQLite/SQLAlchemy + Vanilla JS SPA + ChromaDB
> Current goal: Sidebar restructuring and the Email 3-pane redesign are committed and pushed (`b205f82`, `236ef91`, `19aeec5`). Two follow-up polish rounds landed since: 2026-07-25 (icon-rail collapse, Theme layout overflow, new-mail pulse banner) and 2026-07-26 (folder-badge readability, modals now respect the Font setting, reader action row consolidated to 4 icon-only buttons with Reply/Reply All/Reply with AI folded into one dropdown). Agent mode can now actually run `sudo`: it prompts for the password in the UI and feeds it over stdin, uses the real `HOME`, and a missing `tool_progress` entry in the SSE relay whitelist (which also silently broke live tool output) is fixed — **confirmed working end-to-end 2026-07-27 with the user's real password**, not just a stub. `sudo` also now works when it's called *inside* a wrapper script rather than typed directly (`garuda-update`), via a real pty fallback, with ANSI escape codes stripped from pty output. 2026-07-28: `bash`/`python`/`manage_bg_jobs` moved into `ALWAYS_AVAILABLE` — the recurring "I don't have a shell tool" failures were keyword matching deciding *availability*, which can't work for context-dependent follow-ups like "cool check" (and ChromaDB, the semantic fallback, is still down). Verified live end-to-end. See [[history]] and [[roadmap]] (ChromaDB still offline; reboot pending after the kernel update). See [[sessions]] for details.

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
