# External Integrations & Data Contracts

> Part of odysseus/.project-knowledge/ | Last updated: 2026-07-19
> Document exact field contracts — never guess the shape.

**Microsoft Outlook / Office 365 (email)**
- Odysseus email is IMAP/SMTP **username-password only**. Microsoft disables basic auth for most modern tenants, causing `AUTHENTICATE failed` / `535 5.7.139` errors.
- **No OAuth/Graph Mail support yet** — documented known limitation, planned future direction, not built. Source: `docs/email-outlook.md`.

**Agent Migration Manifest** (`agent-migration.v1`, `scripts/agent_migration_manifest.py`)
- Read-only, JSON-only, source-neutral pipeline: `source export → adapter → manifest → preview → apply`. This is a **spec/tooling layer, not yet a wired-in importer feature** — no UI hookup.
- Item kinds: `memory`, `skill` (SKILL.md + frontmatter), `conversation_thread`, `archive_document`. Content embedding is opt-in per kind (`--include-archive-content`, `--include-conversation-content --max-conversation-messages`) to avoid bloating/leaking manifests by default.
- Recognizes ChatGPT `conversations.json` `mapping`-format exports.
- Recommended future-importer apply order: dry-run summary → backup `data/` → import archive docs as documents (not memory) → import conversation threads as searchable/cited archive context (not memory) → show memory candidates for review → import skills after conflict check → skip secrets by default.
- Source: `docs/agent-migration.md`.

**SearXNG (self-hosted search engine)**
- Writes to: none (read-only)
- Reads from: SearXNG API at configured `SEARXNG_INSTANCE` URL
- Triggered by: user search requests
- Config: `config/searxng/settings.yml`

**ChromaDB (vector store)**
- Writes to: ChromaDB collections via HTTP API at configured host:port
- Reads from: ChromaDB collections
- Used by: personal docs RAG, tool selection index, memory vectors

**ntfy (push notifications)**
- Receives: notification requests from task scheduler and email triage
- Channel: HTTP POST to configured ntfy server

**CalDAV providers** (Radicale, Nextcloud, Apple, Fastmail)
- Writes to: remote CalDAV calendar collections
- Reads from: remote CalDAV calendar collections
- Auth: username/password per account

**CardDAV providers** (contacts)
- Writes to: remote CardDAV address book
- Reads from: remote CardDAV address book
- Auth: username/password per contact entry

**IMAP/SMTP (email)**
- Reads from: IMAP inbox (configurable folders)
- Writes to: IMAP (move, flag, delete) + SMTP (send)
- Data: `email_accounts` table stores credentials (Fernet-encrypted); `email_messages` caches fetched mail

**Codex / Claude Code integrations**
- Writes to: API via api_token scoped routes (todos, email, calendar, documents)
- Reads from: same scoped routes
- Auth: API bearer tokens (`ody_*`)
