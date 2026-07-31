# Extensions

Extensions are optional features that live outside the core app — Ithaca (a
home-hub dashboard) and the RSS feed reader ship as extensions today. They can
be turned on or off, installed from a URL without editing any code, and
packaged up so you can host your own and share them between instances.

> Looking for the developer-facing manifest/API reference (how to *build* an
> extension)? See [`extensions/README.md`](../extensions/README.md) in the repo
> root. This page is about *using* the feature.

## The short version

- **Docker/Windows builds ship with zero extensions by default.** A fresh
  install has nothing to disable — Ithaca and RSS aren't even present in the
  image/bundle unless you opt in at build time.
- **Running from source** (`git clone` + `python setup.py`), the in-repo
  extensions (`extensions/ithaca/`, `extensions/rss/`) are present and enabled
  automatically — there's nothing to install for those two.
- Turn extensions on/off, install new ones, and remove installed ones from
  **Settings → Extensions** (admin-only).

## Enabling/disabling an extension

**Settings → Extensions** lists every extension the server can see, whether
enabled or not, with a toggle. Flipping it takes effect on the **next
restart** — there's no hot reload (Python can't cleanly un-import a module or
un-mount a FastAPI router), so the toggle just says so instead of pretending
otherwise.

You can also gate this via environment variables, useful for scripted
deployments:
```bash
ODYSSEUS_EXTENSIONS_DISABLED=rss        # force-disable, ignoring the saved setting
ODYSSEUS_EXTENSIONS_ENABLED=ithaca,rss  # force-enable
```

## Installing an extension from a URL

**Settings → Extensions → Install from URL.** Paste either:
- a git repository URL (`https://github.com/owner/repo.git`, or without the
  `.git` suffix — anything `git clone` accepts), or
- a direct link to a `.zip` archive (e.g. a GitHub "Download ZIP" link, or a
  packaged extension from the section below).

Click Install. The extension's own `extension.json` tells the server its id —
you don't type one in. It lands under a data directory that survives
container recreates and app updates, separate from the app's own code.
Restart to pick it up (same "no hot reload" caveat as above).

**This runs the extension's code with full server privileges the moment
it's enabled — file system, network, everything Odysseus itself can touch.
There is no sandboxing or review.** Only install extensions from a source you
trust. Public git repos over HTTPS work; there's no support for
password/SSH-key-gated private repos — an auth-required clone just fails with
a clear error instead of hanging.

### Installing at boot instead (Docker, headless deploys)

Set `ODYSSEUS_EXTENSIONS_AUTOINSTALL` before starting the container — a
comma-separated list of `id=url` pairs:
```bash
ODYSSEUS_EXTENSIONS_AUTOINSTALL=rss=https://github.com/you/odysseus-rss-fork.git docker compose up -d
```
or in `docker-compose.yml`'s `environment:` (or `.env`):
```
ODYSSEUS_EXTENSIONS_AUTOINSTALL=rss=https://example.com/rss-extension.zip
```
This installs once (skipped on later boots once it's already present — no
repeated network calls) and never blocks the app from starting if the URL is
unreachable or wrong; a failure is just logged.

## Uninstalling an extension

**Settings → Extensions → Uninstall**, admin-only. Only removes something you
*installed* — an extension that ships in the repo (Ithaca, RSS, when running
from source or a build with `ODYSSEUS_BUNDLE_EXTENSIONS=true`) can't be
uninstalled this way, since it isn't in the installed-extensions location to
begin with. Takes effect on next restart.

## Packaging an extension to host yourself

If you want to run your *own* copy of Ithaca or RSS somewhere else — a second
Odysseus instance, a friend's install, your homelab — you can turn any
extension already on a running instance into a downloadable zip, then point
another instance's "Install from URL" at that zip.

**From the running app** (admin-only):
```
GET /api/extensions/<id>/package.zip
```
e.g. open `http://your-odysseus-host:7000/api/extensions/rss/package.zip`
while logged in as an admin, or `curl` it with your session cookie. The
server builds the zip on the fly from whatever `<id>` currently is on disk
(works for both in-repo and previously-installed extensions).

**From the command line, no running server needed:**
```bash
python scripts/package_extension.py rss                # writes ./rss.zip
python scripts/package_extension.py ithaca /tmp/ithaca.zip
```
Useful for packaging as a CI artifact, or before manually attaching to a
GitHub release.

Either way you get a clean zip (no `__pycache__`, `.git`, or `.pyc` cruft)
that you can:
- attach to a GitHub release and share the release asset URL,
- drop on any static file host / your own web server / a NAS share, or
- just hand the file to someone and have them run their own local file server
  for it (`install_from_url` needs `http(s)://`, not a bare file path).

Then, on the *other* instance: Settings → Extensions → Install from URL →
paste wherever you put it.

## Building with extensions baked in (Docker / Windows)

Both packaged-build paths default to **no bundled extensions** — see the
[Setup Guide](setup.md#docker-recommended) for the exact `docker compose`
invocation. The short version: set `ODYSSEUS_BUNDLE_EXTENSIONS=true` at build
time (Docker build-arg, or the equivalent env var read by the PyInstaller
spec for the Windows build) to include the in-repo extensions (Ithaca, RSS) in
the image/bundle and have them enabled by default on first boot, instead of
starting from zero and installing them afterward.

This only affects what ships baked into the artifact — extensions installed
afterward via Settings or `ODYSSEUS_EXTENSIONS_AUTOINSTALL` work the same way
regardless of how the image was built.
