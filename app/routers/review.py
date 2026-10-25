"""Spaced repetition endpoints: the due queue and per-concept strength."""

from collections import OrderedDict
from datetime import timedelta

from fastapi import APIRouter, Depends

from app.curriculum.loader import Curriculum
from app.deps import get_curriculum
from app.progress import all_review_states, solved_exercise_ids
from app.srs import (
    DUE_SOON_DAYS,
    QueueEntry,
    build_queue,
    concept_breakdown,
    parse_ts,
    utcnow,
)

router = APIRouter(prefix="/api", tags=["review"])

# Human labels for the controlled tag vocabulary. Kept here rather than in
# the curriculum so that authoring content never has to carry display text,
# and a typo in a tag cannot produce a blank heading.
CONCEPT_LABELS = {
    "basics": ("Git basics", "What a repository is, and the three places a file can be"),
    "init": ("Starting a repository", "git init, and what the .git directory holds"),
    "status": ("Reading the status", "git status, and the staging area"),
    "staging": ("Staging changes", "git add, and choosing what goes into a commit"),
    "commit": ("Making commits", "git commit, messages, and amending"),
    "log": ("Reading the history", "git log, and the options that change what it shows"),
    "diff": ("Comparing changes", "git diff, and comparing anything to anything"),
    "ignore": ("Ignoring files", ".gitignore, tracked files, and removing them"),
    "undo": ("Undoing things", "restore, reset, revert, and choosing the right one"),
    "stash": ("Stashing work", "git stash, and putting it back"),
    "branches": ("Branches", "Creating, moving between, and naming branches"),
    "merge": ("Merging", "Fast-forwards, merge commits, and conflicts"),
    "rebase": ("Rebasing", "Moving commits, and what that does to history"),
    "cherry-pick": ("Cherry-picking", "Taking one commit from somewhere else"),
    "revert": ("Reverting commits", "Undoing a commit without rewriting history"),
    "remotes": ("Remotes", "clone, fetch, pull, push, and tracking branches"),
    "history": ("Finding things", "reflog, blame, and searching the history"),
    "plumbing": ("Plumbing", "cat-file, rev-parse, for-each-ref, and ls-tree"),
    "bisect": ("Bisecting", "git bisect, and finding the commit that broke it"),
    "worktrees": ("Worktrees", "Two working directories, one repository"),
    "hooks": ("Hooks", "pre-commit, commit-msg, and the hooks that are not there"),
    "tags": ("Tags", "Lightweight and annotated tags, and pointing at commits"),
    "archive": ("Archives and bundles", "git archive, git bundle, and sharing history"),
    "recovery": ("Recovery", "fsck, reflog expiry, and finding lost work"),
}


def _describe(exercise, entry: QueueEntry) -> dict:
    """One queue row: what to review, and how overdue it is."""
    return {
        **entry.to_payload(),
        "title": exercise.title,
        "difficulty": exercise.difficulty,
        "tags": exercise.tags,
    }


@router.get("/review")
def get_review(curriculum: Curriculum = Depends(get_curriculum)):
    """Everything due now, what is coming up, and per-concept strength.

    Concepts are ranked by actionable work first (how many exercises are due),
    then by historical accuracy. Deliberately not a weighted composite score:
    a hidden formula cannot be explained to a learner.
    """
    now = utcnow()
    states = all_review_states()
    queue = build_queue(states, now)

    due_ids = [entry.exercise_id for entry in queue]
    # An exercise that has been renamed or removed since it was scheduled
    # leaves a row behind. Dropping it is kinder than failing the whole
    # page: the curriculum moves, and a review queue that 409s because of
    # an old row is a queue nobody can use. The count is reported, so the
    # drop is visible rather than silent.
    missing = [eid for eid in due_ids if eid not in curriculum.exercises]
    due_ids = [eid for eid in due_ids if eid in curriculum.exercises]
    queue = [entry for entry in queue if entry.exercise_id in curriculum.exercises]

    exercises = OrderedDict((eid, curriculum.exercises[eid]) for eid in due_ids)
    concepts = concept_breakdown(curriculum.exercises, solved_exercise_ids(), states, now)

    due_soon = [
        entry.to_payload() | {"title": curriculum.exercises[entry.exercise_id].title}
        for entry in _due_soon(curriculum, states, now)
    ]

    scheduled = [s for s in states.values() if s.is_scheduled]
    reviews = sum(s.reps + s.lapses for s in states.values())
    next_due = _next_due(states, now)

    return {
        "now": now.strftime("%Y-%m-%d %H:%M:%S"),
        "counts": {
            "due_now": len(queue),
            "due_soon": len(due_soon),
            "scheduled": len(scheduled),
            "reviews_logged": reviews,
            "solved": len(solved_exercise_ids()),
            "next_due_at": next_due[0],
            "next_due_in_days": next_due[1],
            # Rows left behind by an exercise that no longer exists.
            "stale_entries": len(missing),
        },
        "due": [
            _describe(exercises[eid], entry)
            for eid, entry in zip(due_ids, queue, strict=True)
        ],
        "due_soon": due_soon,
        "concepts": [
            {
                **{k: v for k, v in concept.items() if k != "exercise_ids"},
                "label": CONCEPT_LABELS.get(concept["tag"], (concept["tag"], ""))[0],
                "description": CONCEPT_LABELS.get(concept["tag"], ("", ""))[1],
            }
            for concept in concepts
        ],
    }


def _next_due(states, now) -> tuple[str | None, float | None]:
    """The earliest future due date, so an empty queue can still say something.

    Without this the review page says "nothing due" and stops, which reads as
    a dead end rather than as "come back Thursday".
    """
    upcoming = [
        due
        for state in states.values()
        if (due := parse_ts(state.due_at)) is not None and due > now
    ]
    if not upcoming:
        return None, None
    soonest = min(upcoming)
    return soonest.strftime("%Y-%m-%d %H:%M:%S"), round((soonest - now).total_seconds() / 86400, 2)


def _due_soon(curriculum, states, now) -> list[QueueEntry]:
    """Scheduled exercises coming due within the next day.

    ``days_overdue`` is negative for these, which reads correctly in the UI as
    "due in N days" without needing a second code path.
    """
    horizon = now + timedelta(days=DUE_SOON_DAYS)
    entries: list[QueueEntry] = []

    for state in states.values():
        if not state.is_scheduled or state.exercise_id not in curriculum.exercises:
            continue
        due_at = parse_ts(state.due_at)
        if due_at is None or not (now < due_at <= horizon):
            continue
        lead = -(due_at - now).total_seconds() / 86400
        entries.append(QueueEntry(state.exercise_id, state.due_at, lead, state))

    entries.sort(key=lambda entry: entry.due_at)
    return entries
