---
layout: default
---

# Workspace instructions and persona binding

Agent chats bound to a workspace folder can pick up that folder's own
operating manual, the same `CLAUDE.md` / `AGENTS.md` files that Claude Code
and Codex read. A persona can also carry its workspace and MCP servers, so
selecting it is enough to start a correctly scoped agent.

## Instruction files

When a chat has a workspace, Odysseus looks for `AGENTS.md`, else
`CLAUDE.md`, in every directory from the trusted root down to the workspace.
Files are added outermost first, so a shared root file comes before a project
file. A line that holds only `@relative/path.md` is replaced by that file's
contents (nested up to 4 levels, each file once). The result is appended to
the persona's system prompt under **Project instructions**, and the agent
gets *project mode* rules instead of the generic coding rules.

### Trust

Instruction files become system instructions, so they are only read from
folders the admin trusts:

```bash
ODYSSEUS_TRUSTED_INSTRUCTION_ROOTS=/vaults            # os.pathsep- or comma-separated
```

A workspace outside every trusted root (a freshly cloned repo, for example)
never has its `AGENTS.md` loaded. Imports can't leave the trusted root and
must be `.md` files. The total is capped at 60 000 characters.

## Persona binding

In **Character → Persona → Workspace & tools**:

- **Workspace folder** — agent chats with this persona are confined to it,
  unless a different workspace is picked for the chat.
- **MCP servers** — comma-separated server names or ids the persona may use;
  others are hidden and blocked. Empty means all enabled servers. Built-in
  Odysseus servers (memory, email, …) are unaffected.

Both are saved with the persona template and only apply while the persona is
active. Workspaces are an admin/single-user feature, as before.

## Docker

Mount the folder and trust its parent, e.g. in `docker-compose.override.yml`:

```yaml
services:
  odysseus:
    volumes:
      - ${HOME}/notes:/vaults/notes
    environment:
      - ODYSSEUS_TRUSTED_INSTRUCTION_ROOTS=/vaults
```

The app runs as `PUID`, so files the agent writes are owned by that user.
