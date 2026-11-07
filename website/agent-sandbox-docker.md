# Agent sandbox inside Docker

The agent's `bash` and `python` tools run every command through
`src/containment.py`, which uses bubblewrap (`bwrap`) to give the command its
own PID and mount namespaces, a read-only `/usr` and `/etc`, a private `/tmp`,
and the workspace at `/workspace`. Containment is *enforcing*: when bubblewrap
cannot set that up, the tools refuse to run (`ContainmentUnavailable`) instead
of running unsandboxed.

The image installs `bubblewrap`. Docker's default container settings still
stop it from working. This page records what was tested (2026-11-07) and what
a deployment has to decide.

## What was tested

Probe: upstream's own `containment.acquire()` + `containment.run()` (and the
`_bwrap_available()` check) inside a `python:3.14.9-slim` container with
`bubblewrap` installed, as uid 1000 (the app drops to `PUID` 1000 via `gosu`).

| Container options | Result |
|---|---|
| Docker defaults (local Docker 29.9, seccomp) | `bwrap: No permissions to create a new namespace` → tools refuse |
| `seccomp=unconfined` | `bwrap: Can't mount proc on /proc` |
| `seccomp=unconfined` + `systempaths=unconfined` (no AppArmor host) | works: filesystem, process_tree, wall_clock enforced; network isolation also works |
| same, on latitude (Debian 12, kernel 6.1, Docker 29.9.3, AppArmor on) | `bwrap: Failed to make / slave: Permission denied` |
| latitude, + `apparmor=unconfined` | works |
| running as root in the container (no user namespace) | needs `CAP_SYS_ADMIN` + `seccomp=unconfined`; `--unshare-net` also needs `NET_ADMIN`. Not recommended |

No extra capabilities are needed on the unprivileged path; it works even with
`cap_drop: [ALL]`.

## What the odysseus service would need

```yaml
    security_opt:
      - seccomp=unconfined      # allow unshare(CLONE_NEWUSER) for the app user
      - systempaths=unconfined  # unmask /proc so bwrap can mount a fresh /proc
      - apparmor=unconfined     # docker-default AppArmor denies bwrap's mounts
```

These loosen the *outer* container (they apply to the whole Odysseus process,
not only to agent commands), in exchange for the agent sandbox working inside
it. Narrower alternatives, not yet built: a custom seccomp profile (Docker's
default plus `clone`/`unshare` with `CLONE_NEWUSER`) and a custom AppArmor
profile that allows bwrap's mounts.

## What the host needs

- Unprivileged user namespaces enabled: `kernel.unprivileged_userns_clone=1`
  and a non-zero `user.max_user_namespaces` (Debian 12 default; latitude has
  both).
- Nothing else for the unprivileged path. The app must keep running as a
  non-root user inside the container (the entrypoint's `gosu` drop does this).

## Without these options

The bash and python tools return `containment unavailable` and do nothing.
Every other agent tool (files, web, documents, email, Ithaca, RSS, ...) is
unaffected.
