"""Tests for the curriculum: its shape, its content, and its correctness.

The shape is checked at startup by the loader; the content is checked here,
because a lesson with no hints or no explanation is a broken app that only
shows its face once a learner is halfway through it.

The correctness check -- that every reference solution really produces what
it claims, and that every starter really fails -- is the important one. It
is marked ``slow`` because it runs a repository per exercise, but unlike the
Docker trainer's equivalent it needs no daemon, so it is fast enough to run
every time.
"""

import pytest

from app.curriculum.loader import ContentError, load_curriculum
from app.engine.runner import MODE_SUBMIT, evaluate


@pytest.fixture(scope="module")
def curriculum():
    return load_curriculum()


def _commands(text: str) -> list[str]:
    """The meaningful lines of a script: comments and blanks dropped."""
    lines = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(stripped)
    return lines


class TestShape:
    def test_there_are_tracks(self, curriculum):
        assert curriculum.tracks, "the curriculum has no tracks"

    def test_every_track_has_lessons(self, curriculum):
        for track in curriculum.tracks:
            assert track.lessons, f"track {track.id} has no lessons"

    def test_every_lesson_has_exercises(self, curriculum):
        for lesson in curriculum.lessons.values():
            assert lesson.exercises, f"lesson {lesson.id} has no exercises"

    def test_exercise_ids_are_unique(self, curriculum):
        ids = [ex.id for ex in curriculum.exercises.values()]
        assert len(ids) == len(set(ids))

    def test_lesson_ids_are_unique(self, curriculum):
        ids = [lesson.id for lesson in curriculum.lessons.values()]
        assert len(ids) == len(set(ids))

    def test_the_curriculum_is_substantial(self, curriculum):
        """A teaching app with three exercises is a demo, not a course."""
        assert len(curriculum.tracks) >= 3
        assert len(curriculum.lessons) >= 12
        assert len(curriculum.exercises) >= 40

    def test_every_exercise_has_checks(self, curriculum):
        for exercise in curriculum.exercises.values():
            assert exercise.checks, f"exercise {exercise.id} has no checks"

    def test_every_exercise_has_a_starter_and_a_solution(self, curriculum):
        for exercise in curriculum.exercises.values():
            assert exercise.starter.strip(), f"exercise {exercise.id} has an empty starter"
            assert exercise.solution.strip(), f"exercise {exercise.id} has no solution"


class TestContent:
    def test_every_exercise_has_hints(self, curriculum):
        for exercise in curriculum.exercises.values():
            assert exercise.hints, f"exercise {exercise.id} has no hints"

    def test_every_exercise_explains_itself(self, curriculum):
        for exercise in curriculum.exercises.values():
            assert exercise.teach.strip(), f"exercise {exercise.id} has no explanation"

    def test_every_exercise_is_tagged(self, curriculum):
        for exercise in curriculum.exercises.values():
            assert exercise.tags, f"exercise {exercise.id} has no tags"

    def test_tags_come_from_the_controlled_vocabulary(self, curriculum):
        """A free-form tag means the review page gets a heading nobody
        chose, and two spellings of one idea become two concepts."""
        from app.routers.review import CONCEPT_LABELS

        for exercise in curriculum.exercises.values():
            for tag in exercise.tags:
                assert tag in CONCEPT_LABELS, (
                    f"exercise {exercise.id} uses the unknown tag {tag!r}"
                )

    def test_every_lesson_has_a_body(self, curriculum):
        for lesson in curriculum.lessons.values():
            assert lesson.body.strip(), f"lesson {lesson.id} has no body"

    def test_every_lesson_has_a_summary(self, curriculum):
        for lesson in curriculum.lessons.values():
            assert lesson.summary.strip(), f"lesson {lesson.id} has no summary"

    def test_the_starter_does_not_contain_the_answer(self, curriculum):
        """A starter that leaks the solution makes the exercise a typing test.

        Comments are exempt -- a starter that says "run `git status` here"
        is teaching, not leaking. A *command* that is in the solution is.
        """
        for exercise in curriculum.exercises.values():
            starter = _commands(exercise.starter)
            solution = _commands(exercise.solution)
            for command in solution:
                assert command not in starter, (
                    f"exercise {exercise.id}: the starter already contains "
                    f"the answer {command!r}"
                )

    def test_the_solution_is_not_trivially_empty(self, curriculum):
        for exercise in curriculum.exercises.values():
            assert _commands(exercise.solution), (
                f"exercise {exercise.id}: the solution runs nothing"
            )


class TestSeeds:
    def test_a_seed_may_not_escape_the_sandbox(self, curriculum):
        from app.engine.sandbox import SeedError, safe_relative_path

        for exercise in curriculum.exercises.values():
            for name in exercise.seed.files:
                try:
                    safe_relative_path(name)
                except SeedError as exc:
                    raise AssertionError(
                        f"exercise {exercise.id}: {exc.message}"
                    ) from exc

    def test_a_remote_check_names_a_remote_that_exists(self, curriculum):
        for exercise in curriculum.exercises.values():
            remotes = set(exercise.seed.remotes)
            for check in exercise.checks:
                if check.remote:
                    assert check.remote in remotes, (
                        f"exercise {exercise.id}: the check {check.name!r} reads "
                        f"a remote {check.remote!r} the seed does not create"
                    )


class TestValidation:
    """The loader's own checks, exercised directly."""

    def test_a_duplicate_id_is_rejected(self, tmp_path):
        track = tmp_path / "foundations"
        track.mkdir()
        (track / "track.yaml").write_text(
            "id: t\ntitle: T\nlessons:\n"
            "  - id: l\n    title: L\n    exercises:\n"
            "      - id: e\n        title: E\n        prompt: p\n"
            "        starter: 'x'\n        solution: 'y'\n"
            "        checks:\n"
            "          - name: c\n            kind: command\n            command: 'true'\n",
            encoding="utf-8",
        )
        load_curriculum(tmp_path)  # loads fine: one track, one exercise

        (track / "track.yaml").write_text(
            "id: t\ntitle: T\nlessons:\n"
            "  - id: l\n    title: L\n    exercises:\n"
            "      - id: e\n        title: E\n        prompt: p\n"
            "        starter: 'x'\n        solution: 'y'\n"
            "        checks:\n"
            "          - name: c\n            kind: command\n            command: 'true'\n"
            "      - id: e\n        title: E2\n        prompt: p\n"
            "        starter: 'x'\n        solution: 'y'\n"
            "        checks:\n"
            "          - name: c\n            kind: command\n            command: 'true'\n",
            encoding="utf-8",
        )
        with pytest.raises(ContentError, match="duplicate exercise id"):
            load_curriculum(tmp_path)

    def test_an_unknown_check_kind_is_rejected(self, tmp_path):
        track = tmp_path / "foundations"
        track.mkdir()
        (track / "track.yaml").write_text(
            "id: t\ntitle: T\nlessons:\n"
            "  - id: l\n    title: L\n    exercises:\n"
            "      - id: e\n        title: E\n        prompt: p\n"
            "        starter: 'x'\n        solution: 'y'\n"
            "        checks:\n"
            "          - name: c\n            kind: nonsense\n",
            encoding="utf-8",
        )
        with pytest.raises(ContentError, match="kind"):
            load_curriculum(tmp_path)

    def test_an_unknown_state_selector_is_rejected(self, tmp_path):
        track = tmp_path / "foundations"
        track.mkdir()
        (track / "track.yaml").write_text(
            "id: t\ntitle: T\nlessons:\n"
            "  - id: l\n    title: L\n    exercises:\n"
            "      - id: e\n        title: E\n        prompt: p\n"
            "        starter: 'x'\n        solution: 'y'\n"
            "        checks:\n"
            "          - name: c\n            kind: state\n            select: nonsense\n",
            encoding="utf-8",
        )
        with pytest.raises(ContentError, match="select"):
            load_curriculum(tmp_path)

    def test_a_hint_that_parses_as_a_mapping_is_rejected(self, tmp_path):
        """`- the catch: it truncates` is valid YAML -- as a list holding a
        mapping. The loader is what notices that it is not a string."""
        track = tmp_path / "foundations"
        track.mkdir()
        (track / "track.yaml").write_text(
            "id: t\ntitle: T\nlessons:\n"
            "  - id: l\n    title: L\n    exercises:\n"
            "      - id: e\n        title: E\n        prompt: p\n"
            "        starter: 'x'\n        solution: 'y'\n"
            "        checks:\n"
            "          - name: c\n            kind: command\n            command: 'true'\n"
            "        hints:\n"
            "          - the catch: it truncates\n",
            encoding="utf-8",
        )
        with pytest.raises(ContentError, match="hint"):
            load_curriculum(tmp_path)


@pytest.mark.slow
class TestCorrectness:
    """The check that matters: every exercise really works.

    The reference solution must pass its own checks, and the starter must
    fail. An exercise where doing nothing passes is not an exercise.
    """

    def test_every_reference_passes_and_every_starter_fails(self, curriculum):
        failures = []

        for exercise in curriculum.exercises.values():
            reference = evaluate(curriculum, exercise, exercise.solution, mode=MODE_SUBMIT)
            if not reference.ok:
                failures.append(f"{exercise.id}: the reference failed: {reference.error}")
                continue
            if reference.comparison is None or not reference.comparison.passed:
                failed = [
                    c for c in (reference.comparison.checks if reference.comparison else [])
                    if not c.passed
                ]
                detail = "; ".join(f"{c.name}: {c.detail}" for c in failed[:3])
                failures.append(f"{exercise.id}: the reference does not pass: {detail}")
                continue

            starter = evaluate(curriculum, exercise, exercise.starter, mode=MODE_SUBMIT)
            if starter.ok and starter.comparison and starter.comparison.passed:
                failures.append(f"{exercise.id}: the starter passes -- nothing is tested")

        assert not failures, "\n".join(failures)
