"""The one place that shells out to ``git``.

Every other module in the engine goes through here, for one reason: this is
where the sandbox's safety properties live, and they are much easier to
guarantee in one file than to hope for across five.

The sandbox environment, and why each piece of it is there:

``PATH``
    A fixed list. The machine owner's ``PATH`` may contain directories that
    shadow ``git`` with something else, and a teaching tool that silently runs
    a different program is a teaching tool that teaches the wrong thing.

``HOME``
    Points inside the sandbox. Anything that wants to write to "the user's
    home" writes to a directory that is thrown away.

``GIT_CONFIG_GLOBAL`` / ``GIT_CONFIG_SYSTEM`` / ``GIT_CONFIG_NOSYSTEM``
    The machine owner's ``~/.gitconfig`` is not read. It may set
    ``commit.gpgsign``, which would make every commit fail without telling
    anyone why; it may set ``core.hooksPath``, which would run the owner's
    hooks; it may set ``init.defaultBranch``; it may set ``user.name`` to
    something embarrassing. A sandbox must depend on nothing but itself.

``GIT_ALLOW_PROTOCOL=file``
    Git's fetch and push protocols are a remote-code-execution surface:
    ``ext::`` runs an arbitrary command, and ``ssh``/``http`` reach the
    network. Restricting the set to ``file`` means a learner's script can
    only ever talk to the other repositories in its own sandbox.

``GIT_TERMINAL_PROMPT=0`` and ``GIT_ASKPASS``
    A credential prompt in a non-interactive process either hangs until the
    wall clock kills it or reads from a terminal that is not there. Both are
    worse than failing immediately.

``GIT_AUTHOR_*`` / ``GIT_COMMITTER_*``
    A commit's identity and timestamp go into its hash. Pinning them means
    two runs of the same script produce the same commits, which is what makes
    a reference answer reproducible. The sandbox's *global* config carries an
    identity too, so a learner can see it and change it with ``--local`` --
    but the environment variables win over config, so ``git config --local
    user.name`` would silently not take effect. The identity therefore lives
    only in the config file, and the environment leaves it alone.

``GIT_AUTHOR_DATE`` / ``GIT_COMMITTER_DATE``
    The same reason, for timestamps. ``git commit --date=`` still overrides
    the author date, so an exercise about author dates is unaffected.

This is **not a sandbox in the security sense**. A learner's script runs on
the host, with the host's kernel and the host's filesystem, and a determined
script can get out. What is here is a set of guards that catch accidents and
remove the obvious footguns; it is the same trade the Docker trainer makes
for its shell exercises, and it is documented as such.
"""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.config import (
    COMMAND_TIMEOUT_SECONDS,
    DEFAULT_BRANCH,
    GIT_BIN,
    OUTPUT_LIMIT_BYTES,
    SANDBOX_TIMESTAMP,
)

# The directories a sandbox's PATH may contain. Deliberately not the
# machine's PATH.
SANDBOX_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# The identity the sandbox's *global* config carries, so that a learner can
# read it (``git config user.name``) and override it per repository.
SANDBOX_CONFIG_USER_NAME = "Trainer"
SANDBOX_CONFIG_USER_EMAIL = "trainer@example.invalid"


class EngineError(Exception):
    """A failure the learner should read, not a stack trace.

    ``kind`` names the failure mode so the UI can style it, and so a test can
    assert on the reason rather than on a message that might be reworded.
    """

    def __init__(self, message: str, *, kind: str = "error") -> None:
        super().__init__(message)
        self.message = message
        self.kind = kind


@dataclass
class CommandResult:
    """What one command did."""

    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    truncated: bool = False
    timed_out: bool = False
    duration_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def to_payload(self) -> dict:
        return {
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "truncated": self.truncated,
            "timed_out": self.timed_out,
            "duration_ms": round(self.duration_ms, 2),
        }


@dataclass
class SandboxEnv:
    """The environment every sandboxed process runs with.

    Built once per sandbox and handed to every command, so there is exactly
    one definition of "what a sandbox looks like".
    """

    home: Path
    config_path: Path
    values: dict[str, str] = field(default_factory=dict)

    def with_overrides(self, overrides: dict[str, str] | None) -> dict[str, str]:
        """The environment, with an exercise's own variables layered on top."""
        if not overrides:
            return dict(self.values)
        merged = dict(self.values)
        merged.update({str(k): str(v) for k, v in overrides.items()})
        return merged


def build_sandbox_env(root: Path, overrides: dict[str, str] | None = None) -> SandboxEnv:
    """Create the sandbox's home directory, config file and environment."""
    home = root / "home"
    home.mkdir(parents=True, exist_ok=True)

    config_path = root / "gitconfig"
    config_path.write_text(
        "[user]\n"
        f"\tname = {SANDBOX_CONFIG_USER_NAME}\n"
        f"\temail = {SANDBOX_CONFIG_USER_EMAIL}\n"
        "[init]\n"
        f"\tdefaultBranch = {DEFAULT_BRANCH}\n"
        "[core]\n"
        "\tautocrlf = false\n"
        "\tsafecrlf = false\n",
        encoding="utf-8",
    )

    values = {
        "PATH": SANDBOX_PATH,
        "HOME": str(home),
        "SHELL": "/bin/sh",
        "TMPDIR": str(root / "tmp"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "TERM": "dumb",
        "NO_COLOR": "1",
        # Git: no machine-wide configuration of any kind.
        "GIT_CONFIG_GLOBAL": str(config_path),
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        # Git: no credential prompts, and no network protocols.
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "",
        "GIT_ALLOW_PROTOCOL": "file",
        # Git: a reproducible clock. The *identity* is deliberately not set
        # here: an empty GIT_AUTHOR_NAME is not the same as an absent one,
        # and an environment variable would override `git config --local`,
        # which is exactly what an exercise about per-repository identity
        # needs to change.
        "GIT_AUTHOR_DATE": SANDBOX_TIMESTAMP,
        "GIT_COMMITTER_DATE": SANDBOX_TIMESTAMP,
        # Git: never open an editor. An exercise that needs one sets
        # GIT_EDITOR or GIT_SEQUENCE_EDITOR itself.
        "GIT_EDITOR": "true",
        "GIT_SEQUENCE_EDITOR": "true",
        "GIT_PAGER": "cat",
        "GIT_FLUSH": "1",
    }
    (root / "tmp").mkdir(parents=True, exist_ok=True)

    # The exercise's own variables go on top of the sandbox's. They are
    # trusted content -- they come from the curriculum, not from a learner --
    # and an exercise needs them: `GIT_SEQUENCE_EDITOR` is what makes an
    # interactive rebase scriptable at all.
    if overrides:
        values.update({str(key): str(value) for key, value in overrides.items()})

    return SandboxEnv(home=home, config_path=config_path, values=values)


def run_command(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: float = COMMAND_TIMEOUT_SECONDS,
    stdin: str | None = None,
) -> CommandResult:
    """Run one command and capture what it did.

    Output is captured through temporary files rather than pipes, so an
    accidental gigabyte of output is written to disk and then cut short
    rather than held in the server's memory.
    """
    started = time.perf_counter()
    timed_out = False

    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
                argv,
                cwd=str(cwd),
                env=env,
                stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            raise EngineError(
                f"`{argv[0]}` is not installed, so this trainer cannot run it. "
                "Install it and try again.",
                kind="tool_missing",
            ) from exc

        try:
            process.communicate(
                input=stdin.encode("utf-8") if stdin is not None else None,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            timed_out = True
            # The whole process group, not just the leader: a script that
            # started a background job should not leave it running.
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):  # pragma: no cover
                process.kill()
            process.communicate()

        out.seek(0)
        err.seek(0)
        stdout = out.read(OUTPUT_LIMIT_BYTES).decode("utf-8", errors="replace")
        stderr = err.read(OUTPUT_LIMIT_BYTES).decode("utf-8", errors="replace")
        truncated = out.tell() > OUTPUT_LIMIT_BYTES or err.tell() > OUTPUT_LIMIT_BYTES

    return CommandResult(
        exit_code=-1 if timed_out else (process.returncode or 0),
        stdout=stdout,
        stderr=stderr,
        truncated=truncated,
        timed_out=timed_out,
        duration_ms=(time.perf_counter() - started) * 1000,
    )


def run_git(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: float = COMMAND_TIMEOUT_SECONDS,
) -> CommandResult:
    """Run ``git`` with the given arguments."""
    return run_command([GIT_BIN, *args], cwd=cwd, env=env, timeout=timeout)


def run_shell(
    script: str,
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: float = COMMAND_TIMEOUT_SECONDS,
    trace: bool = True,
) -> CommandResult:
    """Run a shell script.

    The script is passed on standard input rather than written into the
    working directory: a file called ``solve.sh`` sitting in the repository
    would show up in ``git status`` and break every check that reads it.

    ``trace`` runs the script with ``sh -x``, which echoes each command
    before running it. That turns the output into a transcript the learner
    can read line by line, and it costs nothing.
    """
    argv = ["/bin/sh"]
    if trace:
        argv.append("-x")
    return run_command(
        argv,
        cwd=cwd,
        env=env,
        timeout=timeout,
        stdin=script,
    )


def git_version() -> str | None:
    """The installed git version, or None if git is not available."""
    try:
        result = run_command(
            [GIT_BIN, "--version"],
            cwd=Path.cwd(),
            env={"PATH": SANDBOX_PATH, "HOME": "/tmp", "LC_ALL": "C.UTF-8"},
            timeout=10.0,
        )
    except EngineError:
        return None
    if not result.ok:
        return None
    return result.stdout.strip() or None
