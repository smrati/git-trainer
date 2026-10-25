"""Running things inside a sandbox: the learner's script, and the checks.

Two kinds of run, and the difference matters:

* **The learner's script** is a shell script, run as a whole, with ``sh -x``
  so that every command is echoed before it runs. The transcript is what the
  learner reads, and a trace is worth more than a parsed log: it shows the
  commands *in the order they ran*, including the ones inside a loop.

* **A check's command** is a single shell command, run after the script has
  finished, in the same directory. Its output is what is compared.

Neither ever touches the machine's git configuration, the machine's home
directory, or anything outside the sandbox: see
:mod:`app.engine.git` for the environment that makes that true.
"""

from __future__ import annotations

from app.config import COMMAND_TIMEOUT_SECONDS, SCRIPT_TIMEOUT_SECONDS
from app.engine.git import CommandResult, run_shell
from app.engine.sandbox import Sandbox


def run_script(
    script: str,
    sandbox: Sandbox,
    *,
    timeout: float = SCRIPT_TIMEOUT_SECONDS,
) -> CommandResult:
    """Run a learner's script in the sandbox repository."""
    env = sandbox.env.values
    return run_shell(
        script,
        cwd=sandbox.repo,
        env=env,
        timeout=timeout,
        trace=True,
    )


def run_check_command(
    command: str,
    sandbox: Sandbox,
    *,
    timeout: float = COMMAND_TIMEOUT_SECONDS,
) -> CommandResult:
    """Run one check's command in the sandbox repository."""
    env = sandbox.env.values
    return run_shell(
        command,
        cwd=sandbox.repo,
        env=env,
        timeout=timeout,
        trace=False,
    )
