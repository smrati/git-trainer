/** Rendering a run's output, the repository's state, and a graded result.
 *
 *  A git exercise does not produce a single result table. It produces a
 *  **repository**, a **transcript** of what the commands printed, and a set
 *  of **checks** the grader ran against it. So there are three renderers
 *  here, and the grading one is the important one -- a failed grade must
 *  always say which check failed and what the difference was.
 */

import { el, mount } from './dom.js';

/** The transcript of a run: what the commands printed, and how it ended. */
export function renderRun(result) {
  const wrap = el('div');

  if (result.error) {
    wrap.append(banner(result.error.kind || 'error', result.error.message));
  }

  // A script whose last command failed has usually made a mistake, and the
  // transcript's scrollback is a long way from the button they just pressed.
  const exitCode = result.script && result.script.exit_code;
  if (exitCode !== null && exitCode !== undefined && exitCode !== 0) {
    wrap.append(
      banner('warn', `Your script exited with ${exitCode} — the last command failed.`),
    );
  }

  if (result.transcript && result.transcript.trim()) {
    wrap.append(
      el('details', { class: 'card', open: true, style: 'margin-top:10px;padding:12px 14px' }, [
        el('summary', { style: 'cursor:pointer;font-weight:500' }, 'What the commands printed'),
        el('pre', { class: 'output', style: 'margin-top:10px' }, result.transcript.trim()),
      ]),
    );
  }

  if (result.state) {
    wrap.append(renderState(result.state));
  }

  const checks = result.checks || [];
  if (checks.length) {
    wrap.append(
      el('div', { class: 'result-meta', style: 'margin-top:16px' }, [
        el('span', { text: `${checks.length} check${checks.length === 1 ? '' : 's'} ran` }),
      ]),
    );
    for (const check of checks) {
      wrap.append(renderCheck(check));
    }
  }

  if (!wrap.childNodes.length) {
    wrap.append(el('div', { class: 'empty', text: 'Nothing ran.' }));
  }

  return wrap;
}

/** The repository as it stands: where you are, and what is dirty. */
export function renderState(state) {
  if (!state) return el('div');

  const rows = [];
  rows.push(['branch', state.current_branch || '(none)']);

  for (const branch of state.branches || []) {
    rows.push([
      branch.current ? 'branch (current)' : 'branch',
      `${branch.name}  ${branch.subject || ''}`.trim(),
    ]);
  }

  for (const entry of state.status || []) {
    const code = `${entry.index}${entry.worktree}`.trim();
    rows.push(['status', `${code}  ${entry.path}`]);
  }

  const table = el('table', { class: 'result' });
  const tbody = el('tbody');
  for (const [label, value] of rows) {
    tbody.append(
      el('tr', {}, [
        el('td', { class: 'k', text: label }),
        el('td', { class: 'v', text: value }),
      ]),
    );
  }
  table.append(tbody);

  return el('div', { style: 'margin-top:16px' }, [
    el('div', { class: 'result-meta' }, el('span', { text: 'The repository' })),
    el('div', { class: 'table-scroll' }, table),
  ]);
}

/** One check's output: what the command printed, and how it ended. */
export function renderCheck(check) {
  const wrap = el('div', { class: 'check-card' });

  const head = el('div', { class: 'result-meta' }, [
    el('strong', { text: check.name || 'check' }),
    el('span', { class: 'spacer' }),
    check.exit_code !== null && check.exit_code !== undefined
      ? el('span', {
          class: check.exit_code === 0 ? 'ok' : 'bad',
          text: `exit ${check.exit_code}`,
        })
      : null,
  ]);
  wrap.append(head);

  const stdout = check.stdout ?? '';
  if (stdout.trim()) {
    wrap.append(el('pre', { class: 'output' }, stdout));
  }

  const stderr = check.stderr ?? '';
  if (stderr.trim()) {
    wrap.append(el('pre', { class: 'output stderr' }, stderr));
  }

  if (check.error) {
    wrap.append(banner('error', check.error));
  }

  if (check.value !== null && check.value !== undefined) {
    wrap.append(
      el('pre', { class: 'output value' }, formatValue(check.value)),
    );
  }

  return wrap;
}

/** The grading diff: every check, what was expected, and what you produced.
 *
 *  A failed grade always shows what differs. Being told "incorrect" without
 *  being shown which check differed is the worst experience this app could
 *  deliver.
 */
export function renderGrading(grading) {
  if (!grading) return null;

  const checks = grading.checks || [];
  if (!checks.length) return null;

  const table = el('table', { class: 'diff' });
  table.append(
    el('thead', {}, el('tr', {}, [
      el('th', { text: 'Check' }),
      el('th', { text: 'Result' }),
      el('th', { text: 'Expected' }),
      el('th', { text: 'Yours' }),
    ])),
  );

  const tbody = el('tbody');
  for (const item of checks) {
    tbody.append(
      el('tr', { class: item.passed ? 'pass' : 'fail' }, [
        el('td', { class: 'idx', text: item.name || `#${item.index + 1}` }),
        el('td', {}, el('span', { class: `badge ${item.passed ? 'solved' : 'revealed'}` },
          item.passed ? 'pass' : 'fail')),
        el('td', {}, el('span', { class: 'was', text: item.expected ?? '—' })),
        el('td', {}, el('span', { class: 'now', text: item.actual ?? '—' })),
      ]),
    );
  }
  table.append(tbody);

  const wrap = el('div', {}, [
    el('div', { class: 'result-meta' }, el('span', {
      text: `${grading.passed_count} of ${grading.total_count} check${grading.total_count === 1 ? '' : 's'} passed`,
    })),
    el('div', { class: 'table-scroll' }, table),
  ]);

  // The first failure gets a sentence of its own. A table of values tells a
  // learner *that* they were wrong; this tells them *why*.
  const firstFailure = checks.find((item) => !item.passed);
  if (firstFailure && firstFailure.detail) {
    wrap.append(el('p', { class: 'diff-detail' }, firstFailure.detail));
  }

  return wrap;
}

function formatValue(value) {
  if (value === null) return 'null';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return JSON.stringify(value, null, 2);
}

/** A message box for errors and confirmations.
 *
 *  The kind is shown as a label, but the *style* comes from a fixed set:
 *  the server sends kinds like `seed_failed` and `tool_missing`, and an
 *  unstyled banner would look like nothing happened.
 */
const BANNER_STYLE = {
  error: 'error',
  warn: 'warn',
  warning: 'warn',
  success: 'success',
  revealed: 'revealed',
  timeout: 'warn',
};

export function banner(kind, message) {
  const style = BANNER_STYLE[kind] || 'error';
  return el('div', { class: `banner ${style}` }, [
    kind && el('span', { class: 'kind', text: kind }),
    message,
  ]);
}

export function replaceResult(node, content) {
  mount(node);
  node.append(content);
}
