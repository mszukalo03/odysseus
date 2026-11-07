import asyncio
import ast
import logging
import os
import re
import shlex
import secrets
import shutil
import subprocess
import sys
import time
import json
import collections
from typing import Awaitable, Callable, Dict, Optional, Tuple
from urllib.parse import urlparse, urlsplit, urlunsplit

import httpx

from src import containment
from src.constants import AGENT_ISOLATED_TMP_DIRNAME, MAX_OUTPUT_CHARS, WORKSPACE_MOUNT

logger = logging.getLogger(__name__)

# Agent shell calls must fail fast enough for the loop to recover and choose a
# better tool.  A one-hour default can pin an entire benchmark worker on an
# accidental recursive scan, even though ordinary artifact commands complete
# in seconds.  Long-running work belongs in manage_bg_jobs.
DEFAULT_BASH_TIMEOUT = 120
DEFAULT_PYTHON_TIMEOUT = 60 * 60

_HOST_SHELL_BRIDGE_HOSTS = {"127.0.0.1", "localhost", "::1", "host.docker.internal"}
IS_WINDOWS = sys.platform.startswith("win")
_HOST_SHELL_CANCEL_TASKS: set[asyncio.Task] = set()

if not IS_WINDOWS:
    # POSIX-only: the sudo-password pty retry (_run_via_pty) needs a real
    # pty/tty, which these modules don't exist for on Windows.
    import fcntl
    import pty
    import termios

# ── Fork: sudo-password prompt, pty retry, tmux persistence, progress
# streaming for the agent's bash tool (see BashTool below). ─────────────────
# A `sudo` invocation at the start of the command or right after a shell
# separator. Anchored this way so we don't rewrite the word "sudo" appearing
# inside a quoted string or a longer identifier.
_SUDO_CALL_RE = re.compile(r"(^|[\n;&|]\s*)sudo(?=\s)", re.MULTILINE)

# The user already told sudo how to behave (-S read stdin, -A askpass,
# -n non-interactive) — leave their flags alone.
_SUDO_SELF_HANDLED_RE = re.compile(r"\bsudo\s+(?:-\w+\s+)*-[SAn]\b")

def _mentions_sudo(command: str) -> bool:
    return bool(_SUDO_CALL_RE.search(command or ""))

def _sudo_is_self_handled(command: str) -> bool:
    return bool(_SUDO_SELF_HANDLED_RE.search(command or ""))

def _inject_sudo_stdin_flags(command: str) -> Tuple[str, int]:
    """Rewrite `sudo ...` -> `sudo -S -p '' ...` so it reads the password from
    stdin and doesn't emit a prompt string into stderr. Returns the rewritten
    command and how many invocations were rewritten."""
    count = len(_SUDO_CALL_RE.findall(command))
    rewritten = _SUDO_CALL_RE.sub(lambda m: f"{m.group(1)}sudo -S -p ''", command)
    return rewritten, count

# garuda-update re-execs itself under sudo and then, deeper still, under
# systemd-inhibit -- each hop can allocate its own fresh pty, so pacman's own
# "Proceed with installation? [Y/n]" prompt can end up on a pty our own
# _run_via_pty never sees or can reach. Rather than chase that nesting,
# use garuda-update's own flag: `--noconfirm` (see
# /usr/lib/garuda/garuda-update/main-update's getopt parsing) survives the
# re-exec via "$@" regardless of pty/env boundaries, and makes its internal
# `auto-pacman` expect script answer prompts itself.
_GARUDA_UPDATE_RE = re.compile(r"\bgaruda-update\b")

def _add_noconfirm_to_garuda_update(command: str) -> str:
    if "--noconfirm" in (command or ""):
        return command
    return _GARUDA_UPDATE_RE.sub("garuda-update --noconfirm", command, count=1)

async def _passwordless_sudo_available(env: Optional[dict], cwd: Optional[str]) -> bool:
    """True when sudo runs without a password (NOPASSWD rule or a live ticket),
    in which case we can skip the prompt entirely."""
    try:
        proc = await asyncio.create_subprocess_shell(
            "sudo -n true",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
            cwd=cwd,
        )
        return await asyncio.wait_for(proc.wait(), timeout=10) == 0
    except Exception:
        return False

# A wrapper script (garuda-update, some installers/AUR helpers) can call
# `sudo` internally without the word "sudo" ever appearing in the command we
# were given, so `_mentions_sudo` misses it. Recognize sudo's own "I have
# nowhere to read a password from" complaints after the fact instead.
_SUDO_NEEDS_TTY_RE = re.compile(
    r"a terminal is required to read the password"
    r"|sudo:\s*a password is required"
    r"|no askpass program specified"
    r"|sorry,\s*a password is required to run sudo",
    re.IGNORECASE,
)

def _looks_like_sudo_tty_failure(text: str) -> bool:
    return bool(_SUDO_NEEDS_TTY_RE.search(text or ""))

# A real pty (unlike a plain pipe) tells tools like pacman "you have a
# terminal", so they switch on ANSI color/cursor-movement codes and redraw
# progress bars in place via bare `\r`. Without an actual terminal emulator
# to interpret those, both the live progress tail and the final tool output
# the model sees would otherwise be full of raw `\x1b[...m` escape garbage.
_ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

def _clean_pty_output(text: str) -> str:
    text = _ANSI_RE.sub("", text)
    return text.replace("\r\n", "\n").replace("\r", "\n")

async def _run_via_pty(
    command: str,
    password: str,
    env: Optional[dict],
    cwd: Optional[str],
    timeout: float,
    progress_cb: Optional[Callable[[Dict], Awaitable[None]]] = None,
) -> Tuple[str, int, bool]:
    """Run `command` attached to a real pty instead of a pipe, so a `sudo`
    call buried inside it (one we can't see or rewrite, e.g. inside
    `garuda-update`) finds a controlling terminal and prompts on it like it
    would for a human, instead of refusing outright. We watch the pty output
    for a password-prompt-looking line and answer it once."""
    master_fd, slave_fd = pty.openpty()

    def _preexec():
        os.setsid()
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
            env=env, cwd=cwd,
            preexec_fn=_preexec,
        )
    finally:
        os.close(slave_fd)

    loop = asyncio.get_event_loop()
    chunks: list[bytes] = []
    password_sent = False
    started = time.time()

    def _read_chunk() -> bytes:
        try:
            return os.read(master_fd, 4096)
        except OSError:
            return b""

    async def _pump():
        nonlocal password_sent
        while True:
            chunk = await loop.run_in_executor(None, _read_chunk)
            if not chunk:
                break
            chunks.append(chunk)
            if not password_sent and re.search(rb"assword", chunk, re.IGNORECASE):
                try:
                    os.write(master_fd, (password + "\n").encode())
                except OSError:
                    pass
                password_sent = True

    async def _progress_emitter():
        await asyncio.sleep(PROGRESS_INTERVAL_S)
        while True:
            if progress_cb:
                try:
                    # Clean the whole buffer each time rather than the raw
                    # chunk stream: an escape sequence or \r-redraw can span
                    # a 4096-byte read boundary, so per-chunk cleaning can
                    # leave fragments behind.
                    cleaned = _clean_pty_output(b"".join(chunks).decode("utf-8", errors="replace"))
                    await progress_cb({
                        "elapsed_s": round(time.time() - started, 1),
                        "tail": cleaned[-2000:],
                    })
                except Exception:
                    pass
            await asyncio.sleep(PROGRESS_INTERVAL_S)

    pump_task = asyncio.create_task(_pump())
    prog_task = asyncio.create_task(_progress_emitter()) if progress_cb else None
    timed_out = False
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        timed_out = True
        try:
            proc.kill()
        except Exception:
            pass
    finally:
        if prog_task is not None:
            prog_task.cancel()
            try:
                await prog_task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            await asyncio.wait_for(pump_task, timeout=1)
        except Exception:
            pump_task.cancel()
        try:
            os.close(master_fd)
        except OSError:
            pass

    output = _clean_pty_output(b"".join(chunks).decode("utf-8", errors="replace"))
    return output, (proc.returncode or 0), timed_out

def _redact(text: str, secret: Optional[str]) -> str:
    """Belt-and-braces: `sudo -S -p ''` shouldn't echo the password anywhere,
    but never let one slip into output the model or transcript will see."""
    if not text or not secret:
        return text
    return text.replace(secret, "********")

PROGRESS_INTERVAL_S = 2.0

PROGRESS_TAIL_LINES = 12

TMUX_CAPTURE_LINES = 2000

def _tmux_session_name(session_id: Optional[str]) -> str:
    raw = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(session_id or "default")).strip("-")
    return f"ody-agent-{raw[:80] or 'default'}"

async def _run_exec(*args: str, timeout: float = 10) -> Tuple[str, str, int]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return "", "timeout", 124
    return (
        out_b.decode("utf-8", errors="replace"),
        err_b.decode("utf-8", errors="replace"),
        proc.returncode or 0,
    )

async def _tmux_has_session(name: str) -> bool:
    _, _, rc = await _run_exec("tmux", "has-session", "-t", name, timeout=3)
    return rc == 0

async def _tmux_capture(name: str) -> str:
    out, _, _ = await _run_exec(
        "tmux", "capture-pane", "-p", "-J", "-S", f"-{TMUX_CAPTURE_LINES}", "-t", name,
        timeout=5,
    )
    return out

async def _tmux_send_line(name: str, line: str) -> None:
    if line:
        await _run_exec("tmux", "send-keys", "-t", name, "-l", line, timeout=5)
    await _run_exec("tmux", "send-keys", "-t", name, "C-m", timeout=5)

async def _ensure_tmux_session(name: str, cwd: str, env: Optional[dict]) -> None:
    if await _tmux_has_session(name):
        await _run_exec("tmux", "send-keys", "-t", name, "stty -echo", "C-m", timeout=5)
        return
    await _run_exec(
        "tmux", "new-session", "-d", "-s", name, "-c", cwd,
        "env",
        f"TERM={env.get('TERM', 'xterm-256color') if env else 'xterm-256color'}",
        f"COLUMNS={env.get('COLUMNS', '120') if env else '120'}",
        f"LINES={env.get('LINES', '40') if env else '40'}",
        "/bin/bash",
        "--noprofile",
        "--norc",
        timeout=10,
    )
    if not await _tmux_has_session(name):
        raise RuntimeError(f"failed to create tmux session {name}")
    await _run_exec("tmux", "send-keys", "-t", name, "stty -echo", "C-m", timeout=5)

def _output_after_marker(capture: str, start_marker: str, end_marker: str) -> Tuple[str, bool]:
    lines = capture.splitlines()
    start_idx = -1
    for idx, line in enumerate(lines):
        if line.strip() == start_marker:
            start_idx = idx
    if start_idx < 0:
        return capture, False
    end_idx = -1
    for idx in range(start_idx + 1, len(lines)):
        if lines[idx].strip().startswith(end_marker):
            end_idx = idx
    if end_idx < 0:
        return "\n".join(lines[start_idx + 1:]), False
    return "\n".join(lines[start_idx + 1:end_idx]), True

def _extract_marker_rc(capture: str, end_marker: str) -> int:
    for line in reversed(capture.splitlines()):
        stripped = line.strip()
        if stripped.startswith(end_marker):
            suffix = stripped[len(end_marker):].strip()
            if suffix.isdigit():
                return int(suffix)
    return 0

async def _run_tmux_bash(
    content: str,
    *,
    session_id: str,
    cwd: str,
    env: Optional[dict],
    timeout: float,
    progress_cb: Optional[Callable[[Dict], Awaitable[None]]] = None,
) -> Tuple[str, str, Optional[int], bool]:
    name = _tmux_session_name(session_id)
    await _ensure_tmux_session(name, cwd, env)

    stamp = f"{int(time.time() * 1000)}-{abs(hash(content)) % 1000000}"
    start_marker = f"__ODYSSEUS_CMD_START_{stamp}__"
    end_prefix = f"__ODYSSEUS_CMD_END_{stamp}__:"
    wrapped = (
        f"printf '\\n{start_marker}\\n'\n"
        f"{content}\n"
        f"__ody_rc=$?\n"
        f"printf '\\n{end_prefix}%s\\n' \"$__ody_rc\"\n"
    )
    for line in wrapped.splitlines():
        await _tmux_send_line(name, line)

    started = time.time()
    last_tail = ""
    while True:
        capture = await _tmux_capture(name)
        body, done = _output_after_marker(capture, start_marker, end_prefix)
        tail = "\n".join(body.splitlines()[-PROGRESS_TAIL_LINES:])
        if progress_cb and tail != last_tail:
            last_tail = tail
            try:
                await progress_cb({
                    "elapsed_s": round(time.time() - started, 1),
                    "tail": tail,
                    "tmux_session": name,
                })
            except Exception:
                pass
        if done:
            rc = _extract_marker_rc(capture, end_prefix)
            cleaned = _clean_tmux_command_output(body, wrapped)
            return cleaned, "", rc, False
        if time.time() - started > timeout:
            try:
                await _run_exec("tmux", "send-keys", "-t", name, "C-c", timeout=3)
            except Exception:
                pass
            cleaned = _clean_tmux_command_output(body, wrapped)
            return cleaned, "", 124, True
        await asyncio.sleep(0.5)

def _clean_tmux_command_output(text: str, wrapped_command: str) -> str:
    lines = text.splitlines()
    wrapped_lines = {ln.rstrip() for ln in wrapped_command.splitlines() if ln.strip()}
    cleaned = []
    for line in lines:
        raw = line.rstrip()
        stripped = raw.strip()
        if not stripped:
            cleaned.append(raw)
            continue
        if stripped in wrapped_lines:
            continue
        if stripped.startswith("__ody_rc=") or stripped.startswith("printf "):
            continue
        if re.fullmatch(r"(?:bash|sh)-[\d.]+\$ ?", stripped):
            continue
        if re.fullmatch(r"[\w.@:/~+-]+[#$] ?", stripped):
            continue
        cleaned.append(raw)
    return "\n".join(cleaned).strip()

async def _run_subprocess_streaming(
    proc: asyncio.subprocess.Process,
    *,
    timeout: float,
    progress_cb: Optional[Callable[[Dict], Awaitable[None]]] = None,
) -> Tuple[str, str, Optional[int], bool]:
    started = time.time()
    stdout_full: list[str] = []
    stderr_full: list[str] = []
    tail = collections.deque(maxlen=PROGRESS_TAIL_LINES)

    async def _reader(stream, full_buf, label: str):
        if stream is None:
            return
        while True:
            line = await stream.readline()
            if not line:
                break
            decoded = line.decode("utf-8", errors="replace").rstrip("\n")
            full_buf.append(decoded)
            if label == "err":
                tail.append(f"! {decoded}")
            else:
                tail.append(decoded)

    async def _progress_emitter():
        await asyncio.sleep(PROGRESS_INTERVAL_S)
        while True:
            if progress_cb:
                try:
                    await progress_cb({
                        "elapsed_s": round(time.time() - started, 1),
                        "tail": "\n".join(list(tail)),
                    })
                except Exception:
                    pass
            await asyncio.sleep(PROGRESS_INTERVAL_S)

    rd_out = asyncio.create_task(_reader(proc.stdout, stdout_full, "out"))
    rd_err = asyncio.create_task(_reader(proc.stderr, stderr_full, "err"))
    prog_task = asyncio.create_task(_progress_emitter()) if progress_cb else None

    timed_out = False
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        timed_out = True
        try:
            proc.kill()
        except Exception:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=2)
        except Exception:
            pass
    except asyncio.CancelledError:
        try:
            proc.kill()
        except Exception:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=2)
        except Exception:
            pass
        for t in (rd_out, rd_err):
            t.cancel()
        if prog_task is not None:
            prog_task.cancel()
        raise
    finally:
        if prog_task is not None and not prog_task.done():
            prog_task.cancel()
            try:
                await prog_task
            except (asyncio.CancelledError, Exception):
                pass
        for t in (rd_out, rd_err):
            try:
                await asyncio.wait_for(t, timeout=1)
            except Exception:
                pass

    return (
        "\n".join(stdout_full),
        "\n".join(stderr_full),
        proc.returncode,
        timed_out,
    )



def _ffmpeg_unicode_drawtext_needs_fontfile(command: str) -> bool:
    """Require a deliberate font for non-ASCII text rendered by ffmpeg.

    Fontconfig's fallback is platform-dependent and commonly resolves to a
    font without the requested glyphs.  An explicit ``fontfile`` makes the
    rendered artifact portable and prevents successful commands that produce
    tofu boxes instead of text.
    """
    text = str(command or "")
    lowered = text.lower()
    return (
        bool(re.search(r"\bffmpeg\b", lowered))
        and "drawtext" in lowered
        and "fontfile" not in lowered
        and any(ord(char) > 127 for char in text)
    )


def _resolve_fontfile_for_text(text: str) -> str:
    """Resolve a host font covering the first requested non-ASCII codepoint."""
    codepoint = next((ord(char) for char in str(text or "") if ord(char) > 127), None)
    matcher = shutil.which("fc-match")
    if codepoint is None or not matcher:
        return ""
    try:
        completed = subprocess.run(
            [matcher, "-f", "%{file}", f":charset={codepoint:04x}"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    candidate = str(completed.stdout or "").strip().splitlines()[0:1]
    if completed.returncode != 0 or not candidate:
        return ""
    path = candidate[0].strip()
    return path if os.path.isfile(path) else ""


async def _cancel_host_shell_bridge_request(
    url: str, token: str, request_id: str,
) -> None:
    if not token or not is_host_shell_bridge_url_allowed(url):
        return
    target = host_shell_bridge_endpoint_url(url, "/cancel")
    try:
        timeout = httpx.Timeout(5.0, connect=2.0, write=2.0, pool=2.0)
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            await client.post(
                target,
                json={"request_id": request_id},
                headers={"X-Odysseus-TUI-Bridge-Token": token},
            )
    except Exception:
        pass


def find_bash() -> Optional[str]:
    """Find a real Bash executable for native Windows agent runs."""
    candidates = [
        shutil.which("bash"),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
    ]
    return next((path for path in candidates if path and os.path.isfile(path)), None)


async def _create_bash_subprocess(command: str, **kwargs):
    """Start the agent shell with Bash semantics on every supported OS.

    ``asyncio.create_subprocess_shell`` delegates to ``cmd.exe`` on native
    Windows.  That contradicts the Bash tool contract and makes POSIX commands
    such as ``pwd``, ``ls -la``, and ``cat`` unreliable even when the launcher
    has found Git Bash.  Pass the selected workspace as a structural ``cwd``
    argument; Git Bash inherits that native Windows directory and exposes it
    using its normal ``/c/...`` representation.
    """
    if IS_WINDOWS:
        bash = find_bash()
        if not bash:
            raise RuntimeError(
                "Git Bash is required for the Bash tool on Windows; "
                "install Git for Windows and restart Odysseus"
            )
        return await asyncio.create_subprocess_exec(bash, "-c", command, **kwargs)
    return await asyncio.create_subprocess_shell(command, **kwargs)


def _host_shell_requires_detach(command: str) -> bool:
    """Recognize commands that must not block an interactive agent turn.

    Models occasionally omit ``detach`` even after the host-shell contract
    tells them to poll long jobs. Keep the normal synchronous path for short
    commands, but make explicit background markers and clearly long sleeps
    deterministic so the bridge returns a job id instead of holding the SSE
    stream open.
    """
    text = str(command or "").strip()
    if not text:
        return False
    first = next((line.strip().lower() for line in text.splitlines() if line.strip()), "")
    if first in {"#!bg", "#bg", "# bg", "#background", "# background", "@background", "# @background"}:
        return True
    match = re.search(r"\bsleep\s+(\d+(?:\.\d+)?)\b", text, re.IGNORECASE)
    if match:
        try:
            return float(match.group(1)) >= 20
        except ValueError:
            return False
    return False


def _host_shell_should_auto_poll(command: str) -> bool:
    """Poll implicit long-sleep jobs so a false completion cannot escape."""
    text = str(command or "").lower()
    if not _host_shell_requires_detach(command):
        return False
    return not any(
        marker in text
        for marker in ("#!bg", "#bg", "# bg", "#background", "# background", "@background")
    )


def _docker_default_gateway_ips() -> set[str]:
    gateways: set[str] = set()
    try:
        with open("/proc/net/route", "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh.readlines()[1:]:
                parts = line.split()
                if len(parts) < 3 or parts[1] != "00000000":
                    continue
                raw = parts[2]
                if len(raw) != 8:
                    continue
                octets = [str(int(raw[i:i + 2], 16)) for i in range(6, -1, -2)]
                gateways.add(".".join(octets))
    except Exception:
        return set()
    return gateways


def _is_private_bridge_ip(host: str) -> bool:
    """LAN + CGNAT/Tailscale (100.64.0.0/10) literal IPs — the ranges a remote
    TUI legitimately advertises when the backend is reachable over the LAN or
    Tailscale. The 172.16/12 docker-private range is deliberately EXCLUDED:
    on a container host those addresses are neighboring containers, not the
    TUI — only the actual default gateway (checked separately) is trusted."""
    parts = host.split(".")
    if len(parts) != 4 or not all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
        return False
    a, b = int(parts[0]), int(parts[1])
    if a == 10:
        return True
    if a == 192 and b == 168:
        return True
    if a == 100 and 64 <= b <= 127:
        return True
    return False


def is_host_shell_bridge_url_allowed(url: str) -> bool:
    parsed = urlparse(str(url or "").strip())
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if parsed.scheme != "http" or not parsed.netloc or parsed.username or parsed.password:
        return False
    if (
        host not in _HOST_SHELL_BRIDGE_HOSTS
        and host not in _docker_default_gateway_ips()
        and not _is_private_bridge_ip(host)
    ):
        return False
    if parsed.path not in ("", "/run"):
        return False
    if parsed.query or parsed.fragment:
        return False
    return True


def host_shell_bridge_endpoint_url(url: str, path: str) -> str:
    """Replace a validated bridge URL's path without changing its authority."""
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _replace_workspace_alias(content: str, cwd: str) -> str:
    """Map virtual /workspace paths without corrupting absolute host paths."""
    return re.sub(
        r"(^|[\s'\"=:(\[,])/workspace(?=$|[/\s'\"`),;\]])",
        lambda match: match.group(1) + cwd,
        str(content or ""),
    )


#: Roots the namespace argv mounts itself. A host path under one of these is
#: already reachable inside the namespace, so it needs no bind and must not get
#: a ``--dir`` chain: mkdir inside a read-only bind fails and takes the whole
#: namespace with it.
_NAMESPACE_MOUNTED_ROOTS = ("/usr", "/home", "/mnt")

#: Destinations a bind must never overlay. Replacing the private root, the
#: private /tmp or the workspace mount with a host directory undoes the
#: namespace from inside the argv that builds it.
_NAMESPACE_RESERVED_DESTS = frozenset({
    "/", "/tmp", "/var", "/opt", "/etc", WORKSPACE_MOUNT,
    "/root", "/run", "/proc", "/dev", "/sys", *_NAMESPACE_MOUNTED_ROOTS,
})


def _namespace_visible_without_bind(path: str) -> bool:
    """True when ``path`` is already reachable through a root the argv mounts."""
    return any(
        path == root or path.startswith(root + os.sep)
        for root in _NAMESPACE_MOUNTED_ROOTS
    )


def _namespace_dir_chain(path: str) -> list[str]:
    """``--dir`` args for every ancestor of ``path`` the argv has to create.

    bwrap mounts into a tmpfs root, so a bind destination's parents have to
    exist before the bind. Returns nothing when the parents already exist by
    virtue of a mount the argv made — creating a directory inside a read-only
    bind is an error, not a no-op.
    """
    if _namespace_visible_without_bind(path):
        return []
    parents: list[str] = []
    parent = os.path.dirname(path)
    while parent not in ("/", "", "/tmp", "/etc", WORKSPACE_MOUNT, *_NAMESPACE_MOUNTED_ROOTS):
        parents.append(parent)
        parent = os.path.dirname(parent)
    args: list[str] = []
    for directory in reversed(parents):
        args.extend(("--dir", directory))
    return args


def _isolated_tmp_dir(cwd: str) -> str:
    """The workspace-local stand-in for the host ``/tmp``.

    Creation is best-effort: the source tree is read-only in Docker and a
    workspace can be mounted read-only, and a command that mentions ``/tmp/``
    must not die with an OSError traceback because a scratch directory could
    not be made. The rewrite still points at the workspace, so a command that
    really needs to write there fails on its own terms, inside the boundary,
    with its own error message.
    """
    path = os.path.join(cwd, AGENT_ISOLATED_TMP_DIRNAME)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path


def _execution_boundary(
    cwd: str, *, wall_clock_s: int = DEFAULT_BASH_TIMEOUT,
) -> "containment.ContainmentProbe":
    """What this host can actually enforce for an agent command in ``cwd``.

    The single place the shell and Python tools ask. Both used to decide for
    themselves, by testing whether a namespace wrapper came back non-None, and
    both then fell through to a regex if it had not — so "was that command
    confined" had no answer and no field in the result. Routing the question
    through :mod:`src.containment` means one mechanism table, one answer, and a
    ``containment`` block in the tool result either way.

    ``network`` is left inherited on purpose: ``--unshare-net`` was measured to
    cut the loopback sidecars this product depends on (ChromaDB on 8100), and
    the Dockerfile installs ``nmap``/``iproute2``/``dnsutils`` because
    Docker-hosted agents are expected to do LAN work. It is a reported
    dimension here, not an enforced one.
    """
    try:
        return containment.probe(
            containment.agent_spec(
                workspace=cwd,
                env={},
                wall_clock_s=wall_clock_s,
                max_output_bytes=MAX_OUTPUT_CHARS,
            )
        )
    except ValueError as exc:
        # A workspace that is not a usable directory is a caller bug to
        # containment, which raises rather than reporting. Here it must not
        # take out the tool, and it is still a containment failure: nothing can
        # be confined to a directory that is not there. Fail closed. The reason
        # goes in the message rather than a traceback -- this is a known shape,
        # not an unexpected exception.
        logger.warning(
            "execution boundary: cannot probe containment for workspace %r (%s); "
            "treating every required dimension as unenforced",
            cwd, exc,
        )
        return containment.ContainmentProbe(
            mechanism="none",
            enforced=frozenset(),
            degraded=(),
            unenforced_required=tuple(sorted(containment.DEFAULT_REQUIRED)),
            mode=containment.CONTAINMENT_MODE,
        )


#: What the fallback actually is, named so it cannot be mistaken for a
#: mechanism. ``_replace_workspace_alias`` rewrites the literal token
#: ``/workspace`` to the real path in the command string; a command that never
#: mentions ``/workspace`` is untouched by it and runs on the host unrestricted.
ALIAS_REWRITE_MECHANISM = "workspace_alias_rewrite"

#: Guards the one-per-process fallback warning below. Module state, because the
#: fact it reports is a property of the host rather than of a command.
_ALIAS_FALLBACK_LOGGED = False


def _filesystem_boundary_block(mechanism: str, mode: str, *, confined: bool) -> dict:
    """The ``containment`` block for a spawn these tools still build themselves.

    Reports the **filesystem dimension only**, deliberately. The probe knows
    this host could also give a process group and a real wall clock, but
    Compatibility namespace previews assemble their own ``create_subprocess_*``
    call and pass neither ``start_new_session`` nor a group-wide kill, so
    listing those dimensions here would be the false claim
    :mod:`src.containment` calls worse than an honest absence. They arrive when
    this spawn path moves onto :func:`containment.run`, not before.
    """
    return {
        "mechanism": mechanism,
        "mode": mode,
        "enforced": [containment.FILESYSTEM] if confined else [],
        "unenforced_required": [] if confined else [containment.FILESYSTEM],
        "contained": confined,
        "executed": True,
        # Names the scope of the claim, so "process_tree is absent from
        # enforced" reads as "not reported here" rather than "not enforced".
        "reported_dimensions": [containment.FILESYSTEM],
    }


def _contained_command(
    content: str,
    cwd: str,
    *,
    chdir: str = WORKSPACE_MOUNT,
    interpreter_prefix: str | None = None,
) -> tuple[str, dict, bool]:
    """Resolve ``content`` into the strongest form this host can run.

    Returns ``(command, containment_block, confined)``. The caller spawns
    ``command``, copies ``containment_block`` into its result verbatim, and
    refuses instead when ``confined`` is false under enforcing mode.

    This replaces ``namespaced or _replace_workspace_alias(...)``, the line this
    ticket exists to delete. The two branches it chose between are not
    comparable — one is a mount namespace, the other is a regex — and choosing
    the second silently means an uncontained host execution reads in the
    transcript exactly like a contained one. The fallback still happens under
    an explicit report-only diagnostic mode; the difference is that it is now
    recorded in the result.

    :raises containment.ContainmentUnavailable: filesystem containment could
        not be established and the mode is enforcing. The command is not run.
    """
    probe = _execution_boundary(cwd)
    wrapped = _wrap_workspace_namespace(
        content, cwd, chdir=chdir, interpreter_prefix=interpreter_prefix,
    )
    # The probe's filesystem answer and the wrapper's None/not-None answer rest
    # on the same functional namespace probe, so they agree
    # by construction. `wrapped` is still what decides, because it is what
    # actually runs: a probe that said yes to a wrapper that declined would be
    # the same false claim in the other direction.
    if wrapped is not None:
        return wrapped, _filesystem_boundary_block(
            probe.mechanism, probe.mode, confined=True,
        ), True
    if probe.mode == containment.MODE_ENFORCING:
        raise containment.ContainmentUnavailable(
            frozenset({containment.FILESYSTEM}), ALIAS_REWRITE_MECHANISM,
        )
    # Once per process, not once per command. The host's ability to establish a
    # namespace does not change between calls, so a per-call warning would
    # drown the log on every macOS install while adding nothing — and the
    # per-call fact is already in the result block, which is where a reader
    # looking at one command will look.
    global _ALIAS_FALLBACK_LOGGED
    if not _ALIAS_FALLBACK_LOGGED:
        _ALIAS_FALLBACK_LOGGED = True
        logger.warning(
            "execution boundary: no filesystem containment is available on this "
            "host (mechanism %r); agent commands fall back to the %s, which is "
            "a path rewrite and not a boundary. Reported per command in the "
            "result's containment block.",
            probe.mechanism, ALIAS_REWRITE_MECHANISM,
        )
    return (
        _replace_workspace_alias(content, cwd),
        _filesystem_boundary_block(
            ALIAS_REWRITE_MECHANISM, probe.mode, confined=False,
        ),
        False,
    )


def _wrap_workspace_namespace(
    content: str,
    cwd: str,
    *,
    chdir: str = WORKSPACE_MOUNT,
    interpreter_prefix: str | None = None,
) -> str | None:
    """Run a shell command with the active workspace mounted at /workspace.

    Rewriting the command line alone is insufficient when a generated Python
    script itself contains paths such as ``/workspace/chart.png``.  A small
    bubblewrap namespace preserves that public contract for each concurrent
    agent without creating a process-global /workspace symlink.
    """
    if IS_WINDOWS or not containment._bwrap_available():
        return None
    readonly = [path for path in ("/home", "/mnt") if os.path.isdir(path)]
    # setup-python installs interpreters under /opt, and local CI virtualenvs
    # can live under /tmp. Those paths are hidden by the private root/tmpfs.
    # Expose only the active interpreter environment, read-only, so Python
    # tools keep their installed packages without exposing the host /tmp.
    if interpreter_prefix:
        prefix = os.path.abspath(interpreter_prefix)
        resolved_prefix = os.path.realpath(prefix)
        already_visible = _namespace_visible_without_bind(prefix)
        # A prefix is trusted only when it names a specific interpreter tree.
        # In particular, never overlay the private root, tmpfs, or workspace
        # with a broad host directory. Reject symlinked prefixes too: bwrap
        # would otherwise bind the resolved source at a different destination.
        has_environment_layout = (
            os.path.isfile(os.path.join(prefix, "pyvenv.cfg"))
            or (
                os.path.isfile(os.path.join(prefix, "bin", "python"))
                and os.path.isdir(os.path.join(
                    prefix, "lib", f"python{sys.version_info.major}.{sys.version_info.minor}",
                ))
            )
        )
        if (
            not already_visible
            and prefix == resolved_prefix
            and prefix not in _NAMESPACE_RESERVED_DESTS
            and len(prefix.split(os.sep)) >= 3
            and os.path.isdir(prefix)
            and has_environment_layout
        ):
            readonly.append(prefix)
    spec = containment.ContainmentSpec(
        workspace=cwd, env={}, wall_clock_s=DEFAULT_BASH_TIMEOUT,
        readonly_extra=tuple(readonly),
    )
    args = containment._bwrap_prefix(spec)
    args[-1] = chdir
    args.extend(("/bin/bash", "-lc", content))
    return shlex.join(args)


def _owned_spec(cwd: str, env: Optional[dict], timeout: int, readonly_extra: tuple = ()) -> containment.ContainmentSpec:
    """Server-defined boundary shared by the native execution tools."""
    readonly = []
    for prefix in (sys.prefix, sys.base_prefix):
        prefix = os.path.realpath(prefix)
        visible = any(prefix == root or prefix.startswith(root + os.sep) for root in ("/usr", "/etc"))
        if not visible and prefix not in _NAMESPACE_RESERVED_DESTS:
            readonly.append(prefix)
    from src.tool_execution import _agent_subprocess_env
    clean_env = _agent_subprocess_env() if env is None else dict(env)
    return containment.agent_spec(
        cwd, clean_env, timeout,
        readonly_extra=tuple(dict.fromkeys([*readonly, *readonly_extra])),
    )


async def _run_owned_command(command, ctx: dict, *, tool: str, timeout: int, argv: bool = False,
                             readonly_extra: tuple = ()) -> dict:
    from src.tool_execution import agent_cwd, _truncate

    grant = None
    launch = None
    result = None
    try:
        from src.agent_runtime.process_resources import require_launch, publish_launch, validate_launch_spec
        from src.agent_runtime.authority import active_request_authority
        launch = require_launch(tool, cwd=agent_cwd())
        authority = active_request_authority()
        if (str(ctx.get("owner") or "").strip().casefold(), str(ctx.get("session_id") or "")) != (
                authority.owner, authority.session_id):
            raise ValueError("Native producer owner or session changed")
        spec = _owned_spec(agent_cwd(), ctx.get("subproc_env"), timeout, readonly_extra)
        validate_launch_spec(launch, spec)
        grant = containment.acquire(
            spec,
            owner=str(ctx.get("session_id") or ctx.get("owner") or tool),
        )
        containment._update_record(grant.id, launch_generation=launch.generation)
        publish_launch(launch, authority, grant.id)
        if containment.FILESYSTEM not in grant.enforced:
            if argv:
                command = [*command[:-1], _replace_workspace_alias(command[-1], grant.workspace)]
            else:
                command = _replace_workspace_alias(command, grant.workspace)
        result = await containment.run(grant, command, argv=argv, progress_cb=ctx.get("progress_cb"))
        from src.agent_runtime.process_resources import attach_containment_processes
        attach_containment_processes(launch, grant.id)
    except containment.ContainmentUnavailable as exc:
        return containment.unavailable_tool_result(exc, tool=tool)
    except (OSError, RuntimeError, ValueError) as exc:
        if grant is not None:
            record = containment._load_records().get(grant.id, {})
            if not record.get("pid") and not record.get("release"):
                containment.release(grant, grace_s=0)
        boundary = result.grant.to_dict() if result is not None else grant.to_dict() if grant else {}
        boundary["executed"] = result is not None or bool(getattr(exc, "containment_executed", False))
        if result is None and not getattr(exc, "containment_established", False):
            boundary.update(contained=False, enforced=[])
        return {"error": f"{tool}: execution failed: {exc}", "exit_code": 1,
                "containment": boundary,
                **({"failure_kind": "resource_linkage_unavailable",
                    "teardown": result.release.to_dict() if result.release else {"dead": False}}
                   if result is not None else {})}
    finally:
        if launch is not None and grant is not None:
            record = containment._load_records().get(grant.id, {})
            if (record.get("launch_generation") == launch.generation
                    and (record.get("release") or {}).get("dead") is True):
                from src.agent_runtime.process_resources import retire_launch
                try:
                    retire_launch(launch, grant.id)
                except (OSError, ValueError, TypeError):
                    logger.warning("Foreground launch publication retirement failed", exc_info=True)

    boundary = result.grant.to_dict()
    boundary["executed"] = True
    teardown = result.release.to_dict() if result.release else {"dead": False}
    output = result.stdout.rstrip()
    if result.stderr.rstrip():
        output = (output + "\nSTDERR: " + result.stderr.rstrip()).strip()
    truncated = result.output_truncated or len(output) > MAX_OUTPUT_CHARS
    capture_note = " Captured output was truncated." if truncated else ""
    common = {"containment": boundary, "teardown": teardown, "output_truncated": truncated}
    if not teardown["dead"]:
        return {**common, "error": f"{tool}: process teardown could not verify death.{capture_note}",
                "failure_kind": "process_teardown_failed", "exit_code": 1,
                "stdout": _truncate(result.stdout, MAX_OUTPUT_CHARS),
                "stderr": _truncate(result.stderr, MAX_OUTPUT_CHARS)}
    if result.timed_out:
        return {**common, "error": f"{tool}: timed out after {timeout}s; process tree terminated.{capture_note}",
                "exit_code": 124, "timed_out": True, "stdout": _truncate(result.stdout, MAX_OUTPUT_CHARS),
                "stderr": _truncate(result.stderr, MAX_OUTPUT_CHARS)}
    if tool == "python":
        child_failure = _python_child_runtime_failure(result.stdout, result.stderr, result.exit_code)
        if child_failure:
            return {**common, "error": _truncate("python: a child operation failed despite a zero Python exit status:\n" + child_failure, MAX_OUTPUT_CHARS),
                    "exit_code": 1, "stderr": _truncate(result.stderr, MAX_OUTPUT_CHARS)}
    if truncated:
        note = "\n…[output truncated by containment capture limit]…"
        output = output[:MAX_OUTPUT_CHARS - len(note)] + note
    return {**common, "output": _truncate(output, MAX_OUTPUT_CHARS) or "(no output)",
            "exit_code": result.exit_code if result.exit_code is not None else 1}


class BashTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        from src.tool_execution import agent_cwd, _truncate
        from src import sudo_auth
        if isinstance(content, dict):
            content = str(content.get("command") or content.get("cmd") or content.get("code") or "")
        content = str(content or "").strip()
        if not content:
            return {
                "error": "bash: command is required; no command was executed",
                "exit_code": 1,
            }
        # Fork: upstream routes bash through src.containment (bubblewrap) and
        # refuses sudo. This fork's agent shell runs uncontained in the real
        # HOME with the browser sudo-password prompt below, so that path is
        # kept here. Upstream's ffmpeg font guard still applies.
        if _ffmpeg_unicode_drawtext_needs_fontfile(content):
            resolved_font = _resolve_fontfile_for_text(content)
            resolved_hint = (
                f" Host fontconfig resolved a covering font at `{resolved_font}`; "
                f"pass `fontfile={resolved_font}`."
                if resolved_font
                else ""
            )
            return {
                "error": (
                    "bash: ffmpeg drawtext with non-ASCII text requires an explicit "
                    "fontfile to avoid missing-glyph boxes."
                    + resolved_hint
                    + " If needed, resolve another suitable installed font with "
                    "`fc-match -f '%{file}' ':charset=<hex-codepoint>'`, then pass that "
                    "path as `drawtext=fontfile=...` and rerun the command."
                ),
                "exit_code": 1,
            }
        progress_cb = ctx.get("progress_cb")
        _subproc_env = ctx.get("subproc_env")
        owner = ctx.get("owner")
        session_id = ctx.get("session_id")
        cwd = agent_cwd()

        # tmux is a POSIX persistence path. A stray MSYS/Cygwin tmux.exe on
        # native Windows must not bypass the Git Bash launcher below: the tmux
        # setup hard-codes /bin/bash and cannot safely consume a native cwd.
        # It also bypasses the sudo-password handling below — a tmux session
        # is a real persistent shell, so a `sudo` typed into it prompts in
        # the pane itself rather than through this one-shot flow.
        if session_id and not IS_WINDOWS and shutil.which("tmux"):
            stdout, stderr, rc, timed_out = await _run_tmux_bash(
                content,
                session_id=str(session_id),
                cwd=cwd,
                env=_subproc_env,
                timeout=DEFAULT_BASH_TIMEOUT,
                progress_cb=progress_cb,
            )
            if timed_out:
                return {
                    "error": f"bash: timed out after {DEFAULT_BASH_TIMEOUT}s — sent Ctrl-C to tmux session",
                    "exit_code": 124,
                    "stdout": _truncate(stdout, MAX_OUTPUT_CHARS),
                    "stderr": _truncate(stderr, MAX_OUTPUT_CHARS),
                    "tmux_session": _tmux_session_name(str(session_id)),
                }
            output = stdout.rstrip()
            err = stderr.rstrip()
            if err:
                output = (output + "\nSTDERR: " + err).strip() if output else "STDERR: " + err
            return {
                "output": _truncate(output, MAX_OUTPUT_CHARS) or "(no output)",
                "exit_code": rc or 0,
                "tmux_session": _tmux_session_name(str(session_id)),
            }

        command = _add_noconfirm_to_garuda_update(content)
        stdin_payload: Optional[bytes] = None
        password: Optional[str] = None

        # There's no TTY here, so an unattended `sudo` can only ever fail.
        # Ask the browser for the password and feed it in over stdin instead.
        # sudo isn't a Windows concept, so this whole detour is POSIX-only.
        if not IS_WINDOWS and _mentions_sudo(command) and not _sudo_is_self_handled(command):
            if not await _passwordless_sudo_available(_subproc_env, cwd):
                password = sudo_auth.get_cached(owner)
                if not password and progress_cb:
                    password = await sudo_auth.request_password(
                        owner, command, progress_cb,
                    )
                if not password:
                    return {
                        "output": (
                            "sudo password was not provided (prompt cancelled or timed out), "
                            "so this command was not run. Do NOT retry it blindly — either ask "
                            "the user to approve the prompt, or find a way to do this without root."
                        ),
                        "exit_code": 1,
                    }
                command, sudo_count = _inject_sudo_stdin_flags(command)
                # One password line per invocation: stdin is consumed by the
                # first sudo, so a chained second one needs its own.
                stdin_payload = ((password + "\n") * max(1, sudo_count)).encode()

        try:
            proc = await _create_bash_subprocess(
                command,
                stdin=asyncio.subprocess.PIPE if stdin_payload is not None else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_subproc_env,
                cwd=cwd,
            )
        except RuntimeError as e:
            return {"error": f"bash: {e}", "exit_code": 1}
        if stdin_payload is not None and proc.stdin is not None:
            try:
                proc.stdin.write(stdin_payload)
                await proc.stdin.drain()
            except Exception:
                pass
            try:
                proc.stdin.close()
            except Exception:
                pass

        stdout, stderr, rc, timed_out = await _run_subprocess_streaming(
            proc,
            timeout=DEFAULT_BASH_TIMEOUT,
            progress_cb=progress_cb,
        )
        stdout = _redact(stdout, password)
        stderr = _redact(stderr, password)
        if timed_out:
            return {"error": f"bash: timed out after {DEFAULT_BASH_TIMEOUT}s — process killed", "exit_code": 124, "stdout": _truncate(stdout, MAX_OUTPUT_CHARS), "stderr": _truncate(stderr, MAX_OUTPUT_CHARS)}
        output = stdout.rstrip()
        err = stderr.rstrip()
        if err:
            output = (output + "\nSTDERR: " + err).strip() if output else "STDERR: " + err

        # We didn't spot `sudo` in the command text, so we ran it plain — but
        # it turned out to be a wrapper (e.g. `garuda-update`) that calls sudo
        # internally and just failed for lack of a terminal. Get a password
        # and retry the same command attached to a real pty this time, so
        # whatever `sudo` call is buried inside it can prompt on that tty.
        # POSIX-only, same as the detection above.
        if not IS_WINDOWS and rc != 0 and password is None and not timed_out and _looks_like_sudo_tty_failure(output):
            retry_password = sudo_auth.get_cached(owner)
            if not retry_password and progress_cb:
                retry_password = await sudo_auth.request_password(owner, command, progress_cb)
            if not retry_password:
                return {
                    "output": (
                        "This command needs a sudo password internally (e.g. a wrapper like "
                        "garuda-update), but the prompt was cancelled or timed out, so it was "
                        "not run. Do NOT retry it blindly — ask the user to approve the prompt."
                    ),
                    "exit_code": 1,
                }
            pty_output, pty_rc, pty_timed_out = await _run_via_pty(
                command, retry_password, _subproc_env, cwd, DEFAULT_BASH_TIMEOUT, progress_cb,
            )
            pty_output = _redact(pty_output, retry_password).rstrip()
            if pty_timed_out:
                return {"error": f"bash: timed out after {DEFAULT_BASH_TIMEOUT}s — process killed", "exit_code": 124, "stdout": _truncate(pty_output, MAX_OUTPUT_CHARS), "stderr": ""}
            if pty_rc != 0 and "try again" in pty_output.lower():
                sudo_auth.clear(owner)
            return {"output": _truncate(pty_output, MAX_OUTPUT_CHARS) or "(no output)", "exit_code": pty_rc}

        # A wrong password burns the cache — otherwise every later command in
        # the turn silently retries the same bad one.
        if rc != 0 and password and "try again" in err.lower():
            sudo_auth.clear(owner)
        output = _truncate(output, MAX_OUTPUT_CHARS)
        return {"output": output or "(no output)", "exit_code": rc or 0}

class HostShellTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        from src.tool_execution import _truncate

        try:
            args = json.loads(content) if str(content or "").strip().startswith("{") else {}
        except Exception:
            args = {}
        command = str(
            args.get("command")
            or args.get("cmd")
            or (content if not args else "")
            or ""
        ).strip()
        runtime = ctx.get("client_runtime_context")
        if not isinstance(runtime, dict):
            return {"error": "host_shell: no TUI host bridge advertised", "exit_code": 1}
        bridge = runtime.get("host_shell_bridge") or runtime.get("hostShellBridge")
        if not isinstance(bridge, dict):
            return {"error": "host_shell: no TUI host bridge advertised", "exit_code": 1}

        url = str(bridge.get("url") or "").strip()
        token = str(bridge.get("token") or "").strip()
        parsed = urlparse(url)
        if not is_host_shell_bridge_url_allowed(url):
            return {"error": "host_shell: invalid bridge URL", "exit_code": 1}
        if not token:
            return {"error": "host_shell: bridge token missing", "exit_code": 1}
        run_url = host_shell_bridge_endpoint_url(url, "/run")

        job_id = str(args.get("job_id") or "").strip()
        if not command and not job_id:
            return {"error": "host_shell: command or job_id required", "exit_code": 1}

        try:
            requested_timeout = int(args.get("timeout") or 30)
        except Exception:
            requested_timeout = 30
        timeout = max(1, min(requested_timeout, 120))

        from src import containment
        from src.tool_execution import agent_cwd

        sanitized_endpoint = f"{parsed.scheme}://{parsed.netloc}{parsed.path}" if parsed.scheme and parsed.netloc else "host_shell_bridge"
        owner = str(ctx.get("session_id") or ctx.get("owner") or "host_shell")
        spec = _owned_spec(agent_cwd(), ctx.get("subproc_env"), timeout)
        grant = containment.declare_external_bridge(spec, owner=owner, endpoint=sanitized_endpoint)
        boundary = grant.to_dict()
        boundary["executed"] = False

        request_body: dict[str, object] = {"timeout": timeout}
        request_id = ""
        if job_id:
            request_body["job_id"] = job_id
        else:
            request_body["command"] = command
            if bool(args.get("detach")) or _host_shell_requires_detach(command):
                request_body["detach"] = True
            else:
                request_id = secrets.token_urlsafe(18)
                request_body["request_id"] = request_id

        try:
            async with httpx.AsyncClient(timeout=timeout + 5, trust_env=False) as client:
                resp = await client.post(
                    run_url,
                    json=request_body,
                    headers={"X-Odysseus-TUI-Bridge-Token": token},
                )
                if resp.status_code >= 400:
                    return {
                        "error": f"host_shell: bridge returned HTTP {resp.status_code}",
                        "exit_code": 1,
                        "host_bridge": "tui",
                        "containment": boundary,
                    }
                data = resp.json()

                # A long command may be detached even when the model omitted
                # the flag. Complete that implicit job at the transport layer
                # so the model cannot report success from a mere start ack.
                if (
                    not job_id
                    and _host_shell_should_auto_poll(command)
                    and isinstance(data, dict)
                    and data.get("job_id")
                    and data.get("status") == "running"
                ):
                    auto_job_id = str(data["job_id"])
                    deadline = time.monotonic() + timeout
                    while time.monotonic() < deadline:
                        await asyncio.sleep(0.25)
                        poll = await client.post(
                            run_url,
                            json={"job_id": auto_job_id},
                            headers={"X-Odysseus-TUI-Bridge-Token": token},
                        )
                        if poll.status_code >= 400:
                            return {
                                "error": f"host_shell: bridge returned HTTP {poll.status_code}",
                                "exit_code": 1,
                                "host_bridge": "tui",
                                "containment": boundary,
                            }
                        data = poll.json()
                        if not isinstance(data, dict):
                            continue
                        # A bridge may briefly lose the job record while its
                        # detached worker is being registered. Keep polling;
                        # do not turn that transient state into exit code 1.
                        if data.get("status") in {"running", "unknown"}:
                            continue
                        if data.get("status") != "running":
                            break
                    if isinstance(data, dict) and data.get("status") in {"running", "unknown"}:
                        data = {
                            **data,
                            "status": "running",
                            "detached": True,
                            "job_id": auto_job_id,
                            "output": "host job still running; poll the returned job_id",
                            "exit_code": 0,
                        }
        except asyncio.CancelledError:
            if request_id:
                task = asyncio.create_task(
                    _cancel_host_shell_bridge_request(url, token, request_id),
                    name=f"cancel-host-shell-{request_id[:24]}",
                )
                _HOST_SHELL_CANCEL_TASKS.add(task)
                task.add_done_callback(_HOST_SHELL_CANCEL_TASKS.discard)
            raise
        except Exception as e:
            return {"error": f"host_shell: bridge call failed: {e}", "exit_code": 1, "containment": boundary}

        if not isinstance(data, dict):
            return {"error": "host_shell: bridge returned invalid payload", "exit_code": 1, "containment": boundary}
        if data.get("error"):
            return {
                "error": _truncate(str(data["error"]), MAX_OUTPUT_CHARS),
                "exit_code": 1,
                "host_bridge": "tui",
                "containment": boundary,
            }
        stdout = str(data.get("stdout") or data.get("output") or "")
        stderr = str(data.get("stderr") or "")
        raw_exit_code = data.get("exit_code")
        if raw_exit_code is None:
            raw_exit_code = data.get("returncode")
        if raw_exit_code is None:
            raw_exit_code = 0
        if isinstance(raw_exit_code, bool) or not isinstance(raw_exit_code, int):
            return {
                "error": "host_shell: bridge returned an invalid exit_code",
                "exit_code": 1,
                "host_bridge": "tui",
                "containment": boundary,
            }
        exit_code = raw_exit_code
        output = stdout.rstrip()
        if stderr.strip():
            output = (output + "\nSTDERR: " + stderr.strip()).strip() if output else "STDERR: " + stderr.strip()
        boundary["executed"] = True
        result = {
            "output": _truncate(output, MAX_OUTPUT_CHARS) or "(no output)",
            "exit_code": exit_code,
            "host_bridge": "tui",
            "containment": boundary,
        }
        for key in ("detached", "job_id", "status", "running", "finished", "cwd"):
            if key in data:
                result[key] = data[key]
        return result

def _python_child_runtime_failure(stdout: str, stderr: str, returncode: int) -> str:
    """Return an unmistakable nested-runtime failure hidden by Python exit 0.

    Libraries such as Pillow may spawn a viewer and then return normally even
    when that child cannot display anything.  Keep this deliberately narrow:
    arbitrary stderr is often a warning and must not turn a successful data
    transformation into a failed tool call.
    """
    if returncode != 0 or str(stdout or "").strip():
        return ""
    err = str(stderr or "").strip()
    if re.search(r"(?im)^xdg-open: no method available for opening\b", err):
        return err
    return ""


def _python_with_visible_final_expression(content: str) -> str:
    """Give the Python tool REPL-like visibility for one final bare value.

    The code still runs once as a normal script. Only a final expression is
    assigned and rendered; explicit print calls and statement-only programs
    retain their historical behavior.
    """
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return content
    if not tree.body or not isinstance(tree.body[-1], ast.Expr):
        return content
    final = tree.body[-1]
    if (
        isinstance(final.value, ast.Call)
        and isinstance(final.value.func, ast.Name)
        and final.value.func.id == "print"
    ):
        return content
    result_name = "__odysseus_final_expression_value__"
    tree.body[-1:] = [
        ast.Assign(targets=[ast.Name(id=result_name, ctx=ast.Store())], value=final.value),
        ast.If(
            test=ast.Compare(
                left=ast.Name(id=result_name, ctx=ast.Load()),
                ops=[ast.IsNot()],
                comparators=[ast.Constant(value=None)],
            ),
            body=[ast.Expr(value=ast.Call(
                func=ast.Name(id="print", ctx=ast.Load()),
                args=[ast.Call(
                    func=ast.Name(id="repr", ctx=ast.Load()),
                    args=[ast.Name(id=result_name, ctx=ast.Load())],
                    keywords=[],
                )],
                keywords=[],
            ))],
            orelse=[],
        ),
    ]
    ast.fix_missing_locations(tree)
    return ast.unparse(tree)


def _python_with_configured_import_paths(content: str, env: dict | None) -> str:
    """Expose only explicitly configured package roots under Python ``-I``."""
    raw = str((env or {}).get("ODYSSEUS_PYTHON_TOOL_SITE_PACKAGES", ""))
    paths = [item for item in raw.split(os.pathsep) if item and os.path.isabs(item)]
    if not paths:
        return content
    return f"import site\n[site.addsitedir(path) for path in {paths!r}]\nexec(compile({content!r}, '<odysseus-python-tool>', 'exec'))"


class PythonTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        from src.tool_execution import agent_cwd, _truncate
        if re.search(
            r"\b(?:requests\.(?:get|post|put|delete|request)|urllib\.request(?:\.\w+)?|httpx\.(?:get|post|request))\s*\(",
            content,
            re.IGNORECASE,
        ) and re.search(r"https?://", content, re.IGNORECASE) or (
            re.search(r"[\"'](?:curl|wget)[\"']", content, re.IGNORECASE)
            and re.search(r"https?://", content, re.IGNORECASE)
        ):
            return {
                "error": (
                    "python: ad-hoc HTTP access is disabled when native web tools are "
                    "available. Use pdf_extract for online PDFs, web_fetch for a concrete "
                    "page, or web_search for discovery. For PDF extraction "
                    "tasks, treat pdf_extract as the download+scan step: extract the "
                    "requested values, then create the requested output artifacts directly "
                    "from that evidence instead of trying requests/urllib again."
                ),
                "exit_code": 1,
            }
        from src.agent_runtime.process_resources import require_launch
        from src.agent_runtime.resources import ResourceIdentityError
        try:
            require_launch("python", cwd=agent_cwd(), content=content)
        except ResourceIdentityError as error:
            return {"error": str(error), "exit_code": 1, "blocked": True, "failure_kind": "resource_identity_denied"}
        if "/tmp/" in content:
            isolated_tmp = _isolated_tmp_dir(agent_cwd())
            content = content.replace("/tmp/", isolated_tmp.rstrip("/") + "/")
        _subproc_env = ctx.get("subproc_env")
        content = _python_with_configured_import_paths(
            _python_with_visible_final_expression(content), _subproc_env
        )
        # All Python code acquires the same server-defined boundary, including
        # arithmetic and ordinary imports. Source text never selects a scope.
        raw_paths = str((_subproc_env or {}).get("ODYSSEUS_PYTHON_TOOL_SITE_PACKAGES", ""))
        roots = tuple(path for path in raw_paths.split(os.pathsep) if path and os.path.isabs(path))
        return await _run_owned_command(
            [sys.executable or "python", "-I", "-c", content], ctx,
            tool="python", timeout=DEFAULT_PYTHON_TIMEOUT, argv=True, readonly_extra=roots,
        )
