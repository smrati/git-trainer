# Authoring exercises

An exercise is a **task**: a starting repository, a script the learner
writes, a **reference** that solves it, and a list of **checks** that turn a
repository into a comparable value.

The rule that shapes everything else:

> **Grading is by reference solution, not by a lookup table.**

There is no expected output written anywhere. The grader runs the reference
script in a copy of the exercise's seed, runs the learner's script in another
copy, and compares what the two repositories turned out to be. The
reference's behaviour *is* the answer. So there is nothing that can drift
out of date, and any correct implementation passes however it was written.

---

## The shape of an exercise

```yaml
- id: f-example                       # unique across every track
  title: A short title
  prompt: |                           # markdown, required
    What the learner must produce.
  kind: cli                           # the only kind today
  starter: |                          # the script the learner starts with
    # Write your commands here, one per line.
  solution: |                         # the reference script
    git add app.py
    git commit -m "Add the app"
  seed:                               # the starting repository
    files:
      README.md: |
        # demo
    commands:
      - git add .
      - git commit -m "Initial commit"
    after:
      - printf 'print("hello")\n' > app.py
  checks:                             # what the grader does
    - name: the history has two commits
      kind: command
      command: git rev-list --count HEAD
    - name: the branches
      kind: state
      select: branches
  hints:                              # 2-3, always block scalars
    - |
      `git add` puts a file in the staging area.
  teach: |                            # "Why this works", required
    Why this works -- shown under "Why this works".
  tags: [commit, staging]             # from the controlled vocabulary
```

### `kind`

There is one kind today: `cli`. Every git exercise is "do this to a
repository", and a second kind would only add a way to write the same
exercise twice.

### `starter` and `solution`

Both are shell scripts, run with `sh`, one command per line.

The **starter** is what the learner sees. It should be comments: what to do
and where to put it. It must not contain the answer -- there is a test that
enforces this, and it compares the *commands*, ignoring comments.

The **solution** is the reference. It must pass its own checks, and the
starter must fail them. `scripts/smoke.py` checks both, for every exercise.

### `seed`

The starting repository. The learner never sees this; the grader runs it
twice, once for the reference and once for the learner, and both copies are
identical.

| Key | What it does |
| --- | --- |
| `files` | the working tree's contents, path to text |
| `commands` | shell commands run in the repository, before any remote exists |
| `after` | shell commands run in the repository, after the remotes are added |
| `remotes` | other repositories, keyed by remote name |
| `env` | extra environment variables for the whole sandbox |
| `init` | `false` leaves a plain directory with no `.git` |
| `base` | for a remote: `local` (clone the local repository) or `empty` |

**A remote is built by cloning the local repository** and then applying the
remote's own files and commands. That is what makes it *related*: the two
share history, so `git fetch`, `git merge origin/main` and `git pull` behave
the way they do in real life instead of failing with "refusing to merge
unrelated histories".

```yaml
seed:
  files:
    README.md: |
      # demo
  commands:
    - git add .
    - git commit -m "Initial commit"
  remotes:
    origin:
      files:
        extra.txt: |
          work done on the remote
      commands:
        - git add .
        - git commit -m "Remote work"
```

That remote is *ahead* of the local repository, which is exactly what an
exercise about `git pull` needs.

### `checks`

Three kinds, and each produces one comparable value.

| Kind | What it does | Use it for |
| --- | --- | --- |
| `command` | runs a shell command in the sandbox repository; compares the exit code and the stdout | the workhorse |
| `state` | reads a named piece of the repository as JSON; compared semantically | branches, status, tags, the history |
| `output` | compares **the learner's script's stdout** with the reference's | exercises whose answer is a command that *prints* something |

`state` selectors: `branches`, `remote_branches`, `status`, `log`, `tags`,
`remotes`, `tracked_files`, `config`. A state check may name a remote with
`remote: origin`, which reads that remote's repository instead.

`output` exists because some exercises have no repository state to compare.
"List the files git is not tracking" is answered by a command that prints,
and running the reference command again would prove nothing about what the
learner wrote.

**Per-check settings**, all optional:

```yaml
checks:
  - name: the log, as JSON
    kind: command
    command: git log --format=%H
    settings:
      output_mode: json      # exact | lines | json
      order_sensitive: false
      scrub: false           # keep hashes and paths
```

`scrub` is on by default. It replaces commit hashes, absolute paths and
dates with placeholders, because a hash is never the skill and two runs of
the same script produce different hashes unless the sandbox pins the clock
and the identity -- which it does, but a hash is still not what you want to
be graded on.

### `cases`

A case is a variant of the starting repository. The reference is run once
per case, so a case with a different seed has a different expected answer.

```yaml
cases:
  - name: the default repository
  - name: a repository with an extra file
    seed:
      files:
        extra.txt: |
          hello
```

---

## Writing prose in YAML

**Write prose as a block scalar.** Always. `prompt`, `teach`, `body`,
`summary`, `description`, `starter`, `solution`, every `files` value and
every hint.

```yaml
hints:
  - |
    `git add` puts a file in the staging area.
    The check reads git's own view of it.
```

A block scalar has none of YAML's scalar hazards. A *plain* scalar cannot
begin with a backtick or an asterisk, cannot contain `": "`, and a line
indented less than the block's first line silently ends the block. All
three produce parser errors that point at the wrong line.

`scripts/lint_content.py` catches all of them and names the offending line.
`scripts/fix_quoting.py` and `scripts/fix_multiline.py` repair the two
mechanical cases -- but **run `fix_multiline.py` first**, because quoting a
wrapped item puts its closing quote in the middle of the sentence.

---

## Writing a good exercise

### The check must notice the difference

The reference passes; the starter must fail. That is not a formality -- it
is the test of whether the exercise tests anything. If the seed already
satisfies the checks, doing nothing passes, and the exercise is a
formality.

The usual way to make an exercise real is to leave the work undone in the
seed. Use `after:` for changes that must happen *after* the initial commit:

```yaml
seed:
  files:
    README.md: |
      # demo
  commands:
    - git add .
    - git commit -m "Initial commit"
  after:
    - printf 'print("hello")\n' > app.py     # created, so it is untracked
```

### Never grade a hash

`git log --format=%H` is not a check -- the hashes differ between runs and
are scrubbed to `<sha>` anyway. Grade the *structure*:

| Instead of | Grade |
| --- | --- |
| `git log --format=%H` | `git log --format=%s` |
| `git rev-parse HEAD` | `git cat-file -t HEAD` |
| `git rev-list --count HEAD` | that is fine -- it is a number |
| `git show --stat` | `git show --name-only --format= HEAD` |

### The sandbox has no editor

There is no `EDITOR` and no `GIT_EDITOR` that waits for input. `GIT_EDITOR`
is `true`, so:

- `git commit` without `-m` fails with "Aborting commit due to empty commit
  message" -- always pass `-m "..."`;
- `git revert` without `--no-edit` fails -- always pass `--no-edit`;
- `git rebase -i` opens the todo list in `GIT_SEQUENCE_EDITOR`, which is also
  `true`. Set it in the seed's `env:` when an exercise needs it:

```yaml
seed:
  env:
    GIT_SEQUENCE_EDITOR: "sed -i 's/^pick \\(.*\\) Fix the bug$/drop \\1 Fix the bug/'"
```

### The sandbox is deterministic

The author identity and the clock are pinned, so two runs of the same seed
produce the same commits. You can rely on that: `git log --format=%s` gives
the same text every time, and a diff is byte-for-byte stable.

You can also rely on the *other* direction: if the reference and the learner
produce the same commits, their hashes are identical, so a check that
compares hash *counts* or *structure* is stable.

### Tags come from a controlled vocabulary

The tag list lives in `app/routers/review.py`, next to the labels the review
page shows, so a tag and its label can never drift apart. `scripts/
tag_exercises.py` checks the vocabulary against the content, and a test
enforces it too.

---

## Verifying your work

```bash
uv run python scripts/lint_content.py            # the YAML parses and has no hazards
uv run python scripts/smoke.py --lesson f-what-is-a-repository
uv run python scripts/smoke.py                   # the whole curriculum
uv run python scripts/tag_exercises.py           # the tags are in the vocabulary
uv run pytest -q                                 # including the correctness test
```

`smoke.py` is the important one. It runs every reference against a real
repository and requires it to pass its own checks, then requires the
starter to fail. Because git needs no daemon and is fast, the whole
curriculum verifies in seconds.

## Writing prose that teaches

The `teach` field is shown under "Why this works", and it is the part a
learner reads after they have solved it. Explain *why*, not *what* they
just did. "A commit is a snapshot of the whole tree, plus a pointer to its
parent" teaches something; "this commits the file" does not.

The same goes for the hints: escalate, and never give away the answer in
the first one.
