# Schema

> Part of odysseus/.project-knowledge/ | Last updated: 2026-07-20
> SQLite via SQLAlchemy ORM. Tables defined in `core/database.py`. Source of truth is that file — this is a navigable summary.

## Tables

| Table | Key Columns | Relations |
|-------|-------------|-----------|
| `sessions` | id (PK), name, endpoint_url, model, owner (FK→users), rag, archived, folder, is_important, message_count, total_input_tokens, total_output_tokens, mode, crew_member_id, last_message_at | has_many chat_messages, has_many documents, has_many gallery_images |
| `chat_messages` | id (PK), session_id (FK→sessions), role (user/assistant/system), content, metadata, timestamp | belongs_to session |
| `chat_messages_fts` | Virtual table for FTS5 full-text search on chat_messages | |
| `documents` | id (PK), session_id (FK→sessions), title, language, current_content, version_count, is_active, archived, owner, tidy_verdict, source_email_uid/folder/account_id/message_id | has_many document_versions, belongs_to session |
| `document_versions` | id (PK), document_id (FK→documents), version_number, content, summary, source (ai/user) | belongs_to document |
| `gallery_images` | id (PK), filename (unique), prompt, model, size, quality, tags, ai_tags, session_id (FK→sessions), album_id (FK→gallery_albums), owner, is_active, favorite, file_hash, taken_at, camera_make/model, gps_lat/lng, width, height, file_size | belongs_to session, belongs_to album |
| `gallery_albums` | id (PK), name, description, cover_id (FK→gallery_images), owner | has_many gallery_images |
| `email_accounts` | id (PK), owner, is_default, imap_host/port/username/password (encrypted), smtp_host/port/username/password (encrypted), imap_use_ssl, smtp_use_ssl, provider, routing_rules (JSON), polling_enabled, sync_folder | |
| `email_messages` | id (PK), account_id (FK→email_accounts), folder, uid, message_id (Message-ID header), subject, from_addr, to_addrs, cc_addrs, date, body_text, body_html, flags, is_read, is_flagged, is_urgent, thread_id, in_reply_to | belongs_to email_account |
| `scheduled_tasks` | id (PK), name, owner, action, params (JSON), schedule (cron expr), enabled, last_run_at, next_run_at, is_event, webhook_enabled, webhook_token, webhook_url, end_after_min | |
| `api_tokens` | id (PK), name, token_prefix, token_hash, owner, scopes, is_active, last_used_at | |
| `webhooks` | id (PK), name, url, events (JSON), owner, secret, is_active | |
| `notes` | id (PK), owner, title, content, color, is_pinned, is_archived, reminder_at, checklist (JSON), tags, folder, shared_with | |
| `contacts` | id (PK), owner, carddav_url, carddav_username, carddav_password (encrypted), name, email, phone, organization, photo, vcard_raw, sync_token, etag | |
| `crew_members` | id (PK), name, system_prompt, model, endpoint_url, temperature, owner, session_id (FK→sessions) | belongs_to session |
| `memories` | id (PK), text, category, source, owner, session_id (FK→sessions), timestamp | belongs_to session; vector copies in `data/memory_vectors/` (ChromaDB/fastembed) |
| `settings` | key (PK), value, type | |
| `notifications` | id (PK), owner, title, body, type, link, is_read, created_at | |
| `calendar_cache` | id (PK), owner, cal_data (JSON), account_id, calendar_id | |
| `editor_drafts` | id (PK), owner, name, state (JSON), thumbnail, updated_at | |
| `vault_items` | id (PK), owner, name, content (encrypted), type | |
| `feed_groups` | id (PK), owner, name, parent_id (self-referential FK, nested groups) | has_many feeds |
| `feeds` | id (PK), owner, group_id (FK→feed_groups), title, site_url, feed_url, icon, fetch_interval, last_fetched, error_count, last_error, enabled, sort_order (2026-07-20, drag-to-reorder/move), created_at | has_many articles, belongs_to feed_group |
| `feed_sync_accounts` | id (PK), owner, service (greader/newsblur/inoreader), credentials (encrypted), sync_enabled, last_synced | |

> **Not a DB table:** Skills are file-based `SKILL.md` files under `data/skills/<category>/<name>/` (YAML frontmatter + markdown body), managed by `SkillsManager` (`services/memory/skills.py`); usage counters in `data/skills/_usage.json`. Owner-scoped via the `owner:` frontmatter field. See [[history]] for the correction note.

## Access Rules

- Most tables are owner-scoped: queries filter by `owner == current_user` or `owner IS NULL`
- Admin routes require `require_admin` decorator (checks user is admin in auth.json)
- Sessions scoped per-owner; legacy null-owner sessions are shared
- API tokens are scoped (e.g. `chat`, `email:read`, `todos:write` — coarse-grained, see [[roadmap]] known gap: only `chat`/`admin` scope tiers exist, no per-capability grant)
- `internal-tool` loopback bypasses owner checks for agent-internal calls

## Attachment Reference Contract

> Source: `docs/attachments.md`. No distinct "artifact" table exists — artifact-like refs are covered by the same attachment-ID reference scan across chat/document/gallery/notes/calendar; any future artifact store must be added to that scan before cleanup.

- Shape: `{"type":"attachment_ref","attachment_id":"<hex>.<ext>","name","mime","size","checksum_sha256","created_at"}`, optionally `width`/`height`/`vision`/`vision_model`/`gallery_id`.
- Persisted in `chat_messages.content` as text + reference lines (**never** raw `data:` URLs) and in `chat_messages.metadata.attachments`. FTS migration/triggers scrub legacy base64-indexed rows.
- Upload lifecycle backed by `uploads.json` (owner, checksum, mime, size, created_at) — not a DB table. Cleanup fails closed if the attachment-reference scan can't complete. Deletions atomically drop the `uploads.json` row first, restoring it if filesystem removal fails. Writers must take an owner-checked reservation that serializes with deletion; cleanup and write-reservation share the upload-index lock. **Documented single-worker-only correctness** — multi-process deployment needs a real lock or DB-backed lifecycle state (not yet built).
- `data/.app_key` — the Fernet encryption key referenced across backup docs alongside `app.db`, vault, memory, RAG indexes, personal docs, and uploads as the core `data/` state to back up.
