"""Tests for the app database and the progress state machine.

Two separate things are tested here, and the split matters: the *schema*
(tests that the tables exist and survive a migration) and the *state
machine* (that solved and revealed stay apart).
"""


import pytest

from app.db import SCHEMA_VERSION, connect, init_db
from app.progress import (
    all_states,
    clear_review_state,
    get_review_state,
    get_state,
    record_attempt,
    record_reveal,
    reset_progress,
    save_review_state,
    schedule_review,
    solved_exercise_ids,
    summarise,
)
from app.srs import ReviewState


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Point the app's database at a throwaway file."""
    path = tmp_path / "test.db"
    monkeypatch.setattr("app.db.DATABASE_PATH", path)
    monkeypatch.setattr("app.config.DATABASE_PATH", path)
    init_db(path)
    return path


class TestSchema:
    def test_the_tables_exist(self, db):
        conn = connect(db)
        try:
            names = {
                row["name"]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        finally:
            conn.close()
        assert {"attempts", "exercise_progress", "review_state"} <= names

    def test_the_schema_version_is_recorded(self, db):
        conn = connect(db)
        try:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        finally:
            conn.close()

    def test_a_newer_schema_is_refused(self, db):
        """An app that understands less than the file it is given must say so
        rather than quietly corrupting it."""
        conn = connect(db)
        try:
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
            conn.commit()
        finally:
            conn.close()
        with pytest.raises(RuntimeError, match="newer than this app"):
            init_db(db)

    def test_progress_survives_a_reopen(self, db):
        record_attempt("e", "git status", True)
        init_db(db)
        assert get_state("e").status == "solved"


class TestProgress:
    def test_an_unattempted_exercise_is_unsolved(self, db):
        assert get_state("nope").status == "unsolved"

    def test_a_pass_solves(self, db):
        record_attempt("e", "git status", True)
        state = get_state("e")
        assert state.status == "solved"
        assert state.attempts == 1
        assert state.solved_at is not None

    def test_a_failure_does_not_solve(self, db):
        record_attempt("e", "git status", False)
        assert get_state("e").status == "unsolved"

    def test_a_reveal_is_not_a_solve(self, db):
        record_reveal("e")
        assert get_state("e").status == "revealed"

    def test_a_pass_after_a_reveal_stays_revealed(self, db):
        """Copying the answer and then reproducing it proves you can copy."""
        record_reveal("e")
        record_attempt("e", "git status", True)
        assert get_state("e").status == "revealed"

    def test_a_reveal_does_not_erase_a_solve(self, db):
        record_attempt("e", "git status", True)
        record_reveal("e")
        state = get_state("e")
        assert state.solved_at is not None
        assert state.status == "revealed"

    def test_revealing_twice_keeps_the_first_time(self, db):
        record_reveal("e")
        first = get_state("e").revealed_at
        record_reveal("e")
        assert get_state("e").revealed_at == first

    def test_only_independently_solved_exercises_count(self, db):
        record_attempt("solved", "x", True)
        record_reveal("revealed")
        record_attempt("revealed", "x", True)
        assert solved_exercise_ids() == {"solved"}

    def test_the_last_submission_is_kept(self, db):
        record_attempt("e", "first", False)
        record_attempt("e", "second", False)
        assert get_state("e").last_code == "second"

    def test_the_summary_separates_solved_from_revealed(self, db):
        record_attempt("a", "x", True)
        record_reveal("b")
        states = all_states()
        summary = summarise(states, ["a", "b", "c"])
        assert summary["solved"] == 1
        assert summary["revealed"] == 1
        assert summary["unsolved"] == 1
        assert summary["total"] == 3

    def test_the_summary_never_blends_solved_and_revealed(self, db):
        """A completion figure that includes copied answers is worse than no
        figure at all."""
        record_attempt("a", "x", True)
        record_reveal("b")
        summary = summarise(all_states(), ["a", "b"])
        assert summary["solve_rate"] == 0.5

    def test_reset_clears_everything(self, db):
        record_attempt("a", "x", True)
        record_reveal("b")
        schedule_review("a", True)
        reset_progress()
        assert all_states() == {}
        assert get_review_state("a").due_at is None


class TestReviewState:
    def test_a_pass_is_scheduled(self, db):
        state = schedule_review("e", True)
        assert state.due_at is not None
        assert state.reps == 1

    def test_a_revealed_exercise_is_unscheduled(self, db):
        schedule_review("e", True)
        record_reveal("e")
        assert get_review_state("e").due_at is None

    def test_clearing_removes_the_row(self, db):
        save_review_state(ReviewState("e", due_at="2026-01-01 00:00:00"))
        clear_review_state("e")
        assert get_review_state("e").due_at is None
