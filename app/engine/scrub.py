"""Normalising output so that two runs can be compared.

Two runs of the same script are not identical, and three things make them
differ:

* **Commit hashes.** A hash is a function of the commit's content, its
  ancestry, its author, its committer and its timestamps. Two runs produce
  the same hash only if all of those agree -- and even when they do, the
  hash is not *the thing being taught*. Grading a hash would grade git's
  internals instead of git's behaviour, and would fail a learner who did
  exactly the right thing in a slightly different order.

* **Absolute paths.** The sandbox lives in a different temporary directory
  every time, so a path in the output is noise.

* **Timestamps.** The sandbox pins the clock for commits, but a reflog
  entry, a `git status` timestamp or a file's mtime is real time.

So all three are replaced with placeholders before anything is compared.
What is left is structure -- which is the skill.

The replacement is a *placeholder*, not a deletion, so a diff still shows
where a hash was, and two different hashes on opposite sides still differ
from each other in position even though the text matches.
"""

from __future__ import annotations

import re

# A full object name, or an abbreviated one.
#
# The abbreviated form is only matched when it contains a digit: a seven
# letter word made entirely of hex letters ("effaced") is a word, and a
# seven character abbreviation of a SHA is nearly always mixed.
_FULL_SHA = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{40}(?![0-9A-Fa-f])")
_SHORT_SHA = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{7,39}(?![0-9A-Fa-f])")

# ISO 8601, with or without a time part: "2024-01-01 00:00:00 +0000".
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?(?: ?[+-]\d{4})??")

# Git's own default date rendering: "Mon Jan 1 00:00:00 2024 +0000".
_GIT_DATE = re.compile(
    r"[A-Z][a-z]{2} [A-Z][a-z]{2} +\d{1,2} \d{2}:\d{2}:\d{2} \d{4}(?: [+-]\d{4})?"
)

# A Unix timestamp in seconds. Ten digits, starting with 1 or 2, so a small
# number in a diff is not mistaken for one.
_EPOCH = re.compile(r"(?<!\d)[12]\d{9}(?!\d)")

# A temporary directory path, for output that names one.
_TMP_PATH = re.compile(r"/tmp/[^\s'\"]+")

# The placeholder used for a hash.
SHA_PLACEHOLDER = "<sha>"

# The placeholder used for a path.
PATH_PLACEHOLDER = "<path>"


def _replace_shas(text: str) -> str:
    text = _FULL_SHA.sub(SHA_PLACEHOLDER, text)

    def _abbreviation(match: re.Match) -> str:
        """An abbreviated hash, unless it is a word made of hex letters."""
        candidate = match.group(0)
        if any(character.isdigit() for character in candidate):
            return SHA_PLACEHOLDER
        return candidate

    return _SHORT_SHA.sub(_abbreviation, text)


def scrub_text(text: str, paths: dict[str, str] | None = None) -> str:
    """Replace hashes, dates and paths in ``text`` with placeholders.

    ``paths`` maps an absolute path to the placeholder that should stand in
    for it -- the sandbox's repository becomes ``<repo>`` and a remote
    becomes ``<remote>``, so a diff can say *which* repository it is talking
    about rather than showing a temporary directory nobody recognises.
    """
    if not text:
        return text

    for original, replacement in (paths or {}).items():
        if original:
            text = text.replace(original, replacement)

    text = _replace_shas(text)
    text = _GIT_DATE.sub("<date>", text)
    text = _ISO_DATE.sub("<date>", text)
    text = _EPOCH.sub("<epoch>", text)
    text = _TMP_PATH.sub(PATH_PLACEHOLDER, text)
    return text


def scrub_value(value, paths: dict[str, str] | None = None):
    """Scrub every string inside a JSON-shaped value."""
    if isinstance(value, str):
        return scrub_text(value, paths)
    if isinstance(value, list):
        return [scrub_value(item, paths) for item in value]
    if isinstance(value, dict):
        return {key: scrub_value(item, paths) for key, item in value.items()}
    return value
