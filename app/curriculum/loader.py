"""Loading and validating curriculum content.

Curriculum lives as YAML files under ``content/`` rather than in the app
database, so it is reviewed and diffed like code, and a typo in an exercise
is a test failure rather than a bug a learner discovers.

Directory layout::

    content/
      foundations/
        track.yaml
      intermediate/
        track.yaml
      advanced/
        track.yaml

Validation is strict and happens at startup: an exercise with no checks, a
duplicate id, or a check the engine cannot run is a broken app, not
something to discover on click.

Two things are checked here and two things are checked in the test suite,
and the split is deliberate:

* **At startup** -- everything cheap and structural: duplicate ids, missing
  fields, a check with no command, a seed with a file outside the sandbox.
* **In the tests** -- everything that needs a real repository: that every
  reference script really produces the state it claims, and that the starter
  does not contain the answer. Running eighty repositories at startup would
  add seconds to every page load.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from app.config import CONTENT_DIR
from app.engine.compare import CHECK_KINDS, ComparisonSettings
from app.engine.sandbox import Seed, SeedError
from app.engine.state import SELECTORS

# The kinds of exercise there are.
#
# * ``cli`` -- the learner writes a script of git commands; the grader runs it
#   against a seeded repository and compares the resulting repository.
#
# One kind today, and deliberately so: every git exercise is "do this to a
# repository", and a second kind would only add a way to write the same
# exercise twice.
EXERCISE_KINDS = ("cli",)


class ContentError(Exception):
    """A curriculum authoring mistake. Always names the offending file."""


@dataclass(frozen=True)
class Hint:
    text: str


@dataclass
class Case:
    """One variant of the repository the exercise starts from.

    A case is the git analogue of the Docker trainer's build argument: the
    same script, exercised against a different starting point. The reference
    is run once per case, so a case with a different seed has a different
    expected answer.
    """

    name: str = "default"
    seed: Seed = field(default_factory=Seed)

    @property
    def label(self) -> str:
        """What makes this case different, in words a learner can read."""
        parts = []
        if self.seed.files:
            parts.append(f"{len(self.seed.files)} file(s)")
        if self.seed.commands:
            parts.append(f"{len(self.seed.commands)} setup command(s)")
        if self.seed.env:
            parts.append(f"{len(self.seed.env)} environment variable(s)")
        return ", ".join(parts) if parts else "the default repository"
    @property
    def cache_key(self) -> str:
        """A key that identifies this case's repository."""
        payload = repr(
            {
                "init": self.seed.init,
                "files": sorted(self.seed.files.items()),
                "commands": self.seed.commands,
                "after": self.seed.after,
                "env": sorted(self.seed.env.items()),
                "remotes": sorted(
                    (name, sorted(remote.files.items()), remote.commands)
                    for name, remote in self.seed.remotes.items()
                ),
            }
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


@dataclass
class Check:
    """One thing the grader does to a repository and compares.

    Every check produces a single comparable value, and that value is
    produced by running the check against the *reference* repository and
    against the *learner's* repository. The reference's behaviour is the
    expected answer.
    """

    name: str
    kind: str
    command: str = ""
    select: str = ""
    remote: str = ""
    settings: ComparisonSettings = field(default_factory=ComparisonSettings)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "command": self.command,
            "select": self.select,
            "remote": self.remote,
            "settings": self.settings,
        }


@dataclass
class Exercise:
    id: str
    title: str
    prompt: str
    kind: str = "cli"
    starter: str = ""
    solution: str = ""
    seed: Seed = field(default_factory=Seed)
    checks: list[Check] = field(default_factory=list)
    cases: list[Case] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)
    teach: str = ""
    difficulty: str = "beginner"
    settings: ComparisonSettings = field(default_factory=ComparisonSettings)
    tags: list[str] = field(default_factory=list)

    @property
    def check_count(self) -> int:
        return len(self.checks)

    @property
    def case_count(self) -> int:
        return len(self.cases)


@dataclass
class Lesson:
    id: str
    title: str
    summary: str
    body: str
    exercises: list[Exercise] = field(default_factory=list)

    @property
    def exercise_count(self) -> int:
        return len(self.exercises)


@dataclass
class Track:
    id: str
    title: str
    description: str
    difficulty: str
    lessons: list[Lesson] = field(default_factory=list)
    order: int = 0

    @property
    def exercise_count(self) -> int:
        return sum(lesson.exercise_count for lesson in self.lessons)


@dataclass
class Curriculum:
    version: int
    tracks: list[Track]
    exercises: dict[str, Exercise]
    lessons: dict[str, Lesson]
    # Reference expectations, computed lazily and cached here. Keyed by
    # exercise id.
    expectations: dict[str, dict[str, list[dict]]] = field(default_factory=dict)

    def get_exercise(self, exercise_id: str) -> Exercise:
        try:
            return self.exercises[exercise_id]
        except KeyError:
            raise KeyError(f"Unknown exercise: {exercise_id}") from None

    def get_lesson(self, lesson_id: str) -> Lesson:
        try:
            return self.lessons[lesson_id]
        except KeyError:
            raise KeyError(f"Unknown lesson: {lesson_id}") from None


CURRICULUM_VERSION = 1


# --------------------------------------------------------------------------
# Loading helpers
# --------------------------------------------------------------------------


def _require(data: dict, key: str, path: Path, kind: type | tuple = str):
    if key not in data or data[key] in (None, ""):
        raise ContentError(f"{path}: missing required field '{key}'")
    value = data[key]
    if not isinstance(value, kind):
        expected = (
            kind.__name__
            if isinstance(kind, type)
            else " or ".join(k.__name__ for k in kind)
        )
        raise ContentError(f"{path}: field '{key}' must be {expected}")
    return value


def _parse_files(raw, path: Path, field_name: str) -> dict[str, str]:
    """Parse a map of path -> text."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ContentError(f"{path}: '{field_name}' must be a mapping of path to content")

    files: dict[str, str] = {}
    for name, content in raw.items():
        if not isinstance(name, str):
            raise ContentError(f"{path}: '{field_name}' keys must be strings")
        if content is None:
            files[name] = ""
            continue
        if not isinstance(content, str):
            raise ContentError(f"{path}: '{field_name}[{name}]' must be a string")
        files[name] = content
    return files


def _parse_env(raw, path: Path, field_name: str) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ContentError(f"{path}: '{field_name}' must be a mapping of name to value")
    env: dict[str, str] = {}
    for name, value in raw.items():
        if not isinstance(name, str):
            raise ContentError(f"{path}: '{field_name}' keys must be strings")
        env[name] = "" if value is None else str(value)
    return env


def _parse_commands(raw, path: Path, field_name: str) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ContentError(f"{path}: '{field_name}' must be a list of commands")
    commands = []
    for item in raw:
        if not isinstance(item, str):
            raise ContentError(f"{path}: '{field_name}' must be a list of strings")
        commands.append(item)
    return commands


def _parse_seed(raw, path: Path) -> Seed:
    """Parse a repository seed: its files, its commands and its remotes."""
    if raw is None:
        return Seed()
    if not isinstance(raw, dict):
        raise ContentError(f"{path}: 'seed' must be a mapping")

    remotes_raw = raw.get("remotes") or {}
    if not isinstance(remotes_raw, dict):
        raise ContentError(f"{path}: 'seed.remotes' must be a mapping of name to seed")

    remotes: dict[str, Seed] = {}
    for name, remote_raw in remotes_raw.items():
        if not isinstance(name, str) or not name.strip():
            raise ContentError(f"{path}: 'seed.remotes' names must be non-empty strings")
        remotes[name] = _parse_seed(remote_raw, path / f"seed.remotes.{name}")

    base = raw.get("base", "local")
    if base not in ("local", "empty"):
        raise ContentError(f"{path}: 'seed.base' must be 'local' or 'empty'")

    init = raw.get("init", True)
    if not isinstance(init, bool):
        raise ContentError(f"{path}: 'seed.init' must be true or false")

    seed = Seed(
        files=_parse_files(raw.get("files"), path, "seed.files"),
        commands=_parse_commands(raw.get("commands"), path, "seed.commands"),
        after=_parse_commands(raw.get("after"), path, "seed.after"),
        remotes=remotes,
        env=_parse_env(raw.get("env"), path, "seed.env"),
        init=init,
        base=base,
    )

    # The seed is checked here rather than at runtime, so a bad path is an
    # authoring error caught at startup rather than a failed exercise.
    from app.engine.sandbox import safe_relative_path

    for name in seed.files:
        try:
            safe_relative_path(name)
        except SeedError as exc:
            raise ContentError(f"{path}: {exc.message}") from exc

    return seed


def _parse_check(raw: dict, path: Path, base: ComparisonSettings) -> Check:
    if not isinstance(raw, dict):
        raise ContentError(f"{path}: each check must be a mapping")

    kind = raw.get("kind", "command")
    if kind not in CHECK_KINDS:
        raise ContentError(f"{path}: 'kind' must be one of {', '.join(CHECK_KINDS)}")

    name = raw.get("name") or ""
    if not isinstance(name, str):
        raise ContentError(f"{path}: 'name' must be a string")

    command = raw.get("command") or ""
    select = raw.get("select") or ""
    remote = raw.get("remote") or ""

    if not isinstance(command, str):
        raise ContentError(f"{path}: 'command' must be a string")
    if not isinstance(select, str):
        raise ContentError(f"{path}: 'select' must be a string")
    if not isinstance(remote, str):
        raise ContentError(f"{path}: 'remote' must be a string")

    if kind == "command" and not command.strip():
        raise ContentError(f"{path}: a command check needs a 'command'")
    if kind == "state":
        if not select.strip():
            raise ContentError(f"{path}: a state check needs a 'select'")
        if select not in SELECTORS:
            raise ContentError(
                f"{path}: 'select' must be one of {', '.join(SELECTORS)}"
            )

    try:
        settings = ComparisonSettings.from_dict(raw.get("settings"), base)
    except ValueError as exc:
        raise ContentError(f"{path}: {exc}") from exc

    return Check(
        name=name or f"{kind} check",
        kind=kind,
        command=command,
        select=select,
        remote=remote,
        settings=settings,
    )


def _parse_case(raw: dict, path: Path, index: int, base_seed: Seed) -> Case:
    if not isinstance(raw, dict):
        raise ContentError(f"{path}: each case must be a mapping")

    seed_raw = raw.get("seed")
    if seed_raw is None:
        seed = base_seed
    else:
        seed = base_seed.merged(_parse_seed(seed_raw, path / "seed"))

    name = raw.get("name") or f"case {index}"
    return Case(name=str(name), seed=seed)


def _parse_exercise(raw: dict, path: Path, default_difficulty: str) -> Exercise:
    if not isinstance(raw, dict):
        raise ContentError(f"{path}: each exercise must be a mapping")

    exercise_id = _require(raw, "id", path)
    hints = raw.get("hints", []) or []
    if not isinstance(hints, list):
        raise ContentError(f"{path}: 'hints' must be a list of strings")
    for hint in hints:
        # A hint that YAML read as a mapping -- "- the catch: it truncates" --
        # is valid YAML and the wrong thing entirely. It is caught here
        # rather than str()'d into a dict's repr.
        if not isinstance(hint, str):
            raise ContentError(f"{path}: every hint must be a string")

    kind = raw.get("kind", "cli")
    if kind not in EXERCISE_KINDS:
        raise ContentError(f"{path}: 'kind' must be one of {', '.join(EXERCISE_KINDS)}")

    try:
        settings = ComparisonSettings.from_dict(raw.get("settings"))
    except ValueError as exc:
        raise ContentError(f"{path}: {exc}") from exc

    starter = raw.get("starter")
    if starter is None:
        raise ContentError(f"{path}: missing required field 'starter'")
    if not isinstance(starter, str):
        raise ContentError(f"{path}: 'starter' must be a string")

    solution = raw.get("solution")
    if solution is None:
        raise ContentError(f"{path}: missing required field 'solution'")
    if not isinstance(solution, str):
        raise ContentError(f"{path}: 'solution' must be a string")

    seed = _parse_seed(raw.get("seed"), path)

    checks_raw = raw.get("checks") or []
    if not isinstance(checks_raw, list):
        raise ContentError(f"{path}: 'checks' must be a list")
    checks = [
        _parse_check(item, path / f"check {i}", settings)
        for i, item in enumerate(checks_raw, start=1)
    ]

    cases_raw = raw.get("cases") or []
    if not isinstance(cases_raw, list):
        raise ContentError(f"{path}: 'cases' must be a list")
    cases = [
        _parse_case(item, path / f"case {i}", i, seed)
        for i, item in enumerate(cases_raw, start=1)
    ]
    if not cases:
        cases = [Case(name="default", seed=seed)]

    return Exercise(
        id=exercise_id,
        title=_require(raw, "title", path),
        prompt=_require(raw, "prompt", path),
        kind=kind,
        starter=starter,
        solution=solution,
        seed=seed,
        checks=checks,
        cases=cases,
        hints=[str(h) for h in hints],
        teach=raw.get("teach", "") or "",
        difficulty=raw.get("difficulty", default_difficulty),
        settings=settings,
        tags=[str(t) for t in (raw.get("tags", []) or [])],
    )


def _parse_lesson(raw: dict, path: Path, default_difficulty: str) -> Lesson:
    if not isinstance(raw, dict):
        raise ContentError(f"{path}: each lesson must be a mapping")

    lesson_id = _require(raw, "id", path)
    exercises = [
        _parse_exercise(item, path / f"exercise {i}", default_difficulty)
        for i, item in enumerate(raw.get("exercises", []) or [], start=1)
    ]
    if not exercises:
        raise ContentError(f"{path}: lesson '{lesson_id}' has no exercises")

    return Lesson(
        id=lesson_id,
        title=_require(raw, "title", path),
        summary=raw.get("summary", "") or "",
        body=raw.get("body", "") or "",
        exercises=exercises,
    )


def _parse_track(raw: dict, path: Path) -> Track:
    if not isinstance(raw, dict):
        raise ContentError(f"{path}: each track must be a mapping")

    track_id = _require(raw, "id", path)
    difficulty = raw.get("difficulty", "beginner")
    lessons = [
        _parse_lesson(item, path / f"lesson {i}", difficulty)
        for i, item in enumerate(raw.get("lessons", []) or [], start=1)
    ]
    if not lessons:
        raise ContentError(f"{path}: track '{track_id}' has no lessons")

    return Track(
        id=track_id,
        title=_require(raw, "title", path),
        description=raw.get("description", "") or "",
        difficulty=difficulty,
        lessons=lessons,
        order=raw.get("order", 0),
    )


def _validate(curriculum: Curriculum) -> None:
    """Reject duplicate ids and missing pieces.

    Duplicate exercise ids are checked across *all* tracks, not just within
    one: ids key both the progress table and the API, so a collision would
    make one exercise silently overwrite the other's progress.
    """
    seen_exercises: dict[str, str] = {}
    seen_lessons: dict[str, str] = {}

    for track in curriculum.tracks:
        for lesson in track.lessons:
            if lesson.id in seen_lessons:
                raise ContentError(
                    f"duplicate lesson id '{lesson.id}' in "
                    f"'{seen_lessons[lesson.id]}' and track '{track.id}'"
                )
            seen_lessons[lesson.id] = track.id

            for exercise in lesson.exercises:
                if exercise.id in seen_exercises:
                    raise ContentError(
                        f"duplicate exercise id '{exercise.id}' in "
                        f"'{seen_exercises[exercise.id]}' and lesson '{lesson.id}'"
                    )
                seen_exercises[exercise.id] = lesson.id

                if not exercise.checks:
                    raise ContentError(
                        f"exercise '{exercise.id}' has no checks -- without them "
                        "there is nothing to grade against"
                    )

                if not exercise.starter.strip():
                    raise ContentError(
                        f"exercise '{exercise.id}' has an empty starter -- a "
                        "learner should never face an empty editor"
                    )

                if not exercise.solution.strip():
                    raise ContentError(
                        f"exercise '{exercise.id}' has no reference solution"
                    )

                remotes = set(exercise.seed.remotes)
                for check in exercise.checks:
                    if check.remote and check.remote not in remotes:
                        raise ContentError(
                            f"exercise '{exercise.id}': the check "
                            f"'{check.name}' reads the remote "
                            f"'{check.remote}', which the seed does not create"
                        )


def load_curriculum(content_dir: Path | str = CONTENT_DIR) -> Curriculum:
    """Read every track file and return a validated curriculum."""
    content_dir = Path(content_dir)
    if not content_dir.is_dir():
        raise ContentError(f"content directory not found: {content_dir}")

    tracks: list[Track] = []
    seen_track_ids: set[str] = set()
    for track_file in sorted(content_dir.glob("*/track.yaml")):
        track = _parse_track(
            yaml.safe_load(track_file.read_text(encoding="utf-8")), track_file
        )
        if track.id in seen_track_ids:
            raise ContentError(f"duplicate track id '{track.id}' in {track_file}")
        seen_track_ids.add(track.id)
        tracks.append(track)

    if not tracks:
        raise ContentError(f"no track files found under {content_dir}")

    tracks.sort(key=lambda t: (t.order, t.title.lower()))

    exercises: dict[str, Exercise] = {}
    lessons: dict[str, Lesson] = {}
    for track in tracks:
        for lesson in track.lessons:
            lessons[lesson.id] = lesson
            for exercise in lesson.exercises:
                exercises[exercise.id] = exercise

    curriculum = Curriculum(
        version=CURRICULUM_VERSION,
        tracks=tracks,
        exercises=exercises,
        lessons=lessons,
    )
    _validate(curriculum)
    return curriculum
