"""Join multi-line plain scalars into one line.

A list item written as prose often wraps:

    hints:
      - `git init` creates the `.git` directory. It does not commit
        anything.

YAML reads that as one plain scalar continued across lines, which is
correct -- but it makes the item fragile: a plain scalar cannot contain
``: `` and cannot begin with a reserved character, so the moment somebody
edits the first word the file stops parsing. The linter reports the result;
this puts the text on one line and quotes it if it needs quoting.

Only *list items* are touched. Block scalars (``|`` and ``>-``) are left
alone, because their whole purpose is to be several lines, and a nested
mapping (``- id: f-foo``) is left alone because it is a different kind of
node entirely.

**Run this before ``fix_quoting.py``, not after.** Quoting first puts a
closing quote in the middle of a wrapped item, and joining that is a job
for a human.

Better still: write prose in the curriculum as a block scalar, which has
none of these problems.

Usage:  uv run python scripts/fix_multiline.py [--check]
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fix_quoting import is_properly_quoted, needs_quoting, quote  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "content"

BLOCK_MARKERS = {"|", "|-", "|>", ">", ">-", ">+"}
_KEY_RE = re.compile(r"^([A-Za-z_][\w]*):(?:[ \t]+|$)")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _starts_a_node(text: str) -> bool:
    """Does this text begin a new YAML node rather than continue a scalar?"""
    return text.startswith("-") or bool(_KEY_RE.match(text))


def _block_end(lines: list[str], index: int, base: int) -> int:
    """The index just past the end of a block scalar starting at ``index``."""
    cursor = index + 1
    while cursor < len(lines):
        current = lines[cursor]
        if current.strip() and _indent(current) <= base:
            break
        cursor += 1
    return cursor


def _item_pieces(lines: list[str], index: int) -> tuple[list[str], int]:
    """The pieces of a list item and the index just past it.

    A list item continues onto the following lines while they are indented
    deeper and do not start a new YAML node.
    """
    base = _indent(lines[index])
    pieces = [lines[index].strip()[2:].strip()]
    cursor = index + 1
    while cursor < len(lines):
        current = lines[cursor]
        if not current.strip():
            break
        if _indent(current) <= base:
            break
        text = current.strip()
        if _starts_a_node(text):
            break
        pieces.append(text)
        cursor += 1
    return pieces, cursor


def fix_file(path: Path, write: bool) -> list[int]:
    """Join the wrapped list items in one file. Returns the lines changed."""
    lines = path.read_text(encoding="utf-8").splitlines()
    changed: list[int] = []
    out: list[str] = []
    index = 0

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        # A block scalar's content is skipped whole.
        match = _KEY_RE.match(stripped)
        marker = stripped.split(":", 1)[1].strip() if match else ""
        if match and marker in BLOCK_MARKERS:
            end = _block_end(lines, index, _indent(line))
            out.extend(lines[index:end])
            index = end
            continue

        if not stripped.startswith("- ") or stripped.startswith("- |"):
            out.append(line)
            index += 1
            continue

        pieces, cursor = _item_pieces(lines, index)

        if len(pieces) == 1:
            out.append(line)
            index += 1
            continue

        if is_properly_quoted(pieces[0]):
            # The first fragment has already been quoted, so joining it with
            # the rest would put the closing quote in the middle of the
            # sentence. This needs a human: run the joiner *before* the
            # quoter, not after.
            print(
                f"  {path}:{index + 1}: already quoted -- left alone. "
                "Run fix_multiline.py before fix_quoting.py."
            )
            out.append(line)
            index += 1
            continue

        joined = " ".join(piece for piece in pieces if piece)
        if needs_quoting(joined):
            joined = quote(joined)
        out.append(" " * _indent(line) + "- " + joined)
        changed.append(index + 1)
        index = cursor

    if write and changed:
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    total = 0
    for path in sorted(CONTENT.rglob("*.yaml")):
        changed = fix_file(path, write=not args.check)
        total += len(changed)
        if changed:
            verb = "would join" if args.check else "joined"
            print(f"{path.relative_to(ROOT)}: {verb} item(s) at line(s) {changed}")
        else:
            print(f"ok  {path.relative_to(ROOT)}")

    print(f"\n{total} item(s) {'need joining' if args.check else 'joined'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
