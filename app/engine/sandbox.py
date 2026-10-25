"""Seeding a throwaway repository, and taking it away again.

Every exercise starts from a repository: some files, some history, maybe a
remote. The learner then works in that repository, and the grader looks at
what they made of it.

The layout of a sandbox is fixed, and fixed *relatively*, because git
records remote URLs as strings and a path that changes between runs would
make two identical repositories look different::

    <sandbox>/
      repo/              the working repository; the learner's cwd
      remotes/<name>/    a bare repository, for exercises about remotes
      home/              HOME, for anything that wants to write to it
      gitconfig          the sandbox's global configuration
      tmp/               TMPDIR

A remote is added as the *relative* path ``../remotes/<name>``, so the URL
git stores is the same string every time. (A repository cloned from it will
record an absolute path instead -- git resolves a relative clone URL when it
clones -- and the comparison scrubs sandbox paths for exactly that reason.)

**A remote is built from the local repository.** It is a clone of it, with
the remote's own files and commands applied on top. That is what makes a
remote *related* to the local repository: the two share history, so
``git fetch``, ``git merge origin/main`` and ``git pull`` all behave the way
they do in real life, rather than failing with "refusing to merge unrelated
histories".

The sandbox is thrown away at the end of every evaluation. A learner's
commits live in a temporary directory that does not survive the request.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from app.config import (
    DEFAULT_BRANCH,
    MAX_SEED_COMMANDS,
    MAX_SEED_FILE_BYTES,
    MAX_SEED_FILES,
    SANDBOX_PREFIX,
    SEED_TIMEOUT_SECONDS,
)
from app.engine.git import SandboxEnv, build_sandbox_env, run_git, run_shell


class SeedError(Exception):
    """A seed could not be built. Always an authoring bug."""

    def __init__(self, message: str, *, kind: str = "seed_failed") -> None:
        super().__init__(message)
        self.message = message
        self.kind = kind


@dataclass
class Seed:
    """What a repository looks like before the learner touches it.

    ``files``    the working tree's contents.
    ``commands`` shell commands run in the repository, before any remote
                 exists.
    ``after``    shell commands run in the repository, after the remotes
                 have been added -- for the rare seed that needs to fetch.
    ``remotes``  other repositories, keyed by remote name. Each is built by
                 cloning the local repository and applying its own seed.
    ``env``      extra environment variables for the whole sandbox.
    ``init``     whether the repository is created for the learner. False
                 leaves a plain directory, which is what an exercise about
                 ``git init`` needs.
    ``base``     for a remote: ``local`` (clone the local repository, the
                 default) or ``empty`` (start from nothing).
    """

    files: dict[str, str] = field(default_factory=dict)
    commands: list[str] = field(default_factory=list)
    after: list[str] = field(default_factory=list)
    remotes: dict[str, Seed] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    init: bool = True
    base: str = "local"

    def merged(self, other: Seed | None) -> Seed:
        """A case's seed laid over this one.

        Files are merged (the case wins on a conflict), commands are
        replaced wholesale (a partial command list would be ambiguous), and
        the environment is merged.
        """
        if other is None:
            return self
        files = dict(self.files)
        files.update(other.files)
        env = dict(self.env)
        env.update(other.env)
        return Seed(
            files=files,
            commands=list(other.commands) if other.commands else list(self.commands),
            after=list(other.after) if other.after else list(self.after),
            remotes=dict(self.remotes),
            env=env,
            init=self.init,
            base=self.base,
        )


@dataclass
class Sandbox:
    """A live sandbox: a directory, a repository, and some remotes."""

    root: Path
    repo: Path
    remotes: dict[str, Path]
    env: SandboxEnv

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def scrub_paths(self) -> dict[str, str]:
        """The path replacements that make two sandboxes comparable.

        Longest first, so ``<sandbox>/repo`` is replaced before the shorter
        ``<sandbox>`` that contains it.
        """
        replacements = {
            str(self.repo): "<repo>",
        }
        for name, path in self.remotes.items():
            replacements[str(path)] = f"<remote:{name}>"
        replacements[str(self.root)] = "<sandbox>"
        return dict(sorted(replacements.items(), key=lambda item: -len(item[0])))


# --------------------------------------------------------------------------
# Files
# --------------------------------------------------------------------------


def safe_relative_path(name: str) -> Path:
    """Validate a path from a seed and return it as a relative path.

    A seed is trusted content, but it is still content that ends up writing
    files, and an absolute path or a ``..`` would let an authoring mistake
    reach outside the sandbox.
    """
    if not isinstance(name, str) or not name.strip():
        raise SeedError("A seed file name may not be empty.")

    if name.startswith(("/", "~")):
        raise SeedError(f"The seed file name '{name}' must be relative.")

    if "\\" in name:
        raise SeedError(f"The seed file name '{name}' may not contain a backslash.")

    parts = PurePosixPath(name).parts
    if any(part == ".." for part in parts):
        raise SeedError(f"The seed file name '{name}' may not contain '..'.")

    if not parts:
        raise SeedError(f"The seed file name '{name}' is not a file.")

    return Path(*parts)


def materialise(files: dict[str, str], directory: Path) -> None:
    """Write a seed's files into a directory."""
    if len(files) > MAX_SEED_FILES:
        raise SeedError(f"A seed may contain at most {MAX_SEED_FILES} files.")

    for name, content in files.items():
        path = safe_relative_path(name)
        if len(content.encode("utf-8")) > MAX_SEED_FILE_BYTES:
            raise SeedError(f"The seed file '{name}' is too large.")
        target = directory / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


# --------------------------------------------------------------------------
# Building
# --------------------------------------------------------------------------


def _init_repository(path: Path, env: dict[str, str], bare: bool = False) -> None:
    args = ["init", "--quiet", f"--initial-branch={DEFAULT_BRANCH}"]
    if bare:
        args.append("--bare")
    args.append(str(path))
    result = run_git(args, cwd=path.parent, env=env, timeout=SEED_TIMEOUT_SECONDS)
    if not result.ok:
        raise SeedError(f"Could not create a repository: {result.stderr.strip()}")


def _run_commands(commands: list[str], repo: Path, env: dict[str, str]) -> None:
    """Run a seed's commands, one at a time, so a failure names itself."""
    if len(commands) > MAX_SEED_COMMANDS:
        raise SeedError(f"A seed may run at most {MAX_SEED_COMMANDS} commands.")

    for command in commands:
        if not command.strip():
            continue
        result = run_shell(
            command,
            cwd=repo,
            env=env,
            timeout=SEED_TIMEOUT_SECONDS,
            trace=False,
        )
        if not result.ok:
            detail = (result.stderr or result.stdout).strip()
            raise SeedError(
                f"The seed command `{command.strip()}` failed: "
                f"{detail or f'exit {result.exit_code}'}"
            )


def _copy_repository(source: Path, target: Path, env: dict[str, str]) -> None:
    """Copy every branch and tag from a working repository into a bare one."""
    refs = run_git(
        ["for-each-ref", "--format=%(refname)", "refs/heads", "refs/tags"],
        cwd=source,
        env=env,
        timeout=SEED_TIMEOUT_SECONDS,
    )
    names = [line.strip() for line in refs.stdout.splitlines() if line.strip()]

    for name in names:
        result = run_git(
            ["push", "--quiet", str(target), f"{name}:{name}"],
            cwd=source,
            env=env,
            timeout=SEED_TIMEOUT_SECONDS,
        )
        if not result.ok:
            raise SeedError(
                f"Could not copy {name} into the remote: {result.stderr.strip()}"
            )

    # The bare repository's HEAD has to point at the branch that was checked
    # out, or a clone of it warns that HEAD refers to a ref that does not
    # exist.
    head = run_git(
        ["symbolic-ref", "HEAD"],
        cwd=source,
        env=env,
        timeout=SEED_TIMEOUT_SECONDS,
    )
    if head.ok and head.stdout.strip():
        run_git(
            [f"--git-dir={target}", "symbolic-ref", "HEAD", head.stdout.strip()],
            cwd=target,
            env=env,
            timeout=SEED_TIMEOUT_SECONDS,
        )


def _build_remote(
    name: str,
    seed: Seed,
    local_repo: Path,
    remotes_dir: Path,
    env: dict[str, str],
) -> Path:
    """Build one bare repository for a remote.

    A bare repository is what a remote should be: pushing into a repository
    that has a working tree checked out is refused by git, and an exercise
    about pushing must not be refused for that reason.
    """
    bare_path = remotes_dir / name
    work_path = remotes_dir / f".{name}.work"

    if seed.base == "empty":
        _init_repository(work_path, env)
    else:
        # A clone of the local repository, so the two share history.
        result = run_git(
            ["clone", "--quiet", "--no-hardlinks", str(local_repo), str(work_path)],
            cwd=remotes_dir,
            env=env,
            timeout=SEED_TIMEOUT_SECONDS,
        )
        if not result.ok:
            raise SeedError(
                f"Could not clone the repository for the remote '{name}': "
                f"{result.stderr.strip()}"
            )

    materialise(seed.files, work_path)
    _run_commands(seed.commands, work_path, env)

    _init_repository(bare_path, env, bare=True)
    _copy_repository(work_path, bare_path, env)

    shutil.rmtree(work_path, ignore_errors=True)
    return bare_path


def create_sandbox(seed: Seed, root: Path | None = None) -> Sandbox:
    """Build a sandbox: the repository, then its remotes, then the rest."""
    owned_root = root is None
    root = root or Path(tempfile.mkdtemp(prefix=SANDBOX_PREFIX))
    try:
        (root / "tmp").mkdir(parents=True, exist_ok=True)
        sandbox_env = build_sandbox_env(root, seed.env)
        env = sandbox_env.values

        remotes_dir = root / "remotes"
        remotes_dir.mkdir(parents=True, exist_ok=True)

        repo = root / "repo"
        if seed.init:
            _init_repository(repo, env)
        else:
            repo.mkdir(parents=True, exist_ok=True)
        materialise(seed.files, repo)
        _run_commands(seed.commands, repo, env)

        # The remotes come next: they are built from the repository as it
        # stands now, so they share its history.
        remote_paths: dict[str, Path] = {}
        for name, remote_seed in seed.remotes.items():
            if not seed.init:
                raise SeedError(
                    f"The seed creates the remote '{name}' but does not create "
                    "a repository to clone it from."
                )
            remote_paths[name] = _build_remote(name, remote_seed, repo, remotes_dir, env)

        for name in remote_paths:
            # The URL is relative, so git stores the same string every time.
            relative = Path("..") / "remotes" / name
            result = run_git(
                ["remote", "add", name, str(relative)],
                cwd=repo,
                env=env,
                timeout=SEED_TIMEOUT_SECONDS,
            )
            if not result.ok:
                raise SeedError(
                    f"Could not add the remote '{name}': {result.stderr.strip()}"
                )

        _run_commands(seed.after, repo, env)

        return Sandbox(root=root, repo=repo, remotes=remote_paths, env=sandbox_env)
    except Exception:
        if owned_root:
            shutil.rmtree(root, ignore_errors=True)
        raise
