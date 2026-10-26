"""Tests for the authoring tools.

The repair scripts exist because plain YAML scalars have sharp edges, and a
tool that rewrites what it does not fully understand will quietly corrupt
content. These tests check that they fix the one thing they understand and
leave everything else alone.
"""

import socket
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"

sys.path.insert(0, str(SCRIPTS))

import serve  # noqa: E402
from fix_multiline import fix_file as join_file  # noqa: E402
from fix_quoting import fix_file as quote_file  # noqa: E402
from lint_content import lint_file  # noqa: E402


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


class TestLinter:
    def test_clean_content_passes(self, tmp_path):
        path = tmp_path / "track.yaml"
        _write(
            path,
            "id: t\ntitle: T\nlessons:\n"
            "  - id: l\n    title: L\n    body: |\n"
            "      # A heading\n\n"
            "      Some prose.\n"
            "    exercises:\n"
            "      - id: e\n        title: E\n        prompt: |\n"
            "          Do the thing.\n"
            "        starter: |\n"
            "          # Your commands here.\n"
            "        solution: |\n"
            "          git status\n"
            "        checks:\n"
            "          - name: c\n            kind: command\n            command: 'true'\n"
            "        hints:\n"
            "          - |\n"
            "            A hint.\n",
        )
        assert lint_file(path) == []

    def test_a_backtick_starting_a_scalar_is_reported(self, tmp_path):
        path = tmp_path / "track.yaml"
        _write(path, "id: t\nhints:\n  - `git status` is the command\n")
        issues = lint_file(path)
        assert any("reserved" in issue for issue in issues)

    def test_a_colon_space_in_a_scalar_is_left_to_the_parser(self, tmp_path):
        """`- the catch: it truncates` parses as a list holding a mapping, so
        a regex cannot tell it from a real nested mapping and the linter
        stays quiet. The loader is what rejects it."""
        path = tmp_path / "track.yaml"
        _write(path, "id: t\nhints:\n  - the catch: it truncates\n")
        issues = lint_file(path)
        assert not any("': '" in issue for issue in issues)

    def test_a_parse_error_is_reported(self, tmp_path):
        path = tmp_path / "track.yaml"
        _write(path, "id: t\n  bad: [unclosed\n")
        issues = lint_file(path)
        assert any("PARSE ERROR" in issue for issue in issues)


class TestQuoting:
    def test_a_reserved_scalar_is_quoted(self, tmp_path):
        path = tmp_path / "track.yaml"
        _write(path, "id: t\nhints:\n  - `git status` is the command\n")
        changed = quote_file(path, write=True)
        assert changed
        text = path.read_text()
        assert '"' in text
        assert yaml.safe_load(text)["hints"] == ["`git status` is the command"]

    def test_an_already_quoted_scalar_is_left_alone(self, tmp_path):
        path = tmp_path / "track.yaml"
        original = "id: t\nhints:\n  - \"`git status` is the command\"\n"
        _write(path, original)
        assert quote_file(path, write=True) == []
        assert path.read_text() == original

    def test_a_nested_mapping_is_left_alone(self, tmp_path):
        """`- id: f-foo` is a mapping, not a scalar. Quoting it would break
        the content."""
        path = tmp_path / "track.yaml"
        original = "id: t\nexercises:\n  - id: f-foo\n    title: Foo\n"
        _write(path, original)
        assert quote_file(path, write=True) == []
        assert path.read_text() == original

    def test_a_block_scalar_is_left_alone(self, tmp_path):
        path = tmp_path / "track.yaml"
        original = "id: t\nbody: |\n  `git status` is the command\n"
        _write(path, original)
        assert quote_file(path, write=True) == []
        assert path.read_text() == original


class TestJoining:
    def test_a_wrapped_item_is_joined(self, tmp_path):
        path = tmp_path / "track.yaml"
        _write(path, "id: t\nhints:\n  - the first part of the\n    second part\n")
        changed = join_file(path, write=True)
        assert changed
        text = path.read_text()
        assert "the first part of the second part" in text
        assert yaml.safe_load(text)["hints"] == ["the first part of the second part"]

    def test_a_block_scalar_is_left_alone(self, tmp_path):
        path = tmp_path / "track.yaml"
        original = "id: t\nbody: |\n  the first part\n  the second part\n"
        _write(path, original)
        assert join_file(path, write=True) == []
        assert path.read_text() == original

    def test_an_already_quoted_item_is_left_alone(self, tmp_path):
        """Joining a quoted fragment would put the closing quote in the
        middle of the sentence. That is a job for a human."""
        path = tmp_path / "track.yaml"
        original = 'id: t\nhints:\n  - "the first part"\n    the second part\n'
        _write(path, original)
        assert join_file(path, write=True) == []


class TestServeScript:
    """The start script has to find a free port, or it is no use."""

    def test_a_free_port_is_found(self):
        port, taken = serve.find_port(0)
        assert port is not None
        assert not taken

    def test_a_busy_port_is_skipped(self):
        """The whole point of the script."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", 0))
            sock.listen(1)
            busy = sock.getsockname()[1]

            port, taken = serve.find_port(busy)
            assert port > busy
            assert busy in taken

    def test_several_busy_ports_are_skipped(self):
        ports = []
        socks = []
        for _ in range(3):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", 0))
            sock.listen(1)
            socks.append(sock)
            ports.append(sock.getsockname()[1])

        try:
            # The three sockets are on consecutive-ish ports, so the search
            # must step over them rather than stopping at the first.
            first = min(ports)
            port, taken = serve.find_port(first)
            assert port > first
            assert first in taken
        finally:
            for sock in socks:
                sock.close()

    def test_a_busy_host_address_is_not_free(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", 0))
            sock.listen(1)
            port = sock.getsockname()[1]
            assert serve.is_free(port, "127.0.0.1") is False

    def test_the_wildcard_address_is_not_shown_as_a_url(self):
        """0.0.0.0 is the address to bind, not one to visit."""
        assert serve._display_host("0.0.0.0") == "127.0.0.1"
        assert serve._display_host("127.0.0.1") == "127.0.0.1"

    def test_a_chosen_port_is_honoured(self, capsys):
        """--port moves the starting point, and the port that comes back is
        the one that will be used."""
        assert serve.main(["--port", "8123", "--dry-run"]) == 0
        out = capsys.readouterr().out.strip()
        assert out.isdigit()
        assert int(out) >= 8123

    def test_the_dry_run_prints_only_the_port(self, capsys):
        """stdout has to stay a clean port number, so that
        ``PORT=$(./start.sh --dry-run)`` works."""
        assert serve.main(["--dry-run"]) == 0
        assert capsys.readouterr().out.strip().isdigit()

    def test_the_git_check_agrees_with_reality(self):
        """The launcher must not claim git is missing when it is there, or
        the other way round."""
        ok, version = serve.git_status()
        assert ok == (version is not None)
        if ok and version:
            assert version.startswith("git version")

    def test_a_missing_git_is_reported_but_does_not_stop_the_server(
        self, monkeypatch, capsys
    ):
        """Without git the exercises cannot run, but the lessons can, so the
        launcher says so and carries on."""
        monkeypatch.setattr(serve, "git_status", lambda: (False, None))
        assert serve.main(["--dry-run"]) == 0
        captured = capsys.readouterr()
        assert "git was not found" in captured.err
        assert captured.out.strip().isdigit()


class TestScripts:
    """The scripts must at least run."""

    @pytest.mark.parametrize(
        "script",
        [
            "lint_content.py",
            "fix_quoting.py",
            "fix_multiline.py",
            "smoke.py",
            "tag_exercises.py",
            "serve.py",
        ],
    )
    def test_the_script_exists(self, script):
        assert (SCRIPTS / script).exists()

    def test_the_linter_runs_over_the_shipped_content(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "lint_content.py")],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "All curriculum files pass" in result.stdout
