"""Tests for the comparison rules.

The comparison is semantic, and these tests are what stop it drifting into
being textual. They matter because the difference is invisible to a learner:
being told "wrong" for spelling a commit message differently is exactly the
experience this app is trying not to have.
"""


from app.engine.compare import (
    ComparisonSettings,
    compare_checks,
    compare_output,
    compare_values,
)
from app.engine.values import describe, json_equal, normalise_lines, truncate


class TestValues:
    def test_dict_key_order_is_ignored(self):
        result = compare_values({"a": 1, "b": 2}, {"b": 2, "a": 1})
        assert result.passed

    def test_list_order_matters_by_default(self):
        result = compare_values([1, 2], [2, 1])
        assert not result.passed
        assert "expected" in result.detail

    def test_list_order_can_be_ignored(self):
        settings = ComparisonSettings(order_sensitive=False)
        result = compare_values([1, 2], [2, 1], settings)
        assert result.passed

    def test_true_is_not_one(self):
        result = compare_values({"current": True}, {"current": 1})
        assert not result.passed
        assert result.reason == "type"

    def test_null_matches_only_null(self):
        assert compare_values(None, None).passed
        assert not compare_values(None, 0).passed
        assert not compare_values(None, "").passed

    def test_numbers_compare_numerically(self):
        assert compare_values(1, 1.0).passed

    def test_numbers_allow_a_tolerance(self):
        settings = ComparisonSettings(float_tolerance=0.5)
        assert compare_values(1.0, 1.4, settings).passed
        assert not compare_values(1.0, 2.0, settings).passed

    def test_a_missing_key_is_reported(self):
        result = compare_values({"a": 1, "b": 2}, {"a": 1})
        assert not result.passed
        assert result.reason == "missing_key"
        assert "b" in result.detail

    def test_an_extra_key_is_reported(self):
        result = compare_values({"a": 1}, {"a": 1, "b": 2})
        assert not result.passed
        assert result.reason == "extra_key"

    def test_the_path_names_the_difference(self):
        result = compare_values(
            {"commits": [{"subject": "one"}]}, {"commits": [{"subject": "two"}]}
        )
        assert not result.passed
        assert "commits[0].subject" in result.detail

    def test_an_ignored_path_is_not_compared(self):
        settings = ComparisonSettings(ignore_paths=["noise"])
        result = compare_values({"noise": 1, "real": 2}, {"noise": 9, "real": 2}, settings)
        assert result.passed

    def test_a_list_length_difference_is_reported(self):
        result = compare_values([1, 2, 3], [1])
        assert not result.passed
        assert result.reason == "length"


class TestOutput:
    def test_trailing_whitespace_is_ignored(self):
        assert compare_output("a  \nb\n", "a\nb").passed

    def test_trailing_blank_lines_are_ignored(self):
        assert compare_output("a\n\n\n", "a\n").passed

    def test_a_missing_line_is_not_forgiven(self):
        assert not compare_output("a\nb\n", "a\n").passed

    def test_exact_mode_forgives_nothing(self):
        assert not compare_output("a  \n", "a\n", mode="exact").passed

    def test_json_mode_compares_by_value(self):
        assert compare_output('{"a":1,"b":2}', '{"b":2,"a":1}', mode="json").passed

    def test_json_mode_rejects_invalid_json(self):
        result = compare_output("not json", "not json", mode="json")
        assert not result.passed
        assert "not valid JSON" in result.detail


class TestChecks:
    def _command(self, stdout: str, exit_code: int = 0) -> dict:
        return {"stdout": stdout, "stderr": "", "exit_code": exit_code}

    def test_a_matching_check_passes(self):
        expected = [{"name": "c", "kind": "command", "result": self._command("x\n")}]
        actual = [{"name": "c", "kind": "command", "result": self._command("x\n")}]
        result = compare_checks(expected, actual)
        assert result.passed
        assert result.passed_count == 1
        assert result.total_count == 1

    def test_a_different_exit_code_fails(self):
        expected = [{"name": "c", "kind": "command", "result": self._command("", 0)}]
        actual = [{"name": "c", "kind": "command", "result": self._command("", 1)}]
        result = compare_checks(expected, actual)
        assert not result.passed
        assert "exited with 1" in result.checks[0].detail

    def test_a_missing_check_fails(self):
        expected = [{"name": "c", "kind": "command", "result": self._command("x")}]
        result = compare_checks(expected, [])
        assert not result.passed
        assert "did not run" in result.checks[0].detail

    def test_no_checks_is_not_a_pass(self):
        result = compare_checks([], [])
        assert not result.passed
        assert result.reason == "no_checks"

    def test_the_summary_names_the_first_failure(self):
        expected = [
            {"name": "first", "kind": "command", "result": self._command("a")},
            {"name": "second", "kind": "command", "result": self._command("b")},
        ]
        actual = [
            {"name": "first", "kind": "command", "result": self._command("x")},
            {"name": "second", "kind": "command", "result": self._command("b")},
        ]
        result = compare_checks(expected, actual)
        assert not result.passed
        assert "first" in result.detail
        assert result.passed_count == 1

    def test_a_state_check_shows_the_value_it_compared(self):
        expected = [
            {
                "name": "branches",
                "kind": "state",
                "result": {"value": [{"name": "main", "current": True}]},
            }
        ]
        actual = [
            {
                "name": "branches",
                "kind": "state",
                "result": {"value": [{"name": "main", "current": True}]},
            }
        ]
        result = compare_checks(expected, actual)
        assert result.passed
        assert "main" in result.checks[0].expected

    def test_a_state_check_shows_the_difference(self):
        expected = [
            {
                "name": "branches",
                "kind": "state",
                "result": {"value": [{"name": "main"}]},
            }
        ]
        actual = [
            {
                "name": "branches",
                "kind": "state",
                "result": {"value": [{"name": "master"}]},
            }
        ]
        result = compare_checks(expected, actual)
        assert not result.passed
        assert "main" in result.checks[0].expected
        assert "master" in result.checks[0].actual

    def test_the_reference_defines_the_order(self):
        """A learner cannot reorder the checks to make a diff read better."""
        expected = [
            {"name": "first", "kind": "command", "result": self._command("a")},
            {"name": "second", "kind": "command", "result": self._command("b")},
        ]
        actual = [
            {"name": "second", "kind": "command", "result": self._command("b")},
            {"name": "first", "kind": "command", "result": self._command("a")},
        ]
        result = compare_checks(expected, actual)
        assert not result.passed


class TestHelpers:
    def test_describe_renders_readably(self):
        assert describe(None) == "null"
        assert describe(True) == "true"
        assert describe(1.5) == "1.5"
        assert describe("x") == "x"
        assert describe([1, 2]) == "[1, 2]"

    def test_describe_truncates(self):
        assert "more characters" in describe("x" * 5000, limit=10)

    def test_truncate_says_so(self):
        assert "more characters" in truncate("x" * 5000, limit=10)

    def test_normalise_lines_drops_trailing_blanks(self):
        assert normalise_lines("a\n\n") == ["a"]

    def test_json_equal_treats_one_and_one_point_zero_as_equal(self):
        assert json_equal(1, 1.0)
        assert not json_equal(1, True)
