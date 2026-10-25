"""Tests for the spaced-repetition schedule.

The schedule is a set of pure functions, so these tests are about the
*policy*: what happens on a pass, what happens on a failure, and what
deliberately does not get scheduled.
"""

from datetime import datetime, timedelta

import pytest

from app.srs import (
    INITIAL_INTERVAL_DAYS,
    MAX_INTERVAL_DAYS,
    PASS,
    ReviewState,
    apply_review,
    build_queue,
    concept_breakdown,
    parse_ts,
    schedule_fail,
    schedule_pass,
)


@pytest.fixture
def now():
    return datetime(2026, 1, 1, 12, 0, 0)


class TestSchedule:
    def test_a_first_pass_comes_back_in_a_day(self, now):
        state = schedule_pass(ReviewState("e"), now)
        assert state.stability_days == INITIAL_INTERVAL_DAYS
        assert state.reps == 1
        assert state.lapses == 0
        assert state.last_result == PASS

    def test_the_interval_grows(self, now):
        state = ReviewState("e")
        intervals = []
        for _ in range(4):
            state = schedule_pass(state, now)
            intervals.append(state.stability_days)
        assert intervals == pytest.approx([1.0, 2.2, 4.84, 10.648])

    def test_the_interval_is_capped(self, now):
        state = ReviewState("e", stability_days=MAX_INTERVAL_DAYS, reps=3)
        state = schedule_pass(state, now)
        assert state.stability_days == MAX_INTERVAL_DAYS

    def test_a_failure_is_due_immediately(self, now):
        state = schedule_pass(ReviewState("e"), now)
        state = schedule_fail(state, now)
        assert state.due_at == now.strftime("%Y-%m-%d %H:%M:%S")
        assert state.stability_days == 0.0
        assert state.lapses == 1

    def test_a_failure_resets_the_ladder(self, now):
        state = ReviewState("e", stability_days=10.0, reps=3)
        state = schedule_fail(state, now)
        state = schedule_pass(state, now)
        assert state.stability_days == INITIAL_INTERVAL_DAYS

    def test_a_failure_keeps_the_reps(self, now):
        state = ReviewState("e", reps=3)
        state = schedule_fail(state, now)
        assert state.reps == 3

    def test_apply_review_dispatches(self, now):
        assert apply_review(ReviewState("e"), True, now).last_result == PASS
        assert apply_review(ReviewState("e"), False, now).last_result == "fail"


class TestTimestamps:
    def test_a_timestamp_round_trips(self):
        moment = datetime(2026, 3, 4, 5, 6, 7)
        assert parse_ts(moment.strftime("%Y-%m-%d %H:%M:%S")) == moment

    def test_a_missing_timestamp_is_none(self):
        assert parse_ts(None) is None
        assert parse_ts("") is None

    def test_a_malformed_timestamp_is_none(self):
        """A hand-edited database should cost one exercise its place in the
        queue, not take down the review page."""
        assert parse_ts("not a date") is None


class TestQueue:
    def test_only_due_exercises_are_in_the_queue(self, now):
        states = {
            "due": ReviewState("due", due_at=(now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")),
            "later": ReviewState("later", due_at=(now + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")),
            "never": ReviewState("never"),
        }
        queue = build_queue(states, now)
        assert [entry.exercise_id for entry in queue] == ["due"]

    def test_the_most_overdue_is_first(self, now):
        states = {
            "a": ReviewState("a", due_at=(now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")),
            "b": ReviewState("b", due_at=(now - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")),
        }
        queue = build_queue(states, now)
        assert queue[0].exercise_id == "b"

    def test_a_queue_entry_carries_its_payload(self, now):
        states = {"a": ReviewState("a", due_at=(now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"))}
        payload = build_queue(states, now)[0].to_payload()
        assert payload["exercise_id"] == "a"
        assert payload["days_overdue"] == pytest.approx(1.0)


class _Exercise:
    def __init__(self, exercise_id: str, tags: list[str]):
        self.id = exercise_id
        self.tags = tags


class TestConcepts:
    def test_concepts_are_aggregated(self, now):
        exercises = {
            "a": _Exercise("a", ["git"]),
            "b": _Exercise("b", ["git"]),
        }
        rows = concept_breakdown(exercises, {"a"}, {}, now)
        assert len(rows) == 1
        assert rows[0]["tag"] == "git"
        assert rows[0]["total"] == 2
        assert rows[0]["solved"] == 1

    def test_a_concept_with_due_work_ranks_first(self, now):
        exercises = {
            "a": _Exercise("a", ["git"]),
            "b": _Exercise("b", ["sql"]),
        }
        states = {
            "a": ReviewState("a", due_at=(now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")),
        }
        rows = concept_breakdown(exercises, set(), states, now)
        assert rows[0]["tag"] == "git"

    def test_an_untouched_concept_is_marked_as_such(self, now):
        exercises = {"a": _Exercise("a", ["git"])}
        rows = concept_breakdown(exercises, set(), {}, now)
        assert rows[0]["state"] == "untouched"

    def test_a_weak_concept_is_marked_weak(self, now):
        exercises = {"a": _Exercise("a", ["git"])}
        states = {
            "a": ReviewState(
                "a",
                reps=1,
                lapses=4,
                due_at=(now + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
            ),
        }
        rows = concept_breakdown(exercises, set(), states, now)
        assert rows[0]["state"] == "weak"

    def test_an_accuracy_is_reported(self, now):
        exercises = {"a": _Exercise("a", ["git"])}
        states = {
            "a": ReviewState(
                "a",
                reps=3,
                lapses=1,
                due_at=(now + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
            )
        }
        rows = concept_breakdown(exercises, set(), states, now)
        assert rows[0]["accuracy"] == 0.75
