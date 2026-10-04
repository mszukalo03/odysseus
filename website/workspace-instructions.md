---
layout: default
---

# Workspace instructions and persona binding

Agent chats bound to a workspace folder can pick up that folder's own
operating manual: the same `CLAUDE.md` / `AGENTS.md` files that Claude Code
and Codex read, or an Odysseus-only `ODYSSEUS.md`. A persona can also carry its workspace and MCP servers, so
selecting it is enough to start a correctly scoped agent.

## Instruction files

When a chat has a workspace, Odysseus looks for `ODYSSEUS.md`, else
`AGENTS.md`, else `CLAUDE.md`, in every directory from the trusted root down
to the workspace. `ODYSSEUS.md` lets a folder give Odysseus a leaner manual
(useful for smaller or local models) while Claude Code keeps reading
`CLAUDE.md` from the same folder; the choice is made per directory.
Files are added outermost first, so a shared root file comes before a project
file. A line that holds only `@relative/path.md` is replaced by that file's
contents (nested up to 4 levels, each file once). The result is appended to
the persona's system prompt under **Project instructions**, and the agent
gets *project mode* rules instead of the generic coding rules.

### Project mode

Project mode is tuned for notes vaults as much as code:

- **Answer first.** Quick questions (a count, a status, a lookup) skip the
  manual's session routine: find the file, answer, stop. Opening and closing
  steps (orienting on logs, writing a session log) are for substantive work.
- **Tools.** The read/write file tools and `grep`/`glob`/`ls` are always
  offered, whatever the message looks like; shell stays on the normal
  selection. Plan mode still removes write tools. `grep` has a `count` mode
  for exact row/entry counts.
- **Paths.** A relative path written from the parent root (`thesis/notes.md`
  while the workspace *is* `thesis`) resolves inside the workspace when the
  literal path doesn't exist. Paths can't leave the workspace.
- **Context.** The persona prompt plus instructions are never trimmed, and
  the user's request stays in context through long tool exchanges. The rest
  of the conversation is trimmed around them, so give the endpoint a real
  context length: an unknown window falls back to a 6000-token budget.
- **Finishing.** Two steps before the step cap the agent is told to wrap up,
  and the final step runs without tools so the turn ends with an answer
  (Continue still appears). In project mode a long streak of tool-only steps
  also draws a nudge.

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
  others are hidden, left out of the prompt and blocked, and the named
  servers' tools are offered every turn when the workspace has project
  instructions. Empty means all enabled servers (selected as usual). Built-in
  Odysseus servers (memory, email, …) are unaffected.
- **Thinking** — the default thinking level for this persona's chats
  (Model default / Off / Low / Medium / High). The chat's own Thinking control
  overrides it. Off suits quick lookups over notes.
- **Max agent steps** — a per-persona step cap (1–200); empty uses the global
  *Agent max rounds* setting. A dozen is plenty for a vault assistant on a
  small local model.

All of these are saved with the persona template and only apply while the
persona is active. Workspaces are an admin/single-user feature, as before.

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

## Checking it works

`scripts/vault_eval.py` asks a running instance a list of questions and
reports, per question, whether the answer matched, how many tools and steps
it took and how long it ran. Use a browser session cookie (API tokens are
capped at the non-admin tool policy, which hides the file tools):

```bash
ODY_SESSION=<odysseus_session cookie> ./venv/bin/python scripts/vault_eval.py cases.yaml \
    --base-url http://localhost:7000 --endpoint-id <id> --model <model>
```

Run it per model to compare a cloud model against a local one.
