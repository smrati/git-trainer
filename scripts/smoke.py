"""Verify every exercise in the curriculum.

Two things are checked, for every exercise:

1. **The reference solution passes its own checks.** If it does not, the
   exercise is broken: the answer the app grades against does not work.
2. **The starter fails.** If a blank script passes, the exercise is not
   testing anything.

This is the check that matters, and git makes it cheap: no daemon, no
build, no network. The whole curriculum is verified in seconds.

    uv run python scripts/smoke.py
    uv run python scripts/smoke.py --lesson f-what-is-a-repository
    uv run python scripts/smoke.py -v
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.curriculum.loader import load_curriculum  # noqa: E402
from app.engine.runner import MODE_SUBMIT, evaluate  # noqa: E402


def check_exercise(curriculum, exercise, verbose: bool = False) -> tuple[bool, str]:
    """Run the reference and the starter. Returns (ok, message)."""
    reference = evaluate(curriculum, exercise, exercise.solution, mode=MODE_SUBMIT)

    if not reference.ok:
        return False, f"the reference failed to run: {reference.error}"

    if reference.comparison is None:
        return False, "the reference produced no comparison"

    if not reference.comparison.passed:
        failed = [c for c in reference.comparison.checks if not c.passed]
        details = "; ".join(
            f"{c.name}: {c.detail or 'differs'}" for c in failed[:3]
        )
        return False, f"the reference does not pass its own checks: {details}"

    starter = evaluate(curriculum, exercise, exercise.starter, mode=MODE_SUBMIT)
    if not starter.ok:
        # A starter that errors is still a failing starter. It is worth
        # knowing about, but it is not a broken exercise.
        if verbose:
            print(f"    (the starter failed to run: {starter.error})")
        return True, ""

    if starter.comparison and starter.comparison.passed:
        return False, "the starter passes -- the exercise does not test anything"

    return True, ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the curriculum.")
    parser.add_argument("--lesson", help="only check exercises in this lesson")
    parser.add_argument("--exercise", help="only check this exercise")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    curriculum = load_curriculum()

    exercises = list(curriculum.exercises.values())
    if args.lesson:
        lesson = curriculum.get_lesson(args.lesson)
        exercises = list(lesson.exercises)
    if args.exercise:
        exercises = [curriculum.get_exercise(args.exercise)]

    if not exercises:
        print("No exercises matched.")
        return 1

    failures: list[tuple[str, str]] = []
    started = time.perf_counter()

    for exercise in exercises:
        ok, message = check_exercise(curriculum, exercise, verbose=args.verbose)
        status = "ok  " if ok else "FAIL"
        print(f"{status} {exercise.id}  {exercise.title}")
        if not ok:
            failures.append((exercise.id, message))
            print(f"     {message}")

    elapsed = time.perf_counter() - started
    print()
    print(f"{len(exercises) - len(failures)}/{len(exercises)} exercises verified in {elapsed:.1f}s")

    if failures:
        print()
        for exercise_id, message in failures:
            print(f"  {exercise_id}: {message}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
