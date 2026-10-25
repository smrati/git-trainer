"""Tests for the HTTP API.

These exercise the contract the frontend depends on, so a rename or shape
change breaks here rather than silently in the browser.

The endpoints that really run a repository are marked ``slow``. The
read-only endpoints are never skipped: they are the ones a learner hits
first, and they must work even on a machine with no git at all.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app

# An exercise with one command and one check: the cheapest possible real run.
EXERCISE = "f-commit-the-staged-file"

SOLUTION = 'git commit -m "Add the notes"'


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A client backed by a throwaway app database."""
    monkeypatch.setattr("app.db.DATABASE_PATH", tmp_path / "test.db")
    monkeypatch.setattr("app.config.DATABASE_PATH", tmp_path / "test.db")
    with TestClient(app) as c:
        yield c


class TestHealth:
    def test_reports_the_loaded_curriculum(self, client):
        data = client.get("/api/health").json()
        assert data["status"] == "ok"
        assert data["tracks"] >= 3
        assert data["exercises"] >= 40

    def test_reports_git(self, client):
        """Git's state is reported rather than enforced: the lessons still
        work without it."""
        data = client.get("/api/health").json()
        assert "available" in data["git"]
        assert isinstance(data["git"]["available"], bool)

    def test_counts_the_checks(self, client):
        data = client.get("/api/health").json()
        assert data["checks"] >= data["exercises"]


class TestCurriculum:
    def test_the_tree_lists_tracks_and_exercises(self, client):
        data = client.get("/api/curriculum").json()
        assert len(data["tracks"]) >= 3
        first = data["tracks"][0]
        assert first["difficulty"]
        assert first["exercise_count"] == sum(
            lesson["exercise_count"] for lesson in first["lessons"]
        )
        exercise = first["lessons"][0]["exercises"][0]
        assert set(exercise) >= {"id", "title", "status", "attempts", "kind"}

    def test_the_summary_starts_empty(self, client):
        summary = client.get("/api/curriculum").json()["summary"]
        assert summary["solved"] == 0
        assert summary["revealed"] == 0
        assert summary["total"] > 0

    def test_an_unknown_lesson_is_404(self, client):
        assert client.get("/api/lessons/nope").status_code == 404

    def test_an_unknown_exercise_is_404(self, client):
        assert client.get("/api/exercises/nope").status_code == 404

    def test_a_404_names_what_was_missing(self, client):
        response = client.get("/api/exercises/nope")
        assert "nope" in response.json()["detail"]


class TestLesson:
    def test_a_lesson_carries_its_text_and_exercises(self, client):
        data = client.get("/api/lessons/f-what-is-a-repository").json()
        assert data["title"]
        assert data["body"]
        assert data["exercises"]
        assert "prompt" in data["exercises"][0]

    def test_a_lesson_never_carries_a_solution(self, client):
        data = client.get("/api/lessons/f-what-is-a-repository").json()
        assert "solution" not in data["exercises"][0]
        assert "solution" not in data


class TestExercise:
    def test_an_exercise_carries_its_prompt_and_starter(self, client):
        data = client.get(f"/api/exercises/{EXERCISE}").json()
        assert data["prompt"]
        assert data["starter"]
        assert data["checks"]
        assert data["check_count"] == len(data["checks"])

    def test_an_exercise_never_carries_its_solution(self, client):
        data = client.get(f"/api/exercises/{EXERCISE}").json()
        assert "solution" not in data

    def test_the_check_names_are_visible_but_not_their_values(self, client):
        """Knowing what is checked is the specification. Knowing the expected
        value would be the answer."""
        data = client.get(f"/api/exercises/{EXERCISE}").json()
        for check in data["checks"]:
            assert set(check) >= {"name", "kind"}
            assert "expected" not in check


class TestHints:
    def test_a_hint_is_one_at_a_time(self, client):
        data = client.get(f"/api/exercises/{EXERCISE}").json()
        first = client.get(f"/api/exercises/{EXERCISE}/hints/0").json()
        assert first["index"] == 0
        assert first["text"]
        assert first["total"] == data["hint_count"]

    def test_an_out_of_range_hint_is_404(self, client):
        assert client.get(f"/api/exercises/{EXERCISE}/hints/99").status_code == 404


@pytest.mark.slow
class TestRun:
    def test_a_run_does_not_grade(self, client):
        data = client.post(f"/api/exercises/{EXERCISE}/run", json={"script": SOLUTION}).json()
        assert data["ok"]
        assert data["grading"] is None

    def test_a_run_reports_the_transcript(self, client):
        data = client.post(f"/api/exercises/{EXERCISE}/run", json={"script": SOLUTION}).json()
        assert "notes.txt" in data["transcript"]

    def test_a_run_reports_the_repository_state(self, client):
        data = client.post(f"/api/exercises/{EXERCISE}/run", json={"script": SOLUTION}).json()
        assert data["state"]["current_branch"] == "main"
        assert data["state"]["status"] == []

    def test_a_run_never_records_progress(self, client):
        client.post(f"/api/exercises/{EXERCISE}/run", json={"script": SOLUTION})
        summary = client.get("/api/curriculum").json()["summary"]
        assert summary["solved"] == 0
        assert summary["attempted"] == 0

    def test_an_empty_script_is_accepted(self, client):
        data = client.post(f"/api/exercises/{EXERCISE}/run", json={"script": ""}).json()
        assert data["ok"]


@pytest.mark.slow
class TestSubmit:
    def test_a_correct_answer_passes(self, client):
        data = client.post(f"/api/exercises/{EXERCISE}/submit", json={"script": SOLUTION}).json()
        assert data["ok"]
        assert data["grading"]["passed"]
        assert data["state"]["status"] == "solved"

    def test_a_wrong_answer_fails_with_a_diff(self, client):
        data = client.post(
            f"/api/exercises/{EXERCISE}/submit", json={"script": 'git commit -m "Wrong"'}
        ).json()
        assert data["ok"]
        assert not data["grading"]["passed"]
        assert data["grading"]["checks"]
        failed = [c for c in data["grading"]["checks"] if not c["passed"]]
        assert failed
        assert failed[0]["detail"]

    def test_a_wrong_answer_is_not_solved(self, client):
        client.post(f"/api/exercises/{EXERCISE}/submit", json={"script": 'git commit -m "Wrong"'})
        assert client.get("/api/progress").json()["summary"]["solved"] == 0

    def test_a_pass_schedules_a_review(self, client):
        data = client.post(f"/api/exercises/{EXERCISE}/submit", json={"script": SOLUTION}).json()
        assert data["review"]["scheduled"]
        assert data["review"]["due_at"]

    def test_a_failure_does_not_schedule_a_new_exercise(self, client):
        data = client.post(
            f"/api/exercises/{EXERCISE}/submit", json={"script": 'git commit -m "Wrong"'}
        ).json()
        assert not data["review"]["scheduled"]

    def test_the_attempt_is_recorded(self, client):
        client.post(f"/api/exercises/{EXERCISE}/submit", json={"script": SOLUTION})
        attempts = client.get("/api/progress").json()["recent_attempts"]
        assert attempts
        assert attempts[0]["exercise_id"] == EXERCISE


@pytest.mark.slow
class TestSolution:
    def test_revealing_returns_the_solution(self, client):
        data = client.post(f"/api/exercises/{EXERCISE}/solution").json()
        assert data["solution"].strip()
        assert data["teach"]

    def test_revealing_is_recorded_as_revealed(self, client):
        client.post(f"/api/exercises/{EXERCISE}/solution")
        state = client.get(f"/api/exercises/{EXERCISE}").json()
        assert state["status"] == "revealed"

    def test_solving_after_revealing_stays_revealed(self, client):
        client.post(f"/api/exercises/{EXERCISE}/solution")
        data = client.post(f"/api/exercises/{EXERCISE}/submit", json={"script": SOLUTION}).json()
        assert data["grading"]["passed"]
        assert data["state"]["status"] == "revealed"


@pytest.mark.slow
class TestPlayground:
    def test_the_playground_runs_commands(self, client):
        data = client.post("/api/playground/run", json={"script": "git log --oneline"}).json()
        assert data["ok"]
        assert "Add the project" in data["transcript"]

    def test_the_playground_never_grades(self, client):
        data = client.post("/api/playground/run", json={"script": "git status"}).json()
        assert data["grading"] is None

    def test_the_playground_records_nothing(self, client):
        client.post("/api/playground/run", json={"script": "git status"})
        assert client.get("/api/progress").json()["summary"]["attempted"] == 0

    def test_the_playground_can_start_from_an_exercise(self, client):
        data = client.post(
            "/api/playground/run",
            json={"script": "git log --format=%s", "exercise_id": EXERCISE},
        ).json()
        assert data["ok"]
        assert "Initial commit" in data["transcript"]


class TestProgress:
    def test_the_dashboard_starts_empty(self, client):
        data = client.get("/api/progress").json()
        assert data["summary"]["total"] > 0
        assert data["exercises"] == []
        assert data["recent_attempts"] == []

    def test_progress_can_be_reset(self, client):
        client.post(f"/api/exercises/{EXERCISE}/submit", json={"script": SOLUTION})
        assert client.get("/api/progress").json()["summary"]["solved"] == 1
        client.delete("/api/progress")
        assert client.get("/api/progress").json()["summary"]["solved"] == 0


class TestReview:
    def test_the_review_page_starts_empty(self, client):
        data = client.get("/api/review").json()
        assert data["counts"]["due_now"] == 0
        assert data["counts"]["scheduled"] == 0
        assert data["due"] == []

    def test_the_review_page_lists_the_concepts(self, client):
        data = client.get("/api/review").json()
        assert data["concepts"]
        for concept in data["concepts"]:
            assert concept["label"]
            assert concept["state"]

    def test_a_solved_exercise_becomes_due_later(self, client):
        client.post(f"/api/exercises/{EXERCISE}/submit", json={"script": SOLUTION})
        data = client.get("/api/review").json()
        assert data["counts"]["scheduled"] == 1
        assert data["counts"]["next_due_at"]

    def test_a_revealed_exercise_is_not_scheduled(self, client):
        client.post(f"/api/exercises/{EXERCISE}/solution")
        data = client.get("/api/review").json()
        assert data["counts"]["scheduled"] == 0


class TestPages:
    def test_the_front_end_is_served(self, client):
        for path in ("/", "/index.html", "/learn.html", "/practice.html", "/exercise.html"):
            assert client.get(path).status_code == 200

    def test_an_unknown_page_gets_the_shell(self, client):
        response = client.get("/nope")
        assert response.status_code == 200
        assert "<html" in response.text.lower()

    def test_an_unknown_api_path_gets_json(self, client):
        response = client.get("/api/nope")
        assert response.status_code == 404
        assert "detail" in response.json()
