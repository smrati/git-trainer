"""Grading a submission end to end.

Three steps, in this order:

1. **Check the source-level rules.** Some exercises are about a construct
   rather than a result -- "without using ``git reset --hard``", "the commit
   message must mention the ticket" -- and a result-based grader cannot see
   those. They are opt-in per exercise.
2. **Run the learner's script and the reference script** over the same
   checks, in the same seed. The reference's behaviour **is** the expected
   answer. This is what makes grading semantic rather than textual: there is
   no hand-written expected output anywhere in this app to drift out of date,
   and any correct implementation passes however it was written.
3. **Compare the two** and record the attempt.

The reference is computed once per exercise and cached. It is trusted
content, but it still runs in a sandbox like everything else, because a
reference that hangs must not hang the server.
"""

from __future__ import annotations

import hashlib

from app.engine.compare import ComparisonResult
from app.engine.runner import MODE_RUN, MODE_SUBMIT, evaluate
from app.progress import (
    clear_review_state,
    get_review_state,
    get_state,
    record_attempt,
    save_review_state,
)
from app.srs import apply_review


def _source_problem(script: str, exercise) -> str | None:
    """Check the submitted script against the exercise's source-level rules.

    Two kinds of rule, both opt-in per exercise:

    * ``forbid`` -- a substring the script may not contain. Checked against
      the *text*, case-insensitively, and it fires before anything is run so
      the learner is told why rather than being shown a wrong answer.
    * ``require`` -- a substring the script must contain, for exercises where
      the construct is the point.
    """
    settings = exercise.settings
    haystack = (script or "").lower()

    for needle in settings.forbid:
        if needle.lower() in haystack:
            return (
                f"This exercise asks you to solve it without `{needle}`. "
                "Remove it and try again."
            )

    for needle in settings.require:
        if needle.lower() not in haystack:
            return f"This exercise needs your script to include `{needle}`."

    return None


def execute_only(curriculum, exercise, script: str) -> dict:
    """Run a script without grading it.

    This is the `run` endpoint -- exploring must never mark anything as
    complete, or a learner could stumble onto the right answer and have it
    silently counted as a solve.
    """
    outcome = evaluate(curriculum, exercise, script, mode=MODE_RUN)
    return _payload(outcome)


def _payload(outcome) -> dict:
    """Shape an evaluation for the browser."""
    return {
        "ok": outcome.ok,
        "error": (
            {"kind": outcome.error_kind, "message": outcome.error} if outcome.error else None
        ),
        "transcript": outcome.transcript,
        "script": outcome.script,
        "checks": [
            {
                "name": check.name,
                "kind": check.kind,
                "stdout": check.result.get("stdout", ""),
                "stderr": check.result.get("stderr", ""),
                "exit_code": check.result.get("exit_code"),
                "value": check.result.get("value"),
                "error": check.result.get("error"),
            }
            for check in outcome.checks
        ],
        "grading": outcome.comparison.to_payload() if outcome.comparison else None,
        "duration_ms": round(outcome.duration_ms, 2),
        "state": outcome.state,
    }


def _update_schedule(exercise_id: str, passed: bool) -> dict:
    """Fold a graded submission into the spaced-repetition schedule.

    Three rules, each one there to avoid putting a lie in the review queue:

    * **A pass schedules** the exercise for a future review.
    * **A failure only reschedules something already scheduled.** Failing an
      exercise you are currently learning is not forgetting, it is learning.
      Scheduling it would fill the queue with material you have never held,
      which is where spaced repetition is least useful.
    * **A revealed exercise is unscheduled.** Once you have seen the answer,
      any later pass stops being evidence of retention.
    """
    progress = get_state(exercise_id)
    existing = get_review_state(exercise_id)

    if progress.revealed_at:
        clear_review_state(exercise_id)
        return {"scheduled": False, "reason": "revealed"}

    if passed:
        state = apply_review(existing, True)
        save_review_state(state)
        return {
            "scheduled": True,
            "due_at": state.due_at,
            "interval_days": round(state.stability_days, 2),
            "reps": state.reps,
            "lapses": state.lapses,
        }

    if existing.is_scheduled:
        # You used to have this one and have just lost it: due right now.
        state = apply_review(existing, False)
        save_review_state(state)
        return {
            "scheduled": True,
            "due_at": state.due_at,
            "due_now": True,
            "reps": state.reps,
            "lapses": state.lapses,
        }

    return {"scheduled": False, "reason": "not_yet_scheduled"}


def grade(curriculum, exercise, script: str) -> dict:
    """Run, compare against the reference, and record the attempt."""
    # --- source-level rules ----------------------------------------------
    # Checked before anything runs: an exercise that says "without using
    # git reset --hard" should say so, rather than quietly failing a
    # correct-looking answer.
    problem = _source_problem(script, exercise)
    if problem:
        record_attempt(exercise.id, script, False, error=problem)
        comparison = ComparisonResult(
            passed=False,
            reason="source",
            detail=problem,
            checks=[],
        )
        review = _update_schedule(exercise.id, False)
        return {
            "ok": True,
            "error": None,
            "result": None,
            "grading": comparison.to_payload(),
            "review": review,
        }

    # --- run, compare -----------------------------------------------------
    outcome = evaluate(curriculum, exercise, script, mode=MODE_SUBMIT)

    if not outcome.ok:
        record_attempt(
            exercise.id,
            script,
            False,
            error=outcome.error,
            duration_ms=outcome.duration_ms,
        )
        return {
            "ok": False,
            "error": {"kind": outcome.error_kind, "message": outcome.error},
            "result": {
                "transcript": outcome.transcript,
                "script": outcome.script,
                "checks": [],
                "state": outcome.state,
            },
            "grading": None,
            "review": None,
        }

    comparison = outcome.comparison
    record_attempt(
        exercise.id,
        script,
        comparison.passed if comparison else False,
        checks_passed=comparison.passed_count if comparison else 0,
        checks_total=comparison.total_count if comparison else 0,
        duration_ms=outcome.duration_ms,
    )

    review = _update_schedule(exercise.id, bool(comparison and comparison.passed))

    payload = _payload(outcome)
    payload["grading"] = comparison.to_payload() if comparison else None
    payload["review"] = review
    payload["result"] = {
        "transcript": outcome.transcript,
        "script": outcome.script,
        "checks": payload["checks"],
        "state": outcome.state,
    }
    return payload


def reference_digest(exercise) -> str:
    """A short fingerprint of an exercise's reference script.

    Used by the tests to notice when a reference changes under a cached
    expectation.
    """
    payload = exercise.solution.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]
