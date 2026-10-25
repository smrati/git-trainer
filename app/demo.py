"""The repository the playground starts from.

A small, ordinary project: a README, a script, a test, and a directory that
exists only to be ignored. It is enough to try `git status`, `git add`,
`git commit`, `git log`, `git branch` and `git diff` against, and it has a
history already, so `git log` has something to show.
"""

from app.engine.sandbox import Seed

DEMO_FILES = {
    "README.md": (
        "# demo\n"
        "\n"
        "A tiny repository for trying git commands in.\n"
        "\n"
        "Try `git status`, `git log --oneline`, `git branch`, then change a\n"
        "file and look at `git diff`.\n"
    ),
    "main.py": (
        '"""A tiny program."""\n'
        "\n"
        "\n"
        "def greet(name: str) -> str:\n"
        '    return f"Hello, {name}!"\n'
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        '    print(greet("world"))\n'
    ),
    "tests/test_main.py": (
        "from main import greet\n"
        "\n"
        "\n"
        "def test_greet():\n"
        '    assert greet("world") == "Hello, world!"\n'
    ),
}

DEMO_COMMANDS = [
    "git add README.md main.py tests/test_main.py",
    'git commit -m "Add the project"',
]


def demo_seed() -> Seed:
    """The playground's starting repository."""
    return Seed(files=dict(DEMO_FILES), commands=list(DEMO_COMMANDS))
