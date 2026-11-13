// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

// Run with: node --test scripts/ax-evals/ax-evals.test.js

const assert = require('node:assert/strict')
const { execFileSync } = require('node:child_process')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { test } = require('node:test')

const plan = require('./plan.js')
const { render, loadResults } = require('./report.js')
const { upsertComment } = require('./comment.js')
const { redact } = require('./redact.js')
const config = require('./evals.json')

const BOT = { login: 'github-actions[bot]' }
const SHA = 'c8114371cbfae6ee7ffce6aea618f8de7f161c91'
const REPO = { owner: 'daytona', repo: 'clients' }
const ALL = plan.evalsOf(config).map((e) => e.name)
const TASKS = Object.keys(config.tasks)
const WORKFLOW = path.join(__dirname, '..', '..', '.github', 'workflows', 'ax_evals.yaml')

function mockGithub({
  role = 'write',
  fork = false,
  base = 'main',
  files = [{ filename: 'cli/cmd/root.go' }],
  comments = [],
} = {}) {
  const calls = []
  return {
    calls,
    paginate: async (fn, args) => (await fn(args)).data,
    rest: {
      repos: { getCollaboratorPermissionLevel: async () => ({ data: { role_name: role } }) },
      pulls: {
        get: async () => ({
          data: {
            state: 'open',
            base: { ref: base },
            head: { sha: SHA, ref: 'feature/x', repo: { full_name: fork ? 'someone/clients' : 'daytona/clients' } },
          },
        }),
        listFiles: async () => ({ data: files }),
      },
      issues: {
        listComments: async () => ({ data: comments }),
        createComment: async ({ body }) => calls.push(['create', body]),
        updateComment: async ({ comment_id, body }) => calls.push(['update', comment_id, body]),
        deleteComment: async ({ comment_id }) => calls.push(['delete', comment_id]),
      },
    },
  }
}

function mockCore() {
  const outputs = {}
  return {
    outputs,
    setOutput: (k, v) => (outputs[k] = v),
    info() {},
    notice: (m) => (outputs.notice = m),
    warning: (m) => (outputs.warning = m),
    setFailed: (m) => (outputs.failed = m),
  }
}

function dispatchContext(evals, actor = 'dev', pr = '7') {
  return {
    eventName: 'workflow_dispatch',
    repo: REPO,
    serverUrl: 'https://github.com',
    runId: 100,
    payload: { inputs: { pr, evals }, sender: { login: actor } },
  }
}

async function runPlan(github, context) {
  const core = mockCore()
  await plan({ github, context, core })
  return core.outputs
}

const names = (outputs) => JSON.parse(outputs.matrix).map((entry) => entry.name)
const onSurface = (surface) => TASKS.map((task) => `${surface}-${task}`)

test('evals.json: every eval has a Firegoat prompt id and only known ids are listed', () => {
  assert.deepEqual(Object.keys(config.promptIds).sort(), [...ALL].sort())
  assert.equal(new Set(Object.values(config.promptIds)).size, ALL.length, 'every eval has its own prompt')
  for (const id of Object.values(config.promptIds)) assert.match(id, /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/)
  for (const surface of Object.keys(config.surfaces)) {
    assert.ok(fs.existsSync(path.join(__dirname, 'setup', `${surface}.sh`)), `setup/${surface}.sh`)
  }
})

test('parseEvals', () => {
  assert.deepEqual(plan.parseEvals(''), [])
  assert.deepEqual(plan.parseEvals(undefined), [])
  assert.deepEqual(plan.parseEvals(' python, TS  cli-preview-url'), ['python', 'ts', 'cli-preview-url'])
})

test('selectByTokens: surfaces, tasks, single evals, aliases and all', () => {
  assert.deepEqual(plan.selectByTokens(config, ['all']).names, ALL)
  assert.deepEqual(plan.selectByTokens(config, ['py']).names, onSurface('python'))
  assert.deepEqual(plan.selectByTokens(config, ['preview-url']).names, ['python-preview-url', 'typescript-preview-url', 'cli-preview-url'])
  assert.deepEqual(plan.selectByTokens(config, ['cli-git-commit', 'ts']).names, [...onSurface('typescript'), 'cli-git-commit'])
  assert.deepEqual(plan.selectByTokens(config, ['go']), { unknown: 'go' })
})

test('selectByPaths', () => {
  assert.deepEqual(plan.selectByPaths(config, ['sdk-python/src/x.py', 'README.md']), onSurface('python'))
  assert.deepEqual(plan.selectByPaths(config, ['cli/main.go', 'sdk-typescript/src/a.ts']), [...onSurface('typescript'), ...onSurface('cli')])
  assert.deepEqual(plan.selectByPaths(config, ['docs/x.md', 'examples/a.py']), [])
  assert.deepEqual(plan.selectByPaths(config, ['contract/api-openapi.json']), ALL)
  assert.deepEqual(plan.selectByPaths(config, ['scripts/ax-evals/evals.json']), [])
  assert.deepEqual(plan.selectByPaths(config, ['.github/workflows/ax_evals.yaml']), [])
  assert.deepEqual(plan.selectByPaths(config, ['contracts/x']), [])
  const synthetic = { ...config, sharedPaths: ['a/b.json', 'dir/'] }
  assert.deepEqual(plan.selectByPaths(synthetic, ['a/b.json']), ALL)
  assert.deepEqual(plan.selectByPaths(synthetic, ['dir/x/y.md']), ALL)
  assert.deepEqual(plan.selectByPaths(synthetic, ['a/b.json.bak', 'dirx/y']), [])
})

test('plan: changed surfaces for a write user', async () => {
  const github = mockGithub()
  const outputs = await runPlan(github, dispatchContext(''))
  assert.deepEqual(names(outputs), onSurface('cli'))
  assert.equal(JSON.parse(outputs.matrix)[0].title, 'CLI: Run Python')
  assert.equal(JSON.parse(outputs.matrix)[0].promptId, config.promptIds['cli-run-python'])
  assert.equal(outputs.sha, SHA)
  assert.equal(outputs.pr, '7')
  assert.match(github.calls[0][1], /Running 5 evals/)
})

test('plan: label trigger', async () => {
  const context = {
    ...dispatchContext(''),
    eventName: 'pull_request_target',
    payload: { pull_request: { number: 7 }, sender: { login: 'dev' } },
  }
  assert.deepEqual(names(await runPlan(mockGithub(), context)), onSurface('cli'))
})

test('plan: rejects triage users and non-collaborators', async () => {
  let github = mockGithub({ role: 'triage' })
  assert.equal((await runPlan(github, dispatchContext('all'))).matrix, '[]')
  assert.deepEqual(github.calls, [])

  github = mockGithub()
  github.rest.repos.getCollaboratorPermissionLevel = async () => {
    throw Object.assign(new Error('Not Found'), { status: 404 })
  }
  assert.equal((await runPlan(github, dispatchContext('all', 'stranger'))).matrix, '[]')
  assert.deepEqual(github.calls, [])
})

test('plan: forks, other bases, unknown evals, selection, renames, nothing changed', async () => {
  assert.match((await runPlan(mockGithub({ fork: true }), dispatchContext('all'))).notice, /forks/)
  const toV0 = await runPlan(mockGithub({ base: 'release/v0' }), dispatchContext('all'))
  assert.match(toV0.notice, /targets release\/v0/)
  assert.equal(toV0.matrix, '[]')
  assert.match((await runPlan(mockGithub(), dispatchContext('rust'))).failed, /Unknown eval 'rust'/)
  assert.deepEqual(names(await runPlan(mockGithub(), dispatchContext('preview-url, cli-preview-url'))), [
    'python-preview-url',
    'typescript-preview-url',
    'cli-preview-url',
  ])
  assert.equal(JSON.parse((await runPlan(mockGithub(), dispatchContext('all'))).matrix).length, ALL.length)
  const renamed = mockGithub({ files: [{ filename: 'legacy/x.py', previous_filename: 'sdk-python/x.py' }] })
  assert.deepEqual(names(await runPlan(renamed, dispatchContext(''))), onSurface('python'))
  const huge = mockGithub({ files: Array.from({ length: 3000 }, (_, i) => ({ filename: `docs/${i}.md` })) })
  assert.equal(names(await runPlan(huge, dispatchContext(''))).length, ALL.length)
  const docsOnly = mockGithub({ files: [{ filename: 'README.md' }] })
  assert.equal((await runPlan(docsOnly, dispatchContext(''))).matrix, '[]')
  assert.match(docsOnly.calls[0][1], /nothing was evaluated/)
  assert.match(docsOnly.calls[0][1], /--ref main -f pr=7 -f evals=all/)
  assert.doesNotMatch(docsOnly.calls[0][1], /harness/)
  const harnessOnly = mockGithub({ files: [{ filename: 'scripts/ax-evals/plan.js' }] })
  assert.equal((await runPlan(harnessOnly, dispatchContext(''))).matrix, '[]')
  assert.match(harnessOnly.calls[0][1], /dispatch with `--ref 'feature\/x'` instead/)
  const moved = mockGithub({ files: [{ filename: 'legacy/plan.js', previous_filename: 'scripts/ax-evals/plan.js' }] })
  await runPlan(moved, dispatchContext(''))
  assert.match(moved.calls[0][1], /harness/)
  const odd = mockGithub({ files: [{ filename: 'scripts/ax-evals/plan.js' }] })
  const get = odd.rest.pulls.get
  odd.rest.pulls.get = async (args) => {
    const r = await get(args)
    r.data.head.ref = "x';rm -rf ~;'$HOME"
    return r
  }
  await runPlan(odd, dispatchContext(''))
  assert.ok(odd.calls[0][1].includes(`--ref 'x'\\'';rm -rf ~;'\\''$HOME'`))
  const failing = mockGithub({ files: [{ filename: 'README.md' }] })
  failing.rest.issues.createComment = async () => {
    throw new Error('secondary rate limit')
  }
  const quiet = await runPlan(failing, dispatchContext(''))
  assert.equal(quiet.matrix, '[]')
  assert.equal(quiet.failed, undefined)
  assert.match(quiet.warning, /secondary rate limit/)
})

test('plan: manual runs need a pull request number', async () => {
  for (const pr of ['', '0', '-3', '1.5', 'abc']) {
    const github = mockGithub()
    const outputs = await runPlan(github, dispatchContext('all', 'dev', pr))
    assert.match(outputs.failed, /Not a pull request number/)
    assert.equal(outputs.matrix, '[]')
    assert.deepEqual(github.calls, [])
  }
})

test('upsertComment: newest run wins, spoofed and duplicate markers', async () => {
  const comments = [
    { id: 5, user: { login: 'mallory' }, body: '<!-- ax-evals -->\n<!-- ax-evals-run:999999 -->' },
    { id: 6, user: BOT, body: '<!-- ax-evals -->\n<!-- ax-evals-run:200 -->' },
    { id: 7, user: BOT, body: '<!-- ax-evals -->\n<!-- ax-evals-run:150 -->' },
  ]
  let github = mockGithub({ comments })
  assert.equal(await upsertComment(github, REPO, 7, 100, ['older']), false)
  assert.deepEqual(github.calls, [['delete', 7]])

  github = mockGithub({ comments: comments.slice(0, 2) })
  assert.equal(await upsertComment(github, REPO, 7, 300, ['newer']), true)
  assert.equal(github.calls[0][1], 6)

  // The newest run's comment survives even when it is not the first one listed.
  github = mockGithub({ comments: [comments[2], comments[1]] })
  assert.equal(await upsertComment(github, REPO, 7, 100, ['older']), false)
  assert.deepEqual(github.calls, [['delete', 7]])

  github = mockGithub({ comments: comments.slice(1) })
  github.rest.issues.deleteComment = async () => {
    throw Object.assign(new Error('Not Found'), { status: 404 })
  }
  assert.equal(await upsertComment(github, REPO, 7, 300, ['x']), true)
})

test('loadResults: an unreadable result reads as did not finish', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ax-results-bad-'))
  fs.writeFileSync(path.join(dir, 'cli-run-python.json'), '{"a":"\\u001R"}')
  const [result] = loadResults(dir, [{ name: 'cli-run-python', title: 'CLI: Run Python' }])
  assert.ok(render({ sha: SHA, runUrl: 'u', results: [result] }).join('\n').includes('| CLI: Run Python | ⏹️ Did not finish |'))
})

test('loadResults: a results directory that was never created reads as not started', () => {
  const results = loadResults(path.join(os.tmpdir(), 'ax-results-missing-' + process.pid), [{ name: 'cli-run-python', title: 'CLI: Run Python' }])
  assert.deepEqual(results.map((r) => [r.title, r.finalStatus]), [['CLI: Run Python', '']])
})

test('workflow: the report renders even when no eval saved an artifact', () => {
  const workflow = fs.readFileSync(WORKFLOW, 'utf8')
  const download = workflow.slice(workflow.indexOf('actions/download-artifact'), workflow.indexOf('merge-multiple'))
  assert.match(download, /continue-on-error: true/)
})

test('loadResults: reads <eval>.json from one directory, missing ones as not started', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ax-results-'))
  fs.writeFileSync(
    path.join(dir, 'cli-preview-url.json'),
    JSON.stringify({ name: 'cli-preview-url', finalStatus: 'completed', outcome: 'succeeded' }),
  )
  const results = loadResults(dir, [
    { name: 'cli-preview-url', title: 'CLI: Preview URL' },
    { name: 'python-git-commit', title: 'Python SDK: Git commit' },
  ])
  assert.deepEqual(
    results.map((r) => [r.title, r.finalStatus, r.outcome]),
    [
      ['CLI: Preview URL', 'completed', 'succeeded'],
      ['Python SDK: Git commit', '', undefined],
    ],
  )
})

test('workflow: results are written and downloaded as <eval>.json in one directory', () => {
  const workflow = fs.readFileSync(WORKFLOW, 'utf8')
  const evalJob = workflow.slice(workflow.indexOf('  eval:'), workflow.indexOf('  report:'))
  const reportJob = workflow.slice(workflow.indexOf('  report:'))
  assert.match(evalJob, /save-result\.js "result\/\$\{NAME\}\.json"/)
  assert.match(evalJob, /path: result\/\$\{\{ matrix\.eval\.name \}\}\.json/)
  assert.match(evalJob, /EVALS_API_BASE_URL: https:\/\/firegoat\.ai\/evals/)
  assert.match(reportJob, /merge-multiple: true/)
})

test('render: every status label', () => {
  const checklist = (values) => JSON.stringify({ reward: { checklist: values } })
  const out = render({
    sha: SHA,
    runUrl: 'u',
    results: [
      { title: 'A', finalStatus: 'completed', outcome: 'goal_not_met' },
      { title: 'B', finalStatus: 'failed' },
      { title: 'C', finalStatus: 'superseded' },
      { title: 'D', finalStatus: 'running' },
      { title: 'E', finalStatus: '' },
      { title: 'F', finalStatus: 'completed', outcome: 'succeeded', reportJson: checklist({ a: 1, b: 0 }) },
      { title: 'G', finalStatus: 'completed', outcome: 'succeeded', reportJson: checklist({ a: 1 }) },
      { title: 'H', finalStatus: 'completed', outcome: 'succeeded', reportJson: '' },
      { title: 'I', finalStatus: '', failureReason: 'failed to start eval: 401' },
      { title: 'J', finalStatus: '', runId: 'r1', failureReason: 'fatal error during polling (404)' },
      { title: 'K', finalStatus: '', runId: 'r2' },
    ],
  }).join('\n')
  const expected = {
    A: '❌ goal\\_not\\_met',
    B: '💥 Failed',
    C: '↪️ Superseded',
    D: '⏱️ Timed out',
    E: '⚠️ Not started',
    F: '⚠️ Succeeded, checks failed',
    I: '💥 Could not start',
    J: '⏹️ Did not finish',
    K: '⏹️ Did not finish',
    G: '✅ Succeeded',
    H: '⚠️ Succeeded, checks missing',
  }
  for (const [title, label] of Object.entries(expected))
    assert.ok(out.includes(`| ${title} | ${label} |`), `${title}: ${label}`)
  assert.doesNotMatch(out, /All evals succeeded/)
  assert.match(out, /^### AX evals \(Firegoat\)$/m)
})

test('render: a nulled score explains itself when there is no key finding', () => {
  const reportJson = JSON.stringify({ reward: { checklist: { a: 1 } }, nullNotice: 'Score nulled: the session ended early' })
  const out = render({ sha: SHA, runUrl: 'u', results: [{ title: 'Py', finalStatus: 'completed', outcome: 'goal_not_met', reportJson }] })
  assert.ok(out.includes('- **Py:** Score nulled: the session ended early'))
})

test('render: friction points without a finding, and an empty checklist', () => {
  const out = render({
    sha: SHA,
    runUrl: 'u',
    results: [
      { title: 'Ts', finalStatus: 'completed', outcome: 'succeeded', reportJson: JSON.stringify({ reward: { checklist: {} } }), frictionPoints: [{ severity: 'minor', text: 'slow docs' }] },
    ],
  }).join('\n')
  assert.ok(out.includes('| Ts | ⚠️ Succeeded, checks missing | – |'))
  assert.ok(out.includes('- **Ts:** friction points\n  - _minor_: slow docs'))
})

test('redact: Daytona credentials never reach the comment', () => {
  const key = `dtn_${'a1'.repeat(16)}`
  const invite = `dtni_${'A'.repeat(43)}`
  assert.equal(redact(`DAYTONA_API_KEY=${key} and ${invite}.`), 'DAYTONA_API_KEY=REDACTED and REDACTED.')
  assert.equal(redact('dtn_short and plain text'), 'dtn_short and plain text')
  // Encoded text: the key follows `\\n` (a word character) or is glued to more word characters.
  const encoded = JSON.stringify({ n: `printed:\n${key}`, t: `\t${key}_x` })
  assert.ok(!redact(encoded).includes(key))
  assert.ok(!redact(JSON.stringify(encoded)).includes(key))
  // Escaped or encoded forms of the key are redacted too, and the text around them is kept.
  const hex = key.slice(4)
  const forms = [
    `\\u0064tn_${hex}`,
    `\\\\u0064\\\\u0074n\\\\u005f${hex}`,
    `%64%74%6e%5f${hex}`,
    `dtn&lowbar;${hex}`,
    `dtn&#95;${hex}`,
    `&#x64;tn_${hex}`,
    `dtn\\_${hex}`,
    `dtn_${hex.slice(0, 10)}\u200b${hex.slice(10)}`,
  ]
  for (const form of forms) {
    assert.equal(redact(`before ${form} after`), 'before REDACTED after', form)
    assert.ok(!redact(JSON.stringify({ t: form })).includes(hex.slice(0, 16)), `json ${form}`)
  }
  const json = JSON.stringify({ a: `x \\u0064tn_${hex} y`, b: 'z' })
  assert.deepEqual(JSON.parse(redact(json)), { a: 'x REDACTED y', b: 'z' })
  assert.equal(redact('100% of %20 and \\u0041 stay'), '100% of %20 and \\u0041 stay')
  // An escape-looking prefix cannot swallow a plain key's first character.
  for (const prefix of ['%a', '%6', '\\uabc', '"%2']) {
    const out = redact(`x${prefix}${key}`)
    assert.ok(out.startsWith('x') && out.endsWith('REDACTED') && !out.includes(hex.slice(0, 8)), prefix)
  }
  assert.ok(!redact(`x%a${invite}`).includes('A'.repeat(20)))
  // A cut never splits an escape, so JSON stays valid.
  for (const odd of ['boom \u001dtn_', '\ud83dtn_']) assert.deepEqual(JSON.parse(redact(JSON.stringify({ a: `${odd}${hex}` }))), { a: odd.startsWith('boom') ? 'boom REDACTED' : 'REDACTED' })
  // Astral and escaped invisible characters, and UTF-8 percent runs, inside a key.
  for (const split of ['\u{e0020}', '\u{e0100}', '\\u200b', '&#8203;', '%E2%80%8B']) assert.equal(redact(`dtn${split}_${hex}`), 'REDACTED', split)
  // Nested encodings and any number of backslashes.
  for (const nested of [`%5Cu0064tn_${hex}`, `\\u0025\\u0036\\u0034tn_${hex}`, `${'\\'.repeat(20)}u0064tn_${hex}`, `&#x25;64tn_${hex}`]) {
    assert.ok(!redact(`a ${nested} b`).includes(hex.slice(0, 16)), nested)
  }
  // HTML inside HTML, an invalid UTF-8 byte before an encoded invisible, a long run.
  for (const form of [`dtn&amp;lowbar;${hex}`, `&amp;#100;tn_${hex}`, `%FF%64%74%6E%E2%80%8B%5F${hex}`, `${'%41'.repeat(4095)}%E2%80%8B%64%74%6E%E2%80%8B%5F${hex}`]) {
    assert.ok(!redact(form).includes(hex.slice(0, 16)), form.slice(-50))
  }
  // An encoded byte-order mark is a character, not a stall.
  assert.equal(redact(`%EF%BB%BF dtn_${hex}`), '%EF%BB%BF REDACTED')
  // Deep nesting is decoded; deeper than the pass limit, the text is withheld whole.
  const nest = (n) => { let t = `dtn_${hex}`; for (let i = 0; i < n; i += 1) t = t.replace(/[%_]/g, (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`); return t }
  assert.ok(!redact(`a ${nest(10)} b`).includes(hex.slice(0, 16)))
  assert.equal(redact(`a ${nest(20)} b`), 'REDACTED (text too deeply encoded to check)')
  // A cut takes only the key's own bytes out of a percent-encoded run.
  assert.equal(redact(`q=%E4%B8%AD%20%64tn_${hex}%20%E4%B8%AD&x=1`), 'q=%E4%B8%AD%20REDACTED%20%E4%B8%AD&x=1')
  // Many keys in one text stay linear.
  const keys = `dtn_${hex} `.repeat(20000)
  assert.equal(redact(keys), 'REDACTED '.repeat(20000))
  // A long backslash run is kept as it is.
  assert.equal(redact('\\'.repeat(100000)), '\\'.repeat(100000))
  // Any invisible format character or combining mark inside a key.
  for (const mark of ['\u2061', '\u180e', '\u0301']) assert.equal(redact(`${key.slice(0, 9)}${mark}${key.slice(9)}`), 'REDACTED', escape(mark))
  // References past the last code point are text, not errors.
  for (const ref of ['&#x110000;', '&#1114112;', '&#xFFFFFF;', '&#9999999;']) assert.equal(redact(`a ${ref} b`), `a ${ref} b`)
  const out = render({ sha: SHA, runUrl: 'u', results: [{ title: 'Cli', finalStatus: 'failed', failureReason: `env shows ${key}` }] }).join('\n')
  assert.ok(!out.includes(key) && out.includes('env shows REDACTED'))
})

test('render: report fields cannot break the table or link elsewhere', () => {
  const reportJson = JSON.stringify({ reward: { checklist: { 'a|b': 0, ok: 1 } }, metrics: { time: '1m|2', cost: '$1\n| x', errors: 0 } })
  const out = render({
    sha: SHA,
    runUrl: 'u',
    results: [
      { title: 'A', finalStatus: 'completed', outcome: 'succeeded', reportJson, reportUrl: 'https://evil.example/r' },
      { title: 'B', finalStatus: 'completed', outcome: 'goal|not', reportUrl: 'https://firegoat.ai/evals/daytona.io/reports/x(1)' },
    ],
  })
  const rowA = out.find((l) => l.startsWith('| A |'))
  assert.equal(rowA.split(/(?<!\\)\|/).length, 9)
  assert.ok(rowA.endsWith('| – |'))
  assert.ok(rowA.includes('| 0 |'))
  const rowB = out.find((l) => l.startsWith('| B |'))
  assert.equal(rowB.split(/(?<!\\)\|/).length, 9)
  assert.ok(rowB.includes('[report](https://firegoat.ai/evals/daytona.io/reports/x%281%29)'))
  const piped = render({ sha: SHA, runUrl: 'u', results: [{ title: 'P', finalStatus: 'failed', reportUrl: 'https://firegoat.ai/r/x|https://evil.example/|y' }] })
  const rowP = piped.find((l) => l.startsWith('| P |'))
  assert.equal(rowP.split(/(?<!\\)\|/).length, 9)
  assert.ok(rowP.includes('[report](https://firegoat.ai/r/x%7Chttps://evil.example/%7Cy)'))
})

test('render: agent text cannot autolink or cross-reference', () => {
  const out = render({
    sha: SHA,
    runUrl: 'u',
    results: [{ title: 'Py', finalStatus: 'failed', failureReason: 'see daytona/clients#609, GH-5, https://e.example/x and www.e.example' }],
  }).join('\n')
  const line = out.split('\n').find((l) => l.startsWith('- **Py'))
  for (const live of ['clients\\#609', 'clients#609', 'GH-5', 'https://', 'www.']) assert.ok(!line.includes(live), live)
  const refs = render({
    sha: SHA,
    runUrl: 'u',
    results: [{ title: 'Ts', finalStatus: 'failed', failureReason: `ping &commat;octocat, see &num;1, dtn&lowbar;${'c3'.repeat(16)}` }],
  }).join('\n')
  assert.ok(refs.includes('\\&commat;octocat') && refs.includes('\\&num;1') && refs.includes('REDACTED') && !refs.includes('c3c3'))
})

test('render: agent text cannot render as math', () => {
  const out = render({ sha: SHA, runUrl: 'u', results: [{ title: 'Py', finalStatus: 'failed', failureReason: 'is $O(n^2)$ and $\\Huge{x}$, cost $0.25' }] }).join('\n')
  const line = out.split('\n').find((l) => l.startsWith('- **Py'))
  assert.equal(line.replace(/<span>\$<\/span>/g, '').includes('$'), false)
  assert.equal((line.match(/<span>\$<\/span>/g) || []).length, 5)
})

test('render: the comment stays under GitHub\'s limit even when escaping grows the text', () => {
  const dollars = '$'.repeat(600)
  const results = Array.from({ length: 15 }, (_, i) => ({
    title: `E${i}`,
    finalStatus: 'failed',
    failureReason: dollars,
    frictionPoints: [1, 2, 3].map(() => ({ severity: 'minor', text: '$'.repeat(240) })),
  }))
  const out = render({ sha: SHA, runUrl: 'u', results }).join('\n')
  assert.ok(out.length < 65536, String(out.length))
  assert.ok(out.includes('| E14 |'))
  const huge = { reward: { checklist: { a: 1 } }, metrics: { time: 'x'.repeat(1000), cost: '$'.repeat(1000), errors: '$'.repeat(1000) } }
  const table = render({ sha: SHA, runUrl: 'u', results: Array.from({ length: 15 }, (_, i) => ({ title: `T${i}`, finalStatus: 'completed', outcome: '$'.repeat(1000), reportJson: JSON.stringify(huge) })) }).join('\n')
  assert.ok(table.length < 65536, String(table.length))
  const many = { reward: { checklist: Object.fromEntries(Array.from({ length: 30 }, (_, i) => [`${'$'.repeat(40)}${i}`, 0])) } }
  const checks = render({ sha: SHA, runUrl: 'u', results: Array.from({ length: 15 }, (_, i) => ({ title: `M${i}`, finalStatus: 'completed', outcome: 'succeeded', reportJson: JSON.stringify(many) })) }).join('\n')
  assert.ok(checks.length < 65536, String(checks.length))
  assert.ok(checks.includes('+27 more'))
  const cost = render({ sha: SHA, runUrl: 'u', results: [{ title: 'C', finalStatus: 'completed', outcome: 'succeeded', reportJson: JSON.stringify({ reward: { checklist: { a: 1 } }, metrics: { cost: '$0.46' } }) }] }).join('\n')
  assert.ok(cost.includes('| $0.46 |'))
})

test('render: long agent text is capped', () => {
  const out = render({ sha: SHA, runUrl: 'u', results: [{ title: 'Ts', finalStatus: 'failed', failureReason: 'x'.repeat(20000) }] }).join('\n')
  assert.ok(out.length < 2000)
})

test('render: re-run hint names the PR and main', () => {
  const out = render({ sha: SHA, runUrl: 'u', pr: '42', results: [] }).join('\n')
  assert.match(out, /gh workflow run ax_evals\.yaml --repo daytona\/clients --ref main -f pr=42 -f evals=all/)
})

test('render: escapes agent text', () => {
  const out = render({
    sha: SHA,
    runUrl: 'u',
    results: [{ title: 'CLI', finalStatus: 'failed', failureReason: 'ping @daytona/team a\\|b | c' }],
  }).join('\n')
  const line = out.split('\n').find((l) => l.startsWith('- **CLI'))
  assert.ok(line.includes('@\u200bdaytona/team'))
  assert.ok(line.includes('a\\\\\\|b \\| c'))
  const md = render({
    sha: SHA,
    runUrl: 'u',
    results: [{ title: 'Py', finalStatus: 'failed', failureReason: '![x](https://e.example/i.png) [click](https://e.example) <img src=x> **bold** `code`' }],
  }).join('\n')
  const mdLine = md.split('\n').find((l) => l.startsWith('- **Py'))
  assert.equal(
    mdLine,
    '- **Py:** \\!\\[x\\]\\(https:\u200b//e.example/i.png\\) \\[click\\]\\(https:\u200b//e.example\\) \\<img src=x\\> \\*\\*bold\\*\\* \\`code\\`',
  )
})

test('workflow: plan only starts for the ax-eval label, and for manual runs', () => {
  const workflow = fs.readFileSync(WORKFLOW, 'utf8')
  const planJob = workflow.slice(workflow.indexOf('  plan:'), workflow.indexOf('    steps:'))
  const condition = /if: >-\n([\s\S]*?)\n    [a-z]/.exec(planJob)[1].replace(/\s+/g, ' ').trim()
  assert.equal(
    condition,
    "(github.event_name == 'pull_request_target' && github.event.label.name == 'ax-eval' && github.event.pull_request.head.repo.full_name == github.repository) || github.event_name == 'workflow_dispatch'",
  )
  assert.match(workflow, /pull_request_target:\n    types: \[labeled\]\n    branches: \[main\]/)
  // pull_request_target is safe only while no job checks out or runs PR code.
  const triggers = workflow.slice(workflow.indexOf('\non:\n'), workflow.indexOf('\npermissions:'))
  const keys = triggers.split('\n').filter((line) => /^  [a-z_]+:/.test(line)).map((line) => line.trim())
  assert.deepEqual(keys, ['pull_request_target:', 'workflow_dispatch:'])
})

test('workflow: fork PRs never run PR-controlled code on label events', () => {
  const workflow = fs.readFileSync(WORKFLOW, 'utf8')
  const planJob = workflow.slice(workflow.indexOf('  plan:'), workflow.indexOf('    steps:'))
  const labelClause = /github\.event_name == 'pull_request_target'[^|]*\)/.exec(planJob)
  assert.ok(labelClause, 'plan job has a pull_request clause')
  assert.match(labelClause[0], /github\.event\.pull_request\.head\.repo\.full_name == github\.repository/)
})

test('setup: commit under test is parsed from URL_MAP, never silently main', () => {
  const common = fs.readFileSync(path.join(__dirname, 'setup', '_common.sh'), 'utf8')
  const snippet = common.slice(common.indexOf('REF=$('), common.indexOf('esac') + 'esac'.length)
  const ref = (urlMap) => {
    try {
      return execFileSync('bash', ['-c', `set -euo pipefail\n${snippet}\necho "$REF"`], {
        env: { ...process.env, URL_MAP: urlMap },
        stdio: ['ignore', 'pipe', 'ignore'],
      })
        .toString()
        .trim()
    } catch {
      return 'ERROR'
    }
  }
  assert.equal(ref(`git-ref.invalid=https://${SHA}.invalid`), SHA)
  assert.equal(ref(`x.com=https://a.b,git-ref.invalid=https://${SHA.toUpperCase()}.invalid`), SHA)
  assert.equal(ref('git-ref.invalid=https://abc1234.invalid'), 'ERROR')
  assert.equal(ref(''), 'main')
  assert.equal(ref('docs.example.com=https://preview.example.com'), 'main')
  assert.equal(ref('{"git-ref.invalid":"https://abc1234.invalid"}'), 'ERROR')
  assert.equal(ref('git-ref.invalid=https://not-a-sha.invalid'), 'ERROR')
})

test('sync: prompt text names the surface, the pre-installed build and the docs', () => {
  const out = execFileSync('python3', [path.join(__dirname, 'sync.py'), '--dry-run', '--only', 'cli-preview-url'], {
    env: { ...process.env, EVALS_API_KEY: '' },
  }).toString()
  const prompt = JSON.parse(out)
  assert.equal(prompt.eval, 'cli-preview-url')
  assert.equal(prompt.title, '[CI] clients v2 — CLI: Preview URL')
  assert.match(prompt.text, /^Using the Daytona CLI, start a web server/)
  assert.match(prompt.text, /installed at \/opt\/daytona-cli\/bin\/daytona/)
  assert.match(prompt.text, /DAYTONA_API_KEY/)
  assert.ok(prompt.checks['opens-without-credential'] && prompt.checks.surface && prompt.checks['sandbox-deleted'])
  assert.match(prompt.setupCommand, /install_go\n/)
})

test('setup: node_satisfies compares the node on PATH with an engines floor', () => {
  const common = fs.readFileSync(path.join(__dirname, 'setup', '_common.sh'), 'utf8')
  const fn = common.slice(common.indexOf('node_satisfies() {'), common.indexOf('install_node() {'))
  const ok = (floor) => {
    try {
      execFileSync('bash', ['-c', `${fn}\nnode_satisfies "$1"`, 'x', floor], { stdio: 'ignore' })
      return true
    } catch {
      return false
    }
  }
  const [major, minor] = process.versions.node.split('.').map(Number)
  assert.equal(ok(`>=${major}.${minor}.0`), true)
  assert.equal(ok(`>= ${major - 1}`), true)
  assert.equal(ok(`>=${major + 1}.0.0`), false)
  assert.equal(ok(`>=${major}.${minor + 1}.0`), false)
})

function saveResult(report, env = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ax-save-'))
  const out = path.join(dir, 'r.json')
  execFileSync(process.execPath, [path.join(__dirname, 'save-result.js'), out], {
    env: { ...process.env, NAME: 'cli-run-python', REPORT_JSON: JSON.stringify(report), ...env },
  })
  return JSON.parse(fs.readFileSync(out, 'utf8'))
}

test('save-result: the top-level narrative wins, legacy is the fallback, credentials are redacted', () => {
  const legacy = { keyFinding: 'legacy', frictionPoints: [{ severity: 'minor', desc: 'old' }] }
  const top = saveResult({ keyFinding: 'top', frictionPoints: [{ severity: 'major', desc: 'new' }], legacy })
  assert.equal(top.keyFinding, 'top')
  assert.deepEqual(top.frictionPoints, [{ severity: 'major', text: 'new' }])
  const fallback = saveResult({ keyFinding: null, frictionPoints: [], legacy })
  assert.equal(fallback.keyFinding, 'legacy')
  assert.deepEqual(fallback.frictionPoints, [{ severity: 'minor', text: 'old' }])
  const key = `dtn_${'b2'.repeat(16)}`
  const leaked = saveResult({ keyFinding: `ran env:\n${key}` }, { FAILURE_REASON: `also ${key}` })
  assert.ok(!JSON.stringify(leaked).includes(key))
  assert.equal(leaked.keyFinding, 'ran env:\nREDACTED')
  assert.ok(saveResult({ keyFinding: 'x'.repeat(5000) }).keyFinding.length <= 600)
  const odd = saveResult({ frictionPoints: [null, 'x', { severity: '@team **bold**', desc: 'd' }] })
  assert.deepEqual(odd.frictionPoints, [{ severity: 'other', text: 'd' }])
})

test('workflow: the harness comes from the base branch tip, and every job uses the commit plan used', () => {
  const workflow = fs.readFileSync(WORKFLOW, 'utf8')
  const refs = [...workflow.matchAll(/^\s+ref: (.+)$/gm)].map((m) => m[1].trim())
  assert.deepEqual(refs.filter((ref) => !ref.startsWith('18bd03')), [
    '${{ github.event.pull_request.base.ref || github.sha }}',
    '${{ needs.plan.outputs.harness }}',
    '${{ needs.plan.outputs.harness }}',
  ])
  assert.ok(refs.every((ref) => !/pull_request\.(base\.sha|head)/.test(ref)))
  const planJob = workflow.slice(workflow.indexOf('  plan:'), workflow.indexOf('  eval:'))
  assert.match(planJob, /harness: \$\{\{ steps\.harness\.outputs\.sha \}\}/)
  assert.match(planJob, /- id: harness\n\s+run: echo "sha=\$\(git rev-parse HEAD\)" >> "\$GITHUB_OUTPUT"/)
  assert.ok(planJob.indexOf('- id: harness') > planJob.indexOf('actions/checkout'), 'records the commit after the checkout')
  const save = workflow.slice(workflow.indexOf('- name: Save result'), workflow.indexOf('upload-artifact'))
  assert.doesNotMatch(save, /EVALS_API_KEY/)
})

test('workflow: the eval step redacts everything the evals action logs or outputs', () => {
  const workflow = fs.readFileSync(WORKFLOW, 'utf8')
  const step = workflow.slice(workflow.indexOf('      - id: eval'), workflow.indexOf('- name: Save result'))
  assert.match(step, /require\(`\$\{process\.env\.GITHUB_WORKSPACE\}\/scripts\/ax-evals\/redact\.js`\)/)
  for (const method of ['setOutput', 'setFailed', 'warning', 'notice', 'info']) {
    assert.match(step, new RegExp(`${method}: \\([^)]*\\) => (\\{[^}]*)?core\\.${method}\\([^\\n]*redact\\(`), method)
  }
  assert.match(step, /await run\(\{ core: redacting,/)
})
