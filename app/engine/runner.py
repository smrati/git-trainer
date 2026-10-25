"""Turning a script into results: run it, look at the repository, compare it.

This is the parent half of the engine. It owns the sandbox a submission
lives in, decides which checks run against it, and cleans up after itself.

The flow, and it is the same flow the Docker trainer uses:

1. **Seed** a throwaway repository from the exercise's seed.
2. **Run** the script in it.
3. **Read** the checks -- one comparable value per check.
4. **Compare** those values with the reference's.

Step 4 is where the grading happens, and it is a comparison between two
*repositories*, never between a repository and a hand-written expectation.
The reference script is run in an identical seed and checked the same way,
so the answer is "what the reference does".

The reference is cached per exercise, because running it is the expensive
half and it never changes.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field

from app.config import SCRIPT_TIMEOUT_SECONDS
from app.engine.compare import CheckResult, ComparisonResult, compare_checks
from app.engine.git import EngineError
from app.engine.probe import run_check_command, run_script
from app.engine.sandbox import Sandbox, Seed, SeedError, create_sandbox
from app.engine.scrub import scrub_text, scrub_value
from app.engine.state import StateError, read_state

# The modes an evaluation can run in.
MODE_RUN = "run"
MODE_SUBMIT = "submit"


class ReferenceBroken(Exception):
    """The exercise's own reference script does not work.

    Always an authoring bug, and always the app's fault rather than the
    learner's.
    """


@dataclass
class CheckOutcome:
    """One check, run against one repository."""

    name: str
    kind: str
    result: dict = field(default_factory=dict)
    settings: object = None

    def to_dict(self) -> dict:
        payload = {"name": self.name, "kind": self.kind, "result": self.result}
        if self.settings is not None:
            payload["settings"] = (
                self.settings.to_payload()
                if hasattr(self.settings, "to_payload")
                else self.settings
            )
        return payload


@dataclass
class Evaluation:
    """The outcome of evaluating a script."""

    ok: bool = True
    error: str | None = None
    error_kind: str = "error"
    transcript: str = ""
    script: dict = field(default_factory=dict)
    checks: list[CheckOutcome] = field(default_factory=list)
    comparison: ComparisonResult | None = None
    duration_ms: float = 0.0
    state: dict = field(default_factory=dict)

    def to_payload(self) -> dict:
        return {
            "ok": self.ok,
            "error": {"kind": self.error_kind, "message": self.error} if self.error else None,
            "transcript": self.transcript,
            "script": self.script,
            "checks": [check.to_dict() for check in self.checks],
            "grading": self.comparison.to_payload() if self.comparison else None,
            "duration_ms": round(self.duration_ms, 2),
            "state": self.state,
        }


# --------------------------------------------------------------------------
# Seeding helpers
# --------------------------------------------------------------------------


def seed_digest(seed: Seed) -> str:
    """A fingerprint of a seed, so two cases can share one reference answer."""
    payload = json.dumps(
        {
            "files": seed.files,
            "commands": seed.commands,
            "remotes": {
                name: {"files": remote.files, "commands": remote.commands}
                for name, remote in sorted(seed.remotes.items())
            },
            "env": seed.env,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------
# Reference expectations
# --------------------------------------------------------------------------


def reference_expectations(curriculum, exercise) -> dict[str, list[dict]]:
    """The reference script's behaviour, computed once and cached.

    Keyed by the *case's* seed digest, not by the exercise id: two cases may
    start from different repositories, and those two have different answers.
    """
    cached = curriculum.expectations.get(exercise.id)
    if cached is not None:
        return cached

    results: dict[str, list[dict]] = {}
    for case in exercise.cases:
        key = case.cache_key
        if key in results:
            continue
        outcome = evaluate_artefact(exercise, exercise.solution, case.seed)
        if not outcome.ok:
            # A reference that does not work is an authoring bug. It is
            # surfaced as an error rather than cached as an empty answer,
            # which would quietly mark every submission wrong.
            raise ReferenceBroken(outcome.error or "The reference solution failed.")
        results[key] = [check.to_dict() for check in outcome.checks]

    curriculum.expectations[exercise.id] = results
    return results


# --------------------------------------------------------------------------
# Artefact evaluation
# --------------------------------------------------------------------------


def _check_result(check, sandbox: Sandbox, paths: dict[str, str]) -> CheckOutcome:
    """Run one check and return its comparable value."""
    if check.kind == "command":
        result = run_check_command(check.command, sandbox)
        stdout = scrub_text(result.stdout, paths) if check.settings.scrub else result.stdout
        stderr = scrub_text(result.stderr, paths) if check.settings.scrub else result.stderr
        return CheckOutcome(
            name=check.name,
            kind=check.kind,
            result={
                "stdout": stdout,
                "stderr": stderr,
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
                "truncated": result.truncated,
            },
            settings=check.settings,
        )

    if check.kind == "state":
        repo = str(sandbox.repo)
        bare = False
        if check.remote:
            path = sandbox.remotes.get(check.remote)
            if path is None:
                raise StateError(
                    f"This exercise has no remote named '{check.remote}'.",
                    kind="no_such_remote",
                )
            repo = str(path)
            bare = True
        value = read_state(check.select, repo, sandbox.env.values, bare)
        if check.settings.scrub:
            value = scrub_value(value, paths)
        return CheckOutcome(
            name=check.name,
            kind=check.kind,
            result={"value": value},
            settings=check.settings,
        )

    raise StateError(
        f"A '{check.kind}' check is not a kind this engine can run.",
        kind="bad_check",
    )


def _repository_summary(sandbox: Sandbox, paths: dict[str, str]) -> dict:
    """What the learner sees after a run: where they are, and what is dirty."""
    env = sandbox.env.values
    summary: dict = {"current_branch": None, "branches": [], "status": []}

    try:
        branches = read_state("branches", str(sandbox.repo), env)
        summary["branches"] = scrub_value(branches, paths)
        for branch in summary["branches"]:
            if branch.get("current"):
                summary["current_branch"] = branch.get("name")
                break
    except (StateError, EngineError):
        pass

    try:
        status = read_state("status", str(sandbox.repo), env)
        summary["status"] = scrub_value(status, paths)
    except (StateError, EngineError):
        pass

    return summary


def evaluate_artefact(exercise, script: str, seed: Seed) -> Evaluation:
    """Seed a repository, run a script in it, and read every check.

    ``script`` is the learner's script or the reference's; ``seed`` is the
    repository they both start from.
    """
    started = time.perf_counter()

    try:
        sandbox = create_sandbox(seed)
    except SeedError as exc:
        return Evaluation(ok=False, error=exc.message, error_kind=exc.kind)
    except EngineError as exc:
        return Evaluation(ok=False, error=exc.message, error_kind=exc.kind)

    try:
        paths = sandbox.scrub_paths()

        result = run_script(script, sandbox, timeout=SCRIPT_TIMEOUT_SECONDS)
        transcript = result.stdout + result.stderr

        checks: list[CheckOutcome] = []
        for check in exercise.checks:
            if check.kind == "output":
                # The script's own output, not a command's. The shell's
                # trace goes to stderr, so stdout carries only what the
                # learner's commands printed.
                stdout = (
                    scrub_text(result.stdout, paths)
                    if check.settings.scrub
                    else result.stdout
                )
                checks.append(
                    CheckOutcome(
                        name=check.name,
                        kind=check.kind,
                        result={
                            "stdout": stdout,
                            "stderr": "",
                            "exit_code": result.exit_code,
                        },
                        settings=check.settings,
                    )
                )
                continue
            try:
                checks.append(_check_result(check, sandbox, paths))
            except (StateError, EngineError) as exc:
                return Evaluation(
                    ok=False,
                    error=exc.message,
                    error_kind=getattr(exc, "kind", "check_failed"),
                    transcript=transcript,
                    script=result.to_payload(),
                    duration_ms=(time.perf_counter() - started) * 1000,
                )

        summary = _repository_summary(sandbox, paths)

        return Evaluation(
            ok=True,
            transcript=transcript,
            script=result.to_payload(),
            checks=checks,
            state=summary,
            duration_ms=(time.perf_counter() - started) * 1000,
        )
    finally:
        sandbox.cleanup()


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def evaluate(
    curriculum,
    exercise,
    script: str,
    *,
    mode: str = MODE_SUBMIT,
) -> Evaluation:
    """Evaluate a submission.

    ``MODE_RUN`` runs the script and the checks without comparing anything
    and without touching the reference. Exploring must never be mistaken for
    answering.

    ``MODE_SUBMIT`` also runs the reference and compares.
    """
    if mode == MODE_RUN:
        case = exercise.cases[0] if exercise.cases else None
        if case is None:
            return Evaluation(
                ok=False, error="This exercise has no cases.", error_kind="bad_exercise"
            )
        return evaluate_artefact(exercise, script, case.seed)

    # --- the reference -----------------------------------------------------
    try:
        expectations = reference_expectations(curriculum, exercise)
    except ReferenceBroken as exc:
        return Evaluation(
            ok=False,
            error=(
                "This exercise's reference solution does not work, so it cannot "
                f"be graded: {exc}"
            ),
            error_kind="reference_error",
        )

    # --- the learner's script ----------------------------------------------
    all_results: list[CheckResult] = []
    all_checks: list[CheckOutcome] = []
    total = 0
    passed = 0
    started = time.perf_counter()

    for case in exercise.cases:
        expected = expectations.get(case.cache_key)
        if expected is None:  # pragma: no cover - the cache is filled above
            return Evaluation(
                ok=False,
                error="This exercise has no reference answer.",
                error_kind="reference_error",
            )

        evaluated = evaluate_artefact(exercise, script, case.seed)
        if not evaluated.ok:
            return evaluated

        actual = [check.to_dict() for check in evaluated.checks]
        comparison = compare_checks(expected, actual, exercise.settings)

        if not comparison.passed:
            evaluated.comparison = comparison
            evaluated.duration_ms = (time.perf_counter() - started) * 1000
            return evaluated

        all_results.extend(comparison.checks)
        all_checks.extend(evaluated.checks)
        passed += comparison.passed_count
        total += comparison.total_count

    # Every case passed. One summary comparison carries the whole run.
    summary = ComparisonResult(
        passed=True,
        reason=None,
        detail=None,
        checks=all_results,
        passed_count=passed,
        total_count=total,
    )

    return Evaluation(
        ok=True,
        transcript=evaluated.transcript,
        script=evaluated.script,
        checks=all_checks,
        comparison=summary,
        state=evaluated.state,
        duration_ms=(time.perf_counter() - started) * 1000,
    )


def run_in_sandbox(script: str, seed: Seed) -> Evaluation:
    """Run a script in a sandbox without any checks.

    This is the playground: free exploration, never graded, never recorded.
    """
    return evaluate_artefact(_PlaygroundExercise(), script, seed)


class _PlaygroundExercise:
    """A stand-in exercise for the playground: one check, no grading."""

    def __init__(self) -> None:
        self.checks: list = []
        self.settings = None
