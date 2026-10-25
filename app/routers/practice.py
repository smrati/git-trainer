"""Write endpoints: running, submitting, revealing, and the playground."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.config import MAX_SCRIPT_CHARS
from app.curriculum.loader import Curriculum
from app.demo import demo_seed
from app.deps import get_curriculum
from app.grading import execute_only, grade
from app.progress import get_state, record_reveal

router = APIRouter(prefix="/api", tags=["practice"])

# A submission is one script of git commands. The cap is generous enough for
# a script that creates a branch, commits three times and merges, and tight
# enough that one request cannot carry a megabyte of text.
MAX_SCRIPT_COMMANDS = 200


class SubmissionRequest(BaseModel):
    script: str = Field(default="", max_length=MAX_SCRIPT_CHARS)


class PlaygroundRequest(BaseModel):
    script: str = Field(default="", max_length=MAX_SCRIPT_CHARS)
    # Naming an exercise starts the playground from that exercise's
    # repository, so a learner can explore the exact repository an exercise
    # is about without guessing what it contains.
    exercise_id: str | None = None


def _validate(script: str) -> str:
    """Check a submission's shape before anything is run.

    Rejecting a bad submission here is much kinder than rejecting it half
    way through a run: the learner gets an immediate, specific message
    instead of a git error.
    """
    if len(script) > MAX_SCRIPT_CHARS:
        raise HTTPException(status_code=400, detail="This script is too large.")

    commands = [line for line in script.splitlines() if line.strip()]
    if len(commands) > MAX_SCRIPT_COMMANDS:
        raise HTTPException(
            status_code=400,
            detail=f"A script may contain at most {MAX_SCRIPT_COMMANDS} commands.",
        )
    return script


def _get(exercise_id: str, curriculum: Curriculum):
    try:
        return curriculum.get_exercise(exercise_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"No such exercise: {exercise_id}") from None


@router.post("/exercises/{exercise_id}/run")
def run(
    exercise_id: str,
    body: SubmissionRequest,
    curriculum: Curriculum = Depends(get_curriculum),
):
    """Run a script in a fresh copy of the exercise's repository.

    Never grades and never records progress, so experimenting cannot be
    mistaken for completing an exercise.
    """
    exercise = _get(exercise_id, curriculum)
    script = _validate(body.script)
    try:
        return execute_only(curriculum, exercise, script)
    except Exception as exc:  # EngineError carries .kind/.message
        return _error_payload(exc)


@router.post("/exercises/{exercise_id}/submit")
def submit(
    exercise_id: str,
    body: SubmissionRequest,
    curriculum: Curriculum = Depends(get_curriculum),
):
    """Grade a submission against the reference answer and record the attempt."""
    exercise = _get(exercise_id, curriculum)
    script = _validate(body.script)
    outcome = grade(curriculum, exercise, script)
    if not outcome["ok"]:
        # A failed run is a normal outcome, not a 500: the message is feedback.
        return outcome
    return {**outcome, "state": get_state(exercise_id).to_payload()}


@router.post("/exercises/{exercise_id}/solution")
def reveal_solution(exercise_id: str, curriculum: Curriculum = Depends(get_curriculum)):
    """Return the reference solution and record that it was opened.

    Free to call: the point is to record the reveal honestly rather than to
    gate it. The exercise is then reported as `revealed`, never as `solved`.
    """
    exercise = _get(exercise_id, curriculum)
    record_reveal(exercise_id)
    return {
        "solution": exercise.solution,
        "teach": exercise.teach,
        "state": get_state(exercise_id).to_payload(),
    }


@router.post("/playground/run")
def playground(body: PlaygroundRequest, curriculum: Curriculum = Depends(get_curriculum)):
    """Run any git commands in a throwaway repository.

    Ungraded and unattributed: it is a workbench, not an exercise. Nothing
    it does is recorded, and nothing it does can touch another exercise's
    repository.
    """
    from app.engine.runner import run_in_sandbox

    script = _validate(body.script)

    seed = demo_seed()
    if body.exercise_id:
        exercise = _get(body.exercise_id, curriculum)
        seed = exercise.seed

    try:
        outcome = run_in_sandbox(script, seed)
    except Exception as exc:
        return _error_payload(exc)

    payload = outcome.to_payload()
    payload["checks"] = []
    return payload


def _error_payload(exc: Exception) -> dict:
    """Shape an EngineError into the same envelope as a successful run."""
    kind = getattr(exc, "kind", "error")
    message = getattr(exc, "message", None) or str(exc)
    return {
        "ok": False,
        "error": {"kind": kind, "message": message},
        "transcript": "",
        "script": {},
        "checks": [],
        "grading": None,
        "duration_ms": 0.0,
        "state": {},
    }
