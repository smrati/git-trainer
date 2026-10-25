"""Reading a repository's state as JSON.

``git status`` is good at telling you about the working tree and terrible at
telling you about a *repository*. This module turns a sandbox repository into
structured data: which branches exist, which one is current, what the
history looks like, what is staged, what is tracked.

Two properties matter for grading:

* **No hashes.** A hash is a function of content, ancestry, identity and
  timestamps. It is never the thing being taught, and comparing hashes
  compares git's internals instead of git's behaviour. The history is
  therefore described by *structure*: each commit's subject, its author, the
  refs pointing at it, and the indices of its parents.

* **Stable across runs.** The same operations on the same seed produce the
  same state, every time, because the sandbox pins the clock and the
  identity and gives every repository the same relative layout.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.engine.git import CommandResult, EngineError, run_git

# The unit and record separators used in `git log --format`. They cannot
# appear in a commit message in practice, and they make parsing unambiguous
# in a way that a space or a tab does not.
FIELD = "\x1f"
RECORD = "\x1e"

# The selectors a `state` check may ask for.
SELECTORS = (
    "branches",
    "remote_branches",
    "status",
    "log",
    "tags",
    "remotes",
    "tracked_files",
    "config",
)

# How many fields each reader's format string produces.
REF_FIELDS = 4
STATUS_FIELDS = 3
LOG_FIELDS = 7
LOG_PARENT_FIELDS = 2
TAG_FIELDS = 3
REMOTE_FIELDS = 2
REMOTE_DIRECTION_FIELD = 3


@dataclass
class StateError(Exception):
    """The state could not be read."""

    def __init__(self, message: str, *, kind: str = "state_error") -> None:
        super().__init__(message)
        self.message = message
        self.kind = kind


def _git(args: list[str], repo: str, env: dict[str, str], bare: bool) -> CommandResult:
    """Run git against a working repository or a bare one."""
    if bare:
        return run_git([f"--git-dir={repo}", *args], cwd=repo, env=env)
    return run_git(["-C", repo, *args], cwd=repo, env=env)


def _require(result: CommandResult, what: str) -> str:
    if not result.ok:
        message = (result.stderr or result.stdout).strip()
        raise StateError(f"Could not read the {what}: {message or 'git failed.'}")
    return result.stdout


# --------------------------------------------------------------------------
# Branches
# --------------------------------------------------------------------------


def _read_refs(repo: str, env: dict[str, str], bare: bool, pattern: str) -> list[dict]:
    fmt = FIELD.join(["%(refname)", "%(refname:short)", "%(HEAD)", "%(subject)"])
    result = _git(
        ["for-each-ref", f"--format={fmt}", pattern],
        repo,
        env,
        bare,
    )
    text = _require(result, "references")

    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split(FIELD)
        if len(parts) < REF_FIELDS:
            continue
        full_name, name, head, subject = parts[0], parts[1], parts[2], FIELD.join(parts[3:])
        # `refs/remotes/origin/HEAD` is a symbolic pointer, not a branch, and
        # its short name is just "origin" -- so it has to be filtered on the
        # *full* name, before git shortens it.
        if full_name.endswith("/HEAD"):
            continue
        rows.append(
            {
                "name": name,
                "current": head.strip() == "*",
                "subject": subject.strip(),
            }
        )
    return rows


def read_branches(repo: str, env: dict[str, str], bare: bool = False) -> list[dict]:
    """The local branches, the way ``git branch`` lists them."""
    return _read_refs(repo, env, bare, "refs/heads")


def read_remote_branches(repo: str, env: dict[str, str], bare: bool = False) -> list[dict]:
    """The remote-tracking branches, the way ``git branch -r`` lists them."""
    return _read_refs(repo, env, bare, "refs/remotes")


# --------------------------------------------------------------------------
# Status
# --------------------------------------------------------------------------


def read_status(repo: str, env: dict[str, str], bare: bool = False) -> list[dict]:
    """The working tree and index state, the way ``git status`` reports it.

    Each entry is ``{"index": "M", "worktree": " ", "path": "app.py"}`` --
    the first character is the staged change, the second the unstaged one.
    A renamed or copied file also carries ``original``.
    """
    if bare:
        raise StateError("A bare repository has no working tree to report on.")

    result = _git(["status", "--porcelain=v1", "-z"], repo, env, bare)
    text = _require(result, "status")

    parts = text.split("\0")
    entries: list[dict] = []
    index = 0
    while index < len(parts):
        item = parts[index]
        index += 1
        if not item:
            continue
        if len(item) < STATUS_FIELDS:
            continue
        code = item[:2]
        path = item[3:]
        entry = {"index": code[0], "worktree": code[1], "path": path}
        if "R" in code or "C" in code:
            # A rename or a copy is followed by its original path.
            if index < len(parts):
                entry["original"] = parts[index]
                index += 1
        entries.append(entry)
    return entries


# --------------------------------------------------------------------------
# History
# --------------------------------------------------------------------------


def read_log(repo: str, env: dict[str, str], bare: bool = False) -> dict:
    """The commit history, as a structure rather than a list of hashes.

    ``--full-history`` is what makes this the *whole* graph: without it git
    simplifies history and can prune a merge that did not change the tree,
    which would hide exactly the thing an exercise about merging is asking
    about.

    Each commit records its subject, its author, the refs pointing at it and
    the *indices* of its parents -- so the shape of the graph is compared,
    and not a single hash ever crosses this boundary.
    """
    fmt = FIELD.join(["%H", "%P", "%s", "%an", "%ae", "%D", "%b"]) + RECORD
    result = _git(["log", "--full-history", "--topo-order", f"--format={fmt}"], repo, env, bare)
    text = _require(result, "history")

    commits: list[dict] = []
    index_of: dict[str, int] = {}

    # `git log --format=` puts a newline *after* each record, so splitting on
    # the record separator leaves every record but the first starting with a
    # newline. That newline has to go: it would become part of the commit's
    # hash, and the hash is the key everything else is looked up by.
    records = [record.strip("\n") for record in text.split(RECORD)]

    for record in records:
        if not record.strip():
            continue
        parts = record.split(FIELD)
        while len(parts) < LOG_FIELDS:
            parts.append("")
        sha, parents, subject, author, email, refs, body = parts[:7]
        index_of[sha] = len(commits)
        commits.append(
            {
                "subject": subject.strip(),
                "author": author.strip(),
                "email": email.strip(),
                "refs": [ref.strip() for ref in refs.split(",") if ref.strip()],
                "body": body.strip(),
                "parents": [],
            }
        )

    # Second pass: the parents are hashes, and the answer needs indices.
    for record in records:
        if not record.strip():
            continue
        parts = record.split(FIELD)
        if len(parts) < LOG_PARENT_FIELDS:
            continue
        position = index_of.get(parts[0].strip())
        if position is None:
            continue
        parent_indices = []
        for parent in parts[1].split():
            parent_index = index_of.get(parent.strip())
            if parent_index is not None:
                parent_indices.append(parent_index)
        commits[position]["parents"] = parent_indices

    return {"commits": commits, "count": len(commits)}


# --------------------------------------------------------------------------
# Tags, remotes, files, config
# --------------------------------------------------------------------------


def read_tags(repo: str, env: dict[str, str], bare: bool = False) -> list[dict]:
    """The tags, and for an annotated tag the message it carries."""
    fmt = FIELD.join(["%(refname:short)", "%(objecttype)", "%(subject)"])
    result = _git(
        ["for-each-ref", f"--format={fmt}", "refs/tags"],
        repo,
        env,
        bare,
    )
    text = _require(result, "tags")

    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split(FIELD)
        if len(parts) < TAG_FIELDS:
            continue
        rows.append(
            {
                "name": parts[0].strip(),
                "type": parts[1].strip(),
                "message": FIELD.join(parts[2:]).strip(),
            }
        )
    return rows


def read_remotes(repo: str, env: dict[str, str], bare: bool = False) -> list[dict]:
    """The configured remotes and the URLs they point at."""
    result = _git(["remote", "-v"], repo, env, bare)
    text = _require(result, "remotes")

    rows: dict[str, dict] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) < REMOTE_FIELDS:
            continue
        name = parts[0]
        url = parts[1]
        direction = "fetch"
        if len(parts) >= REMOTE_DIRECTION_FIELD and parts[2].startswith("("):
            direction = parts[2].strip("()")
        row = rows.setdefault(name, {"name": name, "fetch_url": "", "push_url": ""})
        if direction == "push":
            row["push_url"] = url
        else:
            row["fetch_url"] = url
    return list(rows.values())


def read_tracked_files(repo: str, env: dict[str, str], bare: bool = False) -> list[str]:
    """The files git is tracking."""
    result = _git(["ls-files"], repo, env, bare)
    text = _require(result, "tracked files")
    return [line for line in text.splitlines() if line.strip()]


def read_config(repo: str, env: dict[str, str], bare: bool = False) -> dict:
    """The repository's own configuration."""
    result = _git(["config", "--local", "--list"], repo, env, bare)
    text = _require(result, "configuration")

    config: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        config[key.strip()] = value.strip()
    return config


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------

READERS = {
    "branches": read_branches,
    "remote_branches": read_remote_branches,
    "status": read_status,
    "log": read_log,
    "tags": read_tags,
    "remotes": read_remotes,
    "tracked_files": read_tracked_files,
    "config": read_config,
}


def read_state(
    selector: str,
    repo: str,
    env: dict[str, str],
    bare: bool = False,
) -> object:
    """Read one named piece of a repository's state."""
    reader = READERS.get(selector)
    if reader is None:
        raise StateError(
            f"Unknown state selector '{selector}'. "
            f"Choose one of: {', '.join(SELECTORS)}."
        )
    try:
        return reader(repo, env, bare)
    except EngineError as exc:
        raise StateError(exc.message, kind=exc.kind) from exc
