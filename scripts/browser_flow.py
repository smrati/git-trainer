"""Interactive browser checks.

Static checks catch a missing file; they do not catch a workflow that silently
does nothing. These drive the real UI the way a learner would -- open a
lesson, write git commands, submit them, read the feedback -- and assert on
what actually ended up on screen.

    uv run python scripts/browser_flow.py [--base URL]

The repositories are real, so these checks take a little while. They are not
part of ``pytest``: they need a running server.
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from browser_check import CHROME, Chrome, new_tab  # noqa: E402

# The exercise the solve and diff flows use. It commits a file, so a wrong
# answer still has the right *shape* and reaches the comparison.
EXERCISE = "f-commit-the-staged-file"
CORRECT = 'git commit -m "Add the notes"'
WRONG = 'git commit -m "Something else"'
CRASHING = "git nonsense-command"

# How long to wait for a run. A git command is milliseconds; this is generous.
RUN_WAIT = 20.0


class Checks:
    def __init__(self):
        self.passed = 0
        self.failed = 0

    def ok(self, label):
        self.passed += 1
        print(f"  ok   {label}")

    def bad(self, label, detail=""):
        self.failed += 1
        print(f"  FAIL {label}")
        if detail:
            print(f"       {detail}")


async def wait_for(page, expression, timeout=RUN_WAIT, interval=0.3):
    """Poll a JS expression until it is truthy, or give up."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if await page.evaluate(expression):
            return True
        await asyncio.sleep(interval)
    return False


async def check_lesson_markdown(page, checks: Checks, base: str):
    """Lesson bodies load lazily on open -- verify the markdown really renders."""
    await page.goto(f"{base}/learn.html", settle=2.0)

    await page.evaluate("document.querySelectorAll('details.card')[0].open = true")
    await asyncio.sleep(1.5)

    info = await page.evaluate(
        """(() => {
            const prose = document.querySelector('.prose');
            if (!prose) return {found: false};
            return {
                found: true,
                headings: prose.querySelectorAll('h2, h3').length,
                codeBlocks: prose.querySelectorAll('pre code').length,
                unrendered: prose.textContent.includes('```'),
            };
        })()"""
    )

    if not info.get("found"):
        checks.bad("lesson body renders after opening", "no .prose element")
    elif info["headings"] < 1:
        checks.bad("lesson markdown produces headings", f"headings={info['headings']}")
    elif info["codeBlocks"] < 1:
        checks.bad("lesson markdown produces code blocks", f"code={info['codeBlocks']}")
    elif info["unrendered"]:
        checks.bad("no raw markdown left visible", "``` still present in the text")
    else:
        checks.ok(
            f"lesson markdown renders ({info['headings']} headings, "
            f"{info['codeBlocks']} code blocks)"
        )


async def check_run_flow(page, checks: Checks, base: str):
    """'Run' must show what happened, and must not grade."""
    await page.goto(f"{base}/exercise.html?id={EXERCISE}", settle=2.0)

    await page.evaluate(
        f"""(() => {{
            const editor = document.querySelector('.code-editor');
            editor.value = {CORRECT!r};
            editor.dispatchEvent(new Event('input', {{bubbles: true}}));
            [...document.querySelectorAll('button')].find(b => b.textContent === 'Run').click();
        }})()"""
    )
    done = await wait_for(page, "!!document.querySelector('pre.output')")

    state = await page.evaluate(
        """(() => {
            const transcript = document.querySelector('pre.output');
            const table = document.querySelector('table.result');
            const diff = document.querySelector('table.diff');
            return {
                transcript: transcript ? transcript.textContent : '',
                rows: table ? table.querySelectorAll('tbody tr').length : 0,
                text: table ? table.textContent : '',
                graded: !!diff,
            };
        })()"""
    )

    if not done:
        checks.bad("the run finished")
    elif "notes.txt" not in state["transcript"]:
        checks.bad("the transcript shows what git printed", f"got {state['transcript'][:120]!r}")
    elif state["rows"] < 1:
        checks.bad("the repository state is shown", f"rows={state['rows']}")
    elif "main" not in state["text"]:
        checks.bad("the repository state names the branch", f"got {state['text'][:120]!r}")
    elif state["graded"]:
        checks.bad("Run does not grade", "a diff table appeared")
    else:
        checks.ok("run shows the transcript and the repository state, and does not grade")


async def check_solve_flow(page, checks: Checks, base: str):
    """Write a correct script, submit, and confirm the feedback is right."""
    await page.goto(f"{base}/exercise.html?id={EXERCISE}", settle=2.0)

    await page.evaluate(
        f"""(() => {{
            const editor = document.querySelector('.code-editor');
            editor.value = {CORRECT!r};
            editor.dispatchEvent(new Event('input', {{bubbles: true}}));
            [...document.querySelectorAll('button')].find(b => b.textContent === 'Submit answer').click();
        }})()"""
    )
    done = await wait_for(page, "!!document.querySelector('.banner.success, .banner.warn')")
    # The "Next exercise" link is filled in from an async call, so give it a
    # moment to land before reading its href.
    await asyncio.sleep(1.0)

    state = await page.evaluate(
        """(() => {
            const banner = document.querySelector('.banner.success');
            const diff = document.querySelector('table.diff');
            const next = [...document.querySelectorAll('a')].find(a => a.textContent.includes('Next exercise'));
            return {
                success: !!banner,
                message: banner ? banner.textContent.trim() : null,
                rows: diff ? diff.querySelectorAll('tbody tr').length : 0,
                headers: diff ? [...diff.querySelectorAll('th')].map(t => t.textContent) : [],
                nextHref: next ? next.getAttribute('href') : null,
                status: document.querySelector('.badge.solved') ? 'solved' : null,
            };
        })()"""
    )

    if not done:
        checks.bad("the submission finished")
    elif not state["success"]:
        checks.bad("correct answer shows a success banner", f"message={state['message']!r}")
    elif state["rows"] < 1:
        checks.bad("every check is listed", f"rows={state['rows']}")
    elif state["headers"] != ["Check", "Result", "Expected", "Yours"]:
        checks.bad("check table headers", f"got {state['headers']}")
    elif state["status"] != "solved":
        checks.bad("the status badge says solved", f"status={state['status']!r}")
    elif not state["nextHref"] or state["nextHref"] == "[object Promise]":
        checks.bad("'Next exercise' link has a real href", f"href={state['nextHref']!r}")
    else:
        checks.ok(
            f"correct answer: banner, {state['rows']} check(s), next -> {state['nextHref']}"
        )


async def check_diff_flow(page, checks: Checks, base: str):
    """A wrong answer must explain itself, not just say 'wrong'."""
    await page.goto(f"{base}/exercise.html?id={EXERCISE}", settle=2.0)

    await page.evaluate(
        f"""(() => {{
            const editor = document.querySelector('.code-editor');
            editor.value = {WRONG!r};
            editor.dispatchEvent(new Event('input', {{bubbles: true}}));
            [...document.querySelectorAll('button')].find(b => b.textContent === 'Submit answer').click();
        }})()"""
    )
    done = await wait_for(page, "!!document.querySelector('.banner.success, .banner.warn, .banner.error')")

    state = await page.evaluate(
        """(() => {
            const warn = document.querySelector('.banner.warn');
            const diff = document.querySelector('table.diff');
            const detail = document.querySelector('.diff-detail');
            return {
                warned: !!warn,
                message: warn ? warn.textContent.trim() : null,
                diffRows: diff ? diff.querySelectorAll('tbody tr').length : 0,
                headers: diff ? [...diff.querySelectorAll('th')].map(t => t.textContent) : [],
                detail: detail ? detail.textContent.trim() : null,
                expected: diff ? diff.querySelector('tbody .was')?.textContent.trim() : null,
                actual: diff ? diff.querySelector('tbody .now')?.textContent.trim() : null,
            };
        })()"""
    )

    if not done:
        checks.bad("the submission finished")
    elif not state["warned"]:
        checks.bad("wrong answer shows a warning banner", f"message={state['message']!r}")
    elif state["diffRows"] < 1:
        checks.bad("wrong answer shows a per-check diff", f"diffRows={state['diffRows']}")
    elif state["headers"] != ["Check", "Result", "Expected", "Yours"]:
        checks.bad("diff table headers", f"got {state['headers']}")
    elif not state["detail"]:
        checks.bad("wrong answer says what differs", "no .diff-detail element")
    else:
        checks.ok(
            f"wrong answer: banner + {state['diffRows']} diff row(s); "
            f"expected {state['expected']!r} got {state['actual']!r}"
        )


async def check_failure_flow(page, checks: Checks, base: str):
    """A command that does not exist must say so, not crash."""
    await page.goto(f"{base}/exercise.html?id={EXERCISE}", settle=2.0)

    await page.evaluate(
        f"""(() => {{
            const editor = document.querySelector('.code-editor');
            editor.value = {CRASHING!r};
            editor.dispatchEvent(new Event('input', {{bubbles: true}}));
            [...document.querySelectorAll('button')].find(b => b.textContent === 'Submit answer').click();
        }})()"""
    )
    done = await wait_for(
        page, "!!document.querySelector('.banner.error, .banner.warn, .banner.success')"
    )

    state = await page.evaluate(
        """(() => {
            const banners = [...document.querySelectorAll('.banner.warn')];
            const failed = banners.find(b => b.textContent.includes('exited with'));
            const log = document.querySelector('pre.output');
            return {
                warned: !!failed,
                message: failed ? failed.textContent.trim() : null,
                log: log ? log.textContent : '',
            };
        })()"""
    )

    if not done:
        checks.bad("the failing submission finished")
    elif not state["warned"]:
        checks.bad("a failing command is called out", f"message={state['message']!r}")
    elif "exited with" not in (state["message"] or ""):
        checks.bad("the warning says the script failed", f"message={state['message']!r}")
    elif "not a git command" not in state["log"] and "git:" not in state["log"]:
        checks.bad("the command output is shown", "no error line in the log")
    else:
        checks.ok("a failing command is called out and its output is shown")


async def check_hints(page, checks: Checks, base: str):
    """Hints are progressive."""
    await page.goto(f"{base}/exercise.html?id={EXERCISE}", settle=2.0)

    await page.evaluate(
        """[...document.querySelectorAll('button')].find(b => b.textContent.startsWith('Hint')).click()"""
    )
    await asyncio.sleep(1.2)
    first = await page.evaluate(
        """({text: document.querySelector('.hint')?.textContent.trim() || null,
            label: document.querySelector('.hint-index')?.textContent || null})"""
    )
    await page.evaluate(
        """[...document.querySelectorAll('button')].find(b => b.textContent.startsWith('Hint')).click()"""
    )
    await asyncio.sleep(1.2)
    second = await page.evaluate("document.querySelectorAll('.hint').length")

    if not first["text"]:
        checks.bad("hint reveals text")
    elif not first["label"] or not first["label"].startswith("Hint 1 of"):
        checks.bad("hint is numbered", f"label={first['label']!r}")
    elif second != 2:
        checks.bad("hints accumulate", f"count={second}")
    else:
        checks.ok(f"hints progressive: 1 -> {second}")


async def check_reveal_marks_revealed(page, checks: Checks, base: str):
    """Revealing must change the recorded state, not just show the answer."""
    await page.goto(f"{base}/exercise.html?id=f-add-a-gitignore", settle=2.0)
    await page.evaluate(
        """[...document.querySelectorAll('button')].find(b => b.textContent.includes('Show solution')).click()"""
    )
    await asyncio.sleep(1.5)

    state = await page.evaluate(
        """(() => {
            const badges = [...document.querySelectorAll('.badge')].map(b => b.textContent.trim());
            return { badges, hasSolution: !!document.querySelector('pre.output.value') };
        })()"""
    )
    if not state["hasSolution"]:
        checks.bad("revealing shows the solution")
    elif "Revealed" not in state["badges"]:
        checks.bad("revealing updates the status badge", f"badges={state['badges']}")
    else:
        checks.ok(f"reveal shows solution and marks revealed: {state['badges']}")


async def check_playground(page, checks: Checks, base: str):
    """The playground runs commands and shows what happened."""
    await page.goto(f"{base}/playground.html", settle=2.0)

    await page.evaluate(
        """(() => {
            document.querySelector('#editor').value = "git log --format=%s";
            document.querySelector('#run').click();
        })()"""
    )
    done = await wait_for(page, "!!document.querySelector('pre.output')")

    output = await page.evaluate(
        """(() => {
            const pre = document.querySelector('pre.output');
            return pre ? pre.textContent.trim() : null;
        })()"""
    )
    if not done:
        checks.bad("the playground run finished")
    elif "Add the project" not in (output or ""):
        checks.bad("the playground shows the command's output", f"got {output!r}")
    else:
        checks.ok("the playground runs commands and shows their output")


async def check_review_flow(page, checks: Checks, base: str):
    """Concept filtering must actually filter the queue."""
    await page.goto(f"{base}/review.html", settle=2.0)
    before = await page.evaluate("document.querySelectorAll('#queue a.ex-row').length")
    await page.evaluate(
        """document.querySelectorAll('#concepts .card')[0].click()"""
    )
    await asyncio.sleep(0.6)
    after = await page.evaluate("document.querySelectorAll('#queue a.ex-row').length")
    chips = await page.evaluate(
        """document.querySelector('#queue-sub')?.textContent.trim() || ''"""
    )
    if "Filtering by" not in chips:
        checks.bad("concept click shows a filter chip", f"sub={chips!r}")
    elif after > before:
        checks.bad("concept filter narrows the queue", f"{before} -> {after}")
    else:
        checks.ok(f"concept filter works ({before} -> {after} rows)")


FLOWS = [
    ("lesson markdown", check_lesson_markdown),
    ("run flow", check_run_flow),
    ("solve flow", check_solve_flow),
    ("diff flow", check_diff_flow),
    ("failure flow", check_failure_flow),
    ("hints", check_hints),
    ("reveal marks revealed", check_reveal_marks_revealed),
    ("playground", check_playground),
    ("review filtering", check_review_flow),
]


async def run(base: str):
    browser = Chrome()
    checks = Checks()
    try:
        browser.wait_ready()
        for label, flow in FLOWS:
            page = await new_tab(browser)
            try:
                await flow(page, checks, base)
            except Exception as exc:
                checks.failed += 1
                print(f"  FAIL {label} (raised {type(exc).__name__}: {exc})")
            finally:
                await page.ws.close()
    finally:
        browser.close()
    return checks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base",
        default="http://127.0.0.1:8101",
        help="the base URL of the running app (default: %(default)s)",
    )
    args = parser.parse_args()

    if not CHROME:
        print("no Chrome/Chromium found; skipping")
        return 0
    checks = asyncio.run(run(args.base.rstrip("/")))
    print()
    print(f"{checks.passed} passed, {checks.failed} failed")
    return 1 if checks.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
