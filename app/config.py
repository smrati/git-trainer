"""Application settings.

The limits below are the engine's ceilings:

* ``COMMAND_TIMEOUT_SECONDS``  -- wall clock for one command in a sandbox.
* ``SCRIPT_TIMEOUT_SECONDS``   -- wall clock for one whole submission.
* ``OUTPUT_LIMIT_BYTES``       -- how much stdout/stderr may reach the browser.
* ``MAX_SEED_FILES``           -- how many files a seed may create.
* ``MAX_SEED_FILE_BYTES``      -- how large one seeded file may be.

The wall clock is the *primary* guard, for the same reason the Python trainer
made time rather than a step budget the primary guard: time bounds work, and
"it stopped after 5 seconds" is a message a learner can act on.
"""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CONTENT_DIR = BASE_DIR / "content"
STATIC_DIR = Path(__file__).resolve().parent / "static"
DATABASE_PATH = BASE_DIR / "data" / "git_trainer.db"

# --------------------------------------------------------------------------
# Timeouts
# --------------------------------------------------------------------------

# Wall clock for one command inside a sandbox. A git command that has not
# finished in a few seconds is a command the learner got wrong -- usually a
# `git commit` that opened an editor and is waiting for input.
COMMAND_TIMEOUT_SECONDS = 10.0

# Wall clock for a whole submission: the learner's script, every command in
# it, and the reference script. Longer because a script may make twenty
# commits and an exercise may have several checks.
SCRIPT_TIMEOUT_SECONDS = 45.0

# Wall clock for seeding a repository. Seeding is `git init` plus a handful
# of commits, so it is fast; the limit exists to catch a seed that asks for
# something pathological.
SEED_TIMEOUT_SECONDS = 30.0

# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

# How much stdout/stderr may be captured from one command. Beyond this the
# command is stopped and the UI says so, rather than the server holding a
# learner's accidental gigabyte of output in memory.
OUTPUT_LIMIT_BYTES = 100_000

# How many failing checks are shown in a grading diff. More than this and the
# shape is off rather than a near miss -- the message says so instead of
# showing an arbitrary slice.
DIFF_CHECK_LIMIT = 8

# The longest rendering of a value shown in a diff before it is cut short.
MAX_VALUE_CHARS = 4000

# Default tolerance for float comparison, used when a check does not set one.
DEFAULT_FLOAT_TOLERANCE = 1e-9

# Relative tolerance for measurements that are allowed to differ -- a file
# size, a line count. These are compared as a share of the reference.
DEFAULT_RELATIVE_TOLERANCE = 0.05

# --------------------------------------------------------------------------
# Submissions and seeds
# --------------------------------------------------------------------------

# The maximum number of files a seed may create. A seed is a repository
# skeleton, not a data dump.
MAX_SEED_FILES = 40

# The maximum size of a single seeded file, in bytes.
MAX_SEED_FILE_BYTES = 256 * 1024

# The maximum number of setup commands a seed may run.
MAX_SEED_COMMANDS = 60

# The maximum length of a learner's script, in characters. A submission is a
# handful of git commands; this is generous.
MAX_SCRIPT_CHARS = 100_000

# The maximum number of commands in one learner script. A script with more
# than this is not an exercise answer, it is a batch job.
MAX_SCRIPT_COMMANDS = 200

# --------------------------------------------------------------------------
# Git
# --------------------------------------------------------------------------

# The git executable. Overridable so a test can point at a wrapper.
GIT_BIN = "git"

# The identity every commit in a sandbox is made with.
#
# Two jobs. First, determinism: a commit's hash depends on its author, its
# committer and their timestamps, so without pinning them two runs of the
# same script produce different hashes. Second, isolation: the sandbox must
# not read the machine owner's `~/.gitconfig`, which may set a `commit.gpgsign`
# that would make every commit fail, or a `core.hooksPath` that would run the
# owner's hooks.
SANDBOX_USER_NAME = "Trainer"
SANDBOX_USER_EMAIL = "trainer@example.invalid"

# The timestamps every commit in a sandbox is made with. A constant, so a
# commit's hash is a function of its content and its ancestry alone.
SANDBOX_TIMESTAMP = "2024-01-01T00:00:00+00:00"

# The default branch name a sandbox repository starts on.
DEFAULT_BRANCH = "main"

# The prefix every sandbox directory is created under. A directory whose name
# starts with this is known to belong to the trainer and may be removed.
SANDBOX_PREFIX = "gittrainer-"
