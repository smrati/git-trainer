"""Tests for the engine: the sandbox, the state readers, and the scrubber.

These run real git in real temporary repositories. It is fast -- a git
command is a few milliseconds -- and it is the only way to be sure the
sandbox is hermetic and the state readers tell the truth.
"""

from pathlib import Path

import pytest

from app.engine.git import EngineError, build_sandbox_env, run_command, run_git
from app.engine.probe import run_check_command, run_script
from app.engine.sandbox import Seed, SeedError, create_sandbox, materialise, safe_relative_path
from app.engine.scrub import scrub_text, scrub_value
from app.engine.state import StateError, read_state


@pytest.fixture
def sandbox(tmp_path):
    """A sandbox with one commit and two files."""
    seed = Seed(
        files={"README.md": "# demo\n", "app.py": 'print("hello")\n'},
        commands=["git add .", 'git commit -m "Initial commit"'],
    )
    box = create_sandbox(seed, root=tmp_path / "box")
    yield box
    box.cleanup()


class TestSeed:
    def test_the_repository_is_created(self, sandbox):
        assert (sandbox.repo / ".git").is_dir()

    def test_the_files_are_there(self, sandbox):
        assert (sandbox.repo / "README.md").read_text() == "# demo\n"

    def test_the_commands_ran(self, sandbox):
        env = sandbox.env.values
        result = run_git(
            ["log", "--format=%s"], cwd=sandbox.repo, env=env
        )
        assert result.stdout.strip() == "Initial commit"

    def test_the_branch_is_main(self, sandbox):
        result = run_git(
            ["symbolic-ref", "--short", "HEAD"], cwd=sandbox.repo, env=sandbox.env.values
        )
        assert result.stdout.strip() == "main"

    def test_a_failing_seed_command_names_itself(self, tmp_path):
        seed = Seed(commands=["git commit -m 'no changes to commit'"])
        with pytest.raises(SeedError, match="no changes to commit"):
            create_sandbox(seed, root=tmp_path / "box")

    def test_init_false_leaves_no_repository(self, tmp_path):
        seed = Seed(init=False, files={"a.txt": "x\n"})
        box = create_sandbox(seed, root=tmp_path / "box")
        try:
            assert not (box.repo / ".git").exists()
            assert (box.repo / "a.txt").exists()
        finally:
            box.cleanup()

    def test_the_identity_is_pinned(self, tmp_path):
        """Two runs of the same seed must produce the same history."""
        seed = Seed(
            files={"a.txt": "x\n"},
            commands=["git add .", 'git commit -m "Same commit"'],
        )
        hashes = []
        for index in range(2):
            box = create_sandbox(seed, root=tmp_path / f"box{index}")
            try:
                result = run_git(
                    ["rev-parse", "HEAD"], cwd=box.repo, env=box.env.values
                )
                hashes.append(result.stdout.strip())
            finally:
                box.cleanup()
        assert hashes[0] == hashes[1], "the sandbox is not reproducible"

    def test_the_seed_environment_reaches_the_sandbox(self, tmp_path):
        """An exercise's own environment variables have to arrive: an
        interactive rebase is not scriptable without `GIT_SEQUENCE_EDITOR`."""
        seed = Seed(
            files={"a.txt": "x\n"},
            commands=["git add .", 'git commit -m "First"'],
            env={"GIT_SEQUENCE_EDITOR": "true", "TRAINER_MARKER": "yes"},
        )
        box = create_sandbox(seed, root=tmp_path / "box")
        try:
            assert box.env.values["TRAINER_MARKER"] == "yes"
            assert box.env.values["GIT_SEQUENCE_EDITOR"] == "true"
            # The sandbox's own settings are not displaced by the override.
            assert box.env.values["GIT_ALLOW_PROTOCOL"] == "file"
        finally:
            box.cleanup()


class TestPaths:
    def test_an_absolute_path_is_refused(self):
        with pytest.raises(SeedError, match="relative"):
            safe_relative_path("/etc/passwd")

    def test_a_parent_path_is_refused(self):
        with pytest.raises(SeedError, match=r"\.\."):
            safe_relative_path("../outside.txt")

    def test_a_backslash_is_refused(self):
        with pytest.raises(SeedError, match="backslash"):
            safe_relative_path("a\\b.txt")

    def test_a_nested_path_is_allowed(self):
        assert safe_relative_path("src/app/main.py") == Path("src/app/main.py")

    def test_materialise_creates_parents(self, tmp_path):
        materialise({"a/b/c.txt": "x"}, tmp_path)
        assert (tmp_path / "a" / "b" / "c.txt").read_text() == "x"


class TestIsolation:
    """The sandbox must not depend on, or be able to reach, the machine."""

    def test_the_machine_git_config_is_not_read(self, sandbox):
        """`git config --global user.name` must read the sandbox's file, not
        the machine owner's."""
        result = run_git(
            ["config", "--global", "user.name"], cwd=sandbox.repo, env=sandbox.env.values
        )
        assert result.stdout.strip() == "Trainer"

    def test_only_the_file_protocol_is_allowed(self, sandbox):
        """The protocol allow-list is an environment variable, not a config
        entry -- and it is the one that stops ``ext::`` and ``ssh``."""
        assert sandbox.env.values["GIT_ALLOW_PROTOCOL"] == "file"

    def test_the_home_directory_is_inside_the_sandbox(self, sandbox):
        home = Path(sandbox.env.values["HOME"])
        assert str(home).startswith(str(sandbox.root))

    def test_the_path_is_fixed(self, sandbox):
        assert sandbox.env.values["PATH"] == (
            "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        )


class TestScript:
    def test_a_script_runs_in_the_repository(self, sandbox):
        result = run_script("git status --porcelain", sandbox)
        assert result.exit_code == 0

    def test_the_trace_goes_to_stderr(self, sandbox):
        """The trace must not pollute stdout: an `output` check compares the
        script's stdout with the reference's."""
        result = run_script("git log --format=%s", sandbox)
        assert result.stdout.strip() == "Initial commit"
        assert "+ git log" in result.stderr

    def test_a_failing_command_is_reported(self, sandbox):
        result = run_script("git nonsense-command", sandbox)
        assert result.exit_code != 0

    def test_the_script_cannot_see_the_repository_as_a_file(self, sandbox):
        """The script is passed on stdin, so it leaves no file behind that
        `git status` would report."""
        result = run_script("true", sandbox)
        assert result.exit_code == 0
        status = run_check_command("git status --porcelain", sandbox)
        assert status.stdout.strip() == ""

    def test_a_command_runs_in_the_repository(self, sandbox):
        result = run_check_command("pwd", sandbox)
        assert result.stdout.strip() == str(sandbox.repo)


class TestState:
    def test_branches(self, sandbox):
        run_script("git branch feature", sandbox)
        branches = read_state("branches", str(sandbox.repo), sandbox.env.values)
        names = [b["name"] for b in branches]
        assert names == ["feature", "main"]
        current = [b for b in branches if b["current"]]
        assert len(current) == 1 and current[0]["name"] == "main"

    def test_status(self, sandbox):
        (sandbox.repo / "new.txt").write_text("x\n")
        status = read_state("status", str(sandbox.repo), sandbox.env.values)
        assert status == [{"index": "?", "worktree": "?", "path": "new.txt"}]

    def test_status_shows_a_staged_change(self, sandbox):
        (sandbox.repo / "new.txt").write_text("x\n")
        run_script("git add new.txt", sandbox)
        status = read_state("status", str(sandbox.repo), sandbox.env.values)
        assert status[0]["index"] == "A"
        assert status[0]["worktree"] == " "

    def test_log_describes_the_history_without_hashes(self, sandbox):
        log = read_state("log", str(sandbox.repo), sandbox.env.values)
        assert log["count"] == 1
        commit = log["commits"][0]
        assert commit["subject"] == "Initial commit"
        assert commit["parents"] == []
        assert commit["refs"] == ["HEAD -> main"]
        # No hash anywhere in the structure.
        assert "40" not in "".join(commit["refs"])

    def test_log_links_parents_by_index(self, sandbox):
        run_script('git commit --allow-empty -m "Second"', sandbox)
        log = read_state("log", str(sandbox.repo), sandbox.env.values)
        assert log["count"] == 2
        assert log["commits"][0]["subject"] == "Second"
        assert log["commits"][0]["parents"] == [1]
        assert log["commits"][1]["parents"] == []

    def test_log_keeps_a_merge_commit(self, sandbox):
        """History simplification must not hide a merge."""
        run_script(
            "git checkout -b side\n"
            'git commit --allow-empty -m "Side"\n'
            "git checkout main\n"
            'git commit --allow-empty -m "Main"\n'
            "git merge --no-ff side -m 'Merge side'\n",
            sandbox,
        )
        log = read_state("log", str(sandbox.repo), sandbox.env.values)
        subjects = [c["subject"] for c in log["commits"]]
        assert "Merge side" in subjects
        merge = next(c for c in log["commits"] if c["subject"] == "Merge side")
        assert len(merge["parents"]) == 2

    def test_tags(self, sandbox):
        run_script('git tag -a -m "Version one" v1.0', sandbox)
        tags = read_state("tags", str(sandbox.repo), sandbox.env.values)
        assert tags == [{"name": "v1.0", "type": "tag", "message": "Version one"}]

    def test_tracked_files(self, sandbox):
        files = read_state("tracked_files", str(sandbox.repo), sandbox.env.values)
        assert files == ["README.md", "app.py"]

    def test_remotes(self, sandbox):
        remotes = read_state("remotes", str(sandbox.repo), sandbox.env.values)
        assert remotes == []

    def test_an_unknown_selector_is_refused(self, sandbox):
        with pytest.raises(StateError, match="Unknown state selector"):
            read_state("nonsense", str(sandbox.repo), sandbox.env.values)

    def test_a_bare_repository_has_no_working_tree(self, tmp_path):
        seed = Seed(remotes={"origin": Seed(commands=['git commit --allow-empty -m "Remote"'])})
        box = create_sandbox(seed, root=tmp_path / "box")
        try:
            with pytest.raises(StateError, match="working tree"):
                read_state("status", str(box.remotes["origin"]), box.env.values, bare=True)
        finally:
            box.cleanup()


class TestRemotes:
    @pytest.fixture
    def remote_sandbox(self, tmp_path):
        seed = Seed(
            files={"README.md": "# demo\n"},
            commands=["git add .", 'git commit -m "Initial commit"'],
            remotes={
                "origin": Seed(
                    files={"extra.txt": "remote work\n"},
                    commands=["git add .", 'git commit -m "Remote work"'],
                )
            },
        )
        box = create_sandbox(seed, root=tmp_path / "box")
        yield box
        box.cleanup()

    def test_the_remote_shares_history(self, remote_sandbox):
        """The remote is a clone of the local repository, so the two are
        related and a merge between them is not an unrelated-histories
        error."""
        result = run_script(
            "git fetch origin\n"
            "git merge origin/main -m 'Merge remote'\n",
            remote_sandbox,
        )
        assert result.exit_code == 0
        assert "refusing to merge unrelated histories" not in result.stderr

    def test_the_remote_url_is_relative(self, remote_sandbox):
        remotes = read_state("remotes", str(remote_sandbox.repo), remote_sandbox.env.values)
        assert remotes[0]["fetch_url"] == "../remotes/origin"

    def test_the_remote_can_be_pushed_to(self, remote_sandbox):
        """The remote is *ahead* of the local repository, so a push needs the
        remote's work first -- which is the whole point of a remote."""
        result = run_script(
            "git fetch origin\n"
            "git merge origin/main -m 'Merge remote'\n"
            "git push origin main\n",
            remote_sandbox,
        )
        assert result.exit_code == 0, result.stderr
        log = read_state(
            "log", str(remote_sandbox.remotes["origin"]), remote_sandbox.env.values, bare=True
        )
        assert log["count"] == 2

    def test_the_remote_state_can_be_read(self, remote_sandbox):
        log = read_state(
            "log", str(remote_sandbox.remotes["origin"]), remote_sandbox.env.values, bare=True
        )
        assert log["commits"][0]["subject"] == "Remote work"


class TestScrub:
    def test_a_full_hash_is_replaced(self):
        assert scrub_text("abc" + "0123456789abcdef" * 2 + "def") == "<sha>"

    def test_a_short_hash_is_replaced(self):
        assert scrub_text("commit 1234567 here") == "commit <sha> here"

    def test_a_word_that_is_only_hex_letters_is_left_alone(self):
        assert scrub_text("effaced") == "effaced"

    def test_a_date_is_replaced(self):
        assert scrub_text("2024-01-01 00:00:00") == "<date>"

    def test_a_git_date_is_replaced(self):
        assert scrub_text("Mon Jan 1 00:00:00 2024 +0000") == "<date>"

    def test_a_path_is_replaced(self):
        assert scrub_text("at /tmp/whatever/x.txt") == "at <path>"

    def test_a_named_path_is_replaced(self):
        assert scrub_text("at /tmp/box/repo", {"/tmp/box/repo": "<repo>"}) == "at <repo>"

    def test_values_are_scrubbed_recursively(self):
        scrubbed = scrub_value({"a": ["0123456789abcdef0123456789abcdef01234567"]})
        assert scrubbed == {"a": ["<sha>"]}


class TestCommands:
    def test_a_missing_tool_is_reported(self, tmp_path):
        with pytest.raises(EngineError, match="not installed"):
            run_command(
                ["definitely-not-a-real-binary"],
                cwd=tmp_path,
                env={"PATH": "/usr/bin:/bin"},
                timeout=5,
            )

    def test_a_hanging_command_is_stopped(self, tmp_path):
        result = run_command(
            ["sleep", "30"], cwd=tmp_path, env={"PATH": "/usr/bin:/bin"}, timeout=1.0
        )
        assert result.timed_out
        assert result.exit_code == -1

    def test_the_environment_is_built_from_the_sandbox_root(self, tmp_path):
        env = build_sandbox_env(tmp_path)
        assert Path(env.values["HOME"]) == tmp_path / "home"
        assert env.values["GIT_CONFIG_GLOBAL"] == str(tmp_path / "gitconfig")
        assert (tmp_path / "gitconfig").exists()
