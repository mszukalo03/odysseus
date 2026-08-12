import asyncio
import os
import re
import shutil
import sys
import time
import collections
from typing import Optional, Callable, Awaitable, Tuple, Dict
from core.platform_compat import IS_WINDOWS, find_bash
from src.constants import MAX_OUTPUT_CHARS

if not IS_WINDOWS:
    # POSIX-only: the sudo-password pty retry (_run_via_pty) needs a real
    # pty/tty, which these modules don't exist for on Windows.
    import fcntl
    import pty
    import termios

DEFAULT_BASH_TIMEOUT = 60 * 60     # 1 hour
DEFAULT_PYTHON_TIMEOUT = 60 * 60

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

class BashTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        from src.tool_execution import agent_cwd, _truncate
        from src import sudo_auth
        if isinstance(content, dict):
            content = str(content.get("command") or content.get("cmd") or content.get("code") or "")
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

class PythonTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        from src.tool_execution import agent_cwd, _truncate
        progress_cb = ctx.get("progress_cb")
        _subproc_env = ctx.get("subproc_env")
        proc = await asyncio.create_subprocess_exec(
            (sys.executable or "python"), "-I", "-c", content,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_subproc_env,
            cwd=agent_cwd(),
        )
        stdout, stderr, rc, timed_out = await _run_subprocess_streaming(
            proc,
            timeout=DEFAULT_PYTHON_TIMEOUT,
            progress_cb=progress_cb,
        )
        if timed_out:
            return {"error": f"python: timed out after {DEFAULT_PYTHON_TIMEOUT}s — process killed", "exit_code": 124, "stdout": _truncate(stdout, MAX_OUTPUT_CHARS), "stderr": _truncate(stderr, MAX_OUTPUT_CHARS)}
        output = stdout.rstrip()
        err = stderr.rstrip()
        if err:
            output = (output + "\nSTDERR: " + err).strip() if output else "STDERR: " + err
        output = _truncate(output, MAX_OUTPUT_CHARS)
        return {"output": output or "(no output)", "exit_code": rc or 0}
