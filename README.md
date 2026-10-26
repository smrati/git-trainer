# Git Trainer

A full-stack web app for learning, practising and getting assessed on Git.
FastAPI backend, vanilla JavaScript frontend, SQLite for progress storage.
No build step, no bundler, no framework.

Built for working software professionals at any level: three tracks,
eighteen lessons and sixty-six graded exercises, from your first `git init`
to rebasing, bisecting, hooks and recovering lost work.

---

## Quick start

```bash
./start.sh
```

Then open <http://127.0.0.1:8000>.

`start.sh` finds a free port for you: it starts at 8000 and moves up until
one is free, so it does not matter whether something else has taken 8000.
It prints the URL it settled on.

```bash
./start.sh --port 9000       # start looking from 9000
./start.sh --reload          # restart on a code change
./start.sh --host 0.0.0.0    # reachable from your phone
```

`PORT` and `HOST` are read from the environment if you would rather set
them there.

The plain uvicorn command works too, if you would rather not have the
script in the way:

```bash
uv run uvicorn app.main:app --reload --port 9000
```

`start.sh` runs `uv` for you and creates the environment on first run, so
a fresh clone needs nothing but that one command.

**Git has to be installed.** The exercises are really run, in real
repositories, so without git there are no exercises. The lessons and the
exercise text still work without it, and the home page says so plainly.
`start.sh` says so too, at startup, and starts anyway -- it checks git the
way the engine runs it, so a git that the sandbox cannot see is reported
as missing even when it is on your path.

Run the tests:

```bash
uv run pytest -q
```

Lint the Python and the curriculum:

```bash
uv run ruff check app tests scripts
uv run python scripts/lint_content.py
```

Check the curriculum's concept tags, and verify that every reference
solution really works and every starter really fails:

```bash
uv run python scripts/tag_exercises.py
uv run python scripts/smoke.py
```

`smoke.py` is the important one. It runs every reference in the curriculum
in a real repository, checks it against its own checks, and then submits an
empty version of every starter and requires that to fail. It needs no
daemon and takes about half a minute.

### Browser checks

There is no bundler to complain that a module failed to load, so the
frontend is verified by driving a real headless Chrome over the DevTools
Protocol:

```bash
uv run uvicorn app.main:app --port 8100 &     # the checks drive a live server

uv run python scripts/browser_check.py --base http://127.0.0.1:8100
uv run python scripts/browser_flow.py         # drives port 8101
```

`browser_check` loads every page and fails on any console error, uncaught
exception or failed request. `browser_flow` then *uses* the app: opens a
lesson and checks the markdown rendered, writes git commands and runs them,
inspects the repository state, submits a correct answer and a wrong one,
reads the failure diff, reveals a solution, runs the playground and filters
the review queue.

This is not redundant with the pytest suite. The playground built the
*exercise's* repository as if it were a Dockerfile, because the throwaway
exercise it built had no entry file: the page rendered, every asset loaded,
every HTTP check passed, and the playground simply never built anything.
`test_every_api_method_used_exists` catches that class of mistake
statically; the flow checks catch what static analysis cannot.

---

## How it works

### Grading is by reference solution, not by a lookup table

Every exercise stores a **reference solution** -- a script of git commands
-- and a list of **checks**.

When you submit, the app seeds **two identical throwaway repositories**,
runs the reference script in one and your script in the other, and compares
the resulting repository state.

The reference's behaviour **is** the expected answer:

- for a check that runs a command, the output and the exit code;
- for a check that reads the repository, the whole structured value.

There is no hand-written expected output anywhere in this app, so there is
nothing that can drift out of date, and any correct implementation passes
however you wrote it.

The reference is computed once per exercise and cached.

### What an exercise is

An exercise is a **script**. You write the git commands that solve the
task; the grader runs them in a fresh copy of the exercise's starting
repository and looks at what you made of it.

```bash
git add app.py
git commit -m "Add the app"
```

There is no editor in the sandbox, so `git commit` needs `-m` and `git
revert` needs `--no-edit`. The script runs under `sh`, one command per
line, and every command is echoed before it runs, so the output reads as a
transcript.

### Three kinds of check

| Kind | What it does |
| --- | --- |
| `command` | runs a shell command in the sandbox repository; compares the exit code and the output |
| `state` | reads a named piece of the repository as JSON: branches, status, the history, tags, remotes, tracked files |
| `output` | compares **your script's output** with the reference's, for exercises whose answer is a command that prints something |

### Hashes are never graded

A commit's hash is a function of its content, its ancestry, its author and
its timestamps. Grading one would grade git's internals instead of git's
behaviour, and would fail a learner who did exactly the right thing in a
slightly different order.

So the sandbox pins the identity and the clock, and the comparison
**scrubs** hashes, absolute paths and dates before it looks at anything.
What is left is structure:

```
commits[0].subject: expected Add the app, got Add teh app
```

The `log` state describes the history by *structure*: each commit's
subject, its author, the refs pointing at it, and the indices of its
parents. Two histories that differ in their shape differ in the answer.

### The sandbox

Every exercise starts from a seed -- some files, some history, maybe a
remote -- and every run gets its own throwaway directory:

```
<sandbox>/
  repo/              the working repository; your cwd
  remotes/<name>/    a bare repository, for exercises about remotes
  home/              HOME, for anything that wants to write to it
  gitconfig          the sandbox's global configuration
```

A remote is a **clone of the local repository** with its own changes on
top, so the two share history and `git fetch`, `git merge origin/main` and
`git pull` behave the way they do in real life.

The sandbox is deleted the moment the run finishes. Nothing you type can
reach the next exercise.

### The trust model, stated plainly

This is a local, single-user teaching application, and the learner writes
shell commands that git runs. That is a real thing and it is not hidden:
the person running the app already has a shell and already has git.

What the app does anyway:

- **a fixed `PATH`**, so the machine owner's `PATH` cannot shadow `git`
  with something else;
- **`GIT_CONFIG_GLOBAL` and `GIT_CONFIG_SYSTEM` pointed at the sandbox**,
  so the owner's `~/.gitconfig` cannot set `commit.gpgsign` (which would
  make every commit fail without saying why), a `core.hooksPath`, or an
  identity;
- **`GIT_ALLOW_PROTOCOL=file`**, which removes git's remote-code-execution
  surface: `ext::` runs an arbitrary command, and `ssh` and `http` reach
  the network. A learner's script can only ever talk to the other
  repositories in its own sandbox;
- **`GIT_TERMINAL_PROMPT=0`**, so a credential prompt fails immediately
  instead of hanging until the wall clock kills it;
- **a `HOME` inside the sandbox**, so anything that writes to "the user's
  home" writes to a directory that is thrown away;
- **a wall clock** on every run -- a script that has not finished in forty
  five seconds is stopped;
- **output caps**, so an accidental gigabyte of printed output is truncated
  rather than held in memory;
- **paths in seeds are validated**: an absolute path, a `..` or a
  backslash is refused.

This is **not a sandbox in the security sense**. A determined script can
get out. What is here is a set of guards that catch accidents and remove
the obvious footguns; it is the same trade the Docker trainer makes for its
shell exercises, and it is documented as such.

If you ever deploy this for untrusted users, do not.

### `run` and `submit` are separate endpoints

`/run` executes your script and shows you what it did, plus the state of
the repository it left behind. It never grades and never records progress,
so experimenting cannot be mistaken for completing an exercise.

### The playground

A scratch terminal: any git commands you like, in a throwaway repository.
It never grades and never records anything. The exercise page has a
**"Try it in the playground"** button that opens it with *that exercise's*
starting repository, so you can explore the exact repository an exercise is
about without guessing what it contains.

### Solved and revealed are counted separately

Opening a solution is free, but it is recorded. There are three states:

- `unsolved` -- not completed
- `revealed` -- solution opened, explicitly **not** a solve
- `solved` -- passed with no prior reveal

Passing an exercise *after* revealing it leaves it `revealed`. The
dashboard reports solved and revealed side by side and never combines them
into one completion figure -- a percentage that silently includes copied
answers is worse than no percentage.

---

### Spaced repetition

Solving an exercise puts it into a review schedule with a widening
interval:

```
first pass          ->  1 day
second pass         ->  2.2 days
third               ->  4.8 days
...                 ->  capped at 180
```

A **failure is not a longer interval -- it is due immediately.** "Review"
therefore means "redo what you got wrong", which is the same feedback loop
the grader already runs. A failure resets stability to zero, so the next
pass earns a full day rather than topping up a 113-day interval.

Three things are deliberately *not* scheduled:

- **Revealed exercises.** Opening the answer and reproducing it proves you
  can copy, not that you retained anything. Revealing also removes the
  exercise from the schedule outright.
- **Failures of exercises you have never solved.** Failing something you
  are currently learning is not forgetting -- it is learning.
- **`run`, as opposed to `submit`.** Exploring is not recall.

Because a working developer thinks in topics rather than exercise ids,
exercises also carry concept tags (`staging`, `merge`, `rebase`, …) and the
review page aggregates per-concept strength. Concepts are ranked by
actionable work first -- how many are due -- then by pass rate. Deliberately
not a weighted composite score: a hidden formula is impossible to explain
to a learner and impossible to tune without real usage data.

## Layout

```
app/
  main.py              FastAPI app, static mount, SPA fallback
  config.py            limits and paths
  db.py                app database + migrations
  progress.py          solved/revealed state machine, review persistence
  srs.py               spaced-repetition scheduling (pure functions)
  grading.py           run -> compare -> record -> schedule
  demo.py              the playground's starting repository
  engine/
    git.py             the one place that shells out to git
    sandbox.py         a seed -> a throwaway repository
    probe.py           running a script, and running a check's command
    state.py           structured readers: branches, status, log, tags, ...
    scrub.py           normalising hashes, paths and dates
    compare.py         semantic comparison of values and checks
    values.py          values, rendered for a human
    runner.py          the parent: seed, run, compare, clean up
  curriculum/loader.py YAML loading and validation
  routers/             content.py (read), practice.py (run/submit/reveal),
                       review.py (due queue + concepts)
  static/              7 pages, 5 ES modules, one stylesheet
content/
  AUTHORING.md         how to write an exercise
  */track.yaml         tracks -> lessons -> exercises
scripts/               lint_content.py, fix_quoting.py, fix_indentation.py,
                       fix_multiline.py, tag_exercises.py, smoke.py,
                       serve.py, browser_check.py, browser_flow.py
start.sh               the launcher: finds a free port, runs the server
tests/
```

Two separate directories, deliberately: the app database holds progress,
while every repository a learner works in lives in a throwaway directory
under the system temporary directory. Nothing you type can reach your
progress.

Schema changes go through `MIGRATIONS` in `db.py` and are applied against
`PRAGMA user_version`; `tests/test_db.py` migrates a real v1 file and checks
the existing progress survives.

## Adding content

Read `content/AUTHORING.md` -- it is the full specification of an exercise.
The short version:

```yaml
- id: my-exercise
  title: Short title
  prompt: |
    What the learner must produce.
  kind: cli
  starter: |
    # Write your commands here, one per line.
  solution: |
    git add .
    git commit -m "Add the files"
  seed:
    files:
      README.md: |
        # demo
    commands:
      - git add .
      - git commit -m "Initial commit"
  checks:
    - name: the history has two commits
      kind: command
      command: git rev-list --count HEAD
  hints:
    - |
      First nudge.
  teach: |
    Why this works -- shown under "Why this works".
  tags: [commit, staging]
```

Everything is validated at startup: duplicate ids, an exercise with no
checks, a check kind the engine cannot run and a check that reads a remote
the seed does not create all fail loudly rather than surprising a learner
mid-exercise. `tests/test_curriculum.py` enforces this, plus that every
exercise has hints, an explanation, tags and a starter that does not
contain the answer.

### A note on authoring YAML

Plain scalars in YAML have sharp edges, and the errors point at the wrong
line. A scalar cannot *begin* with `` ` `` or `*` (both common in inline
code and bold), cannot contain `": "`, and inside a `|` block any line
indented less than the first content line silently terminates the block.

**Write prose as a block scalar.** `scripts/lint_content.py` catches all of
these hazards and names the offending line; `scripts/fix_quoting.py` and
`scripts/fix_indentation.py` repair the mechanical cases.

The repair tools are deliberately narrow: they fix the one thing they
understand and preserve each line's depth *relative to its block*. Structural
mistakes -- a mis-indented key, a broken list -- are left for a human,
because a tool that rewrites what it does not fully understand will quietly
corrupt content. Run them with `--check` first.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | status, content counts, whether git is installed |
| `GET` | `/api/curriculum` | full tree with per-exercise status |
| `GET` | `/api/lessons/{id}` | lesson text (never solutions) |
| `GET` | `/api/exercises/{id}` | prompt, starter script, check names, status |
| `GET` | `/api/exercises/{id}/hints/{n}` | one hint at a time |
| `POST` | `/api/exercises/{id}/run` | run a script, no grading |
| `POST` | `/api/exercises/{id}/submit` | grade and record |
| `POST` | `/api/exercises/{id}/solution` | reveal, and record the reveal |
| `POST` | `/api/playground/run` | run any git commands, ungraded |
| `GET`/`DELETE` | `/api/progress` | dashboard data / reset |
| `GET` | `/api/review` | due queue, counts, per-concept strength |

## Known limits

- **Single local user.** No accounts, no sync -- progress lives in one
  SQLite file.
- **The learner's script runs on the host.** It is a shell, in a sandbox
  directory, with the guards listed above. See the trust model.
- **Commit hashes are never graded**, so an exercise cannot check that two
  commits are byte-identical.
- **The clock is pinned.** A sandbox's commits all carry the same
  timestamp, so an exercise cannot teach `git log --date=relative`, and two
  sibling commits have no date order.
- **`git log` orders siblings by graph traversal**, not by date, for the
  same reason.
- **A remote's URL is relative** (`../remotes/origin`), so a repository
  cloned from it records an absolute path instead. The comparison scrubs
  sandbox paths for exactly that reason.
- **The schedule is uniform.** Every concept uses the same interval ladder,
  and exercise difficulty does not yet affect how fast an interval grows. A
  hard exercise would probably deserve a slower growth rate than an easy
  one.
- **Concepts are evenly weighted.** Two exercises sharing a tag contribute
  the same weight regardless of how central that tag is to the exercise.
- **No import or export.** Progress lives in one local file.
- **Browser checks need Chrome/Chromium on PATH and a running server**;
  they are not part of `pytest`, so they must be run deliberately.
- **`uv build` fails**: setuptools cannot auto-discover packages in a flat
  layout. This is an application rather than a distributable library, so
  there is nothing to install -- but if you ever want a PyPI package, it
  needs an explicit `[tool.hatch.build]` or a `src/` layout.

## Licence

[Apache-2.0](LICENSE) -- copyright 2026 smrati.

Apache-2.0 rather than MIT because it grants contributors an explicit
patent licence, and terminates that grant if they bring patent litigation
against the project. For most solo projects the practical difference is
nil; it matters if this ever gets used inside a company.
