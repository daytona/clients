// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

// Run with: node --test hack/ax-evals/ax-evals.test.js

const assert = require('node:assert/strict')
const { execFileSync } = require('node:child_process')
const fs = require('node:fs')
const path = require('node:path')
const { test } = require('node:test')

const plan = require('./plan.js')
const { render, loadResults } = require('./report.js')
const { upsertComment } = require('./comment.js')
const config = require('./evals.json')

const BOT = { login: 'github-actions[bot]' }
const SHA = 'c8114371cbfae6ee7ffce6aea618f8de7f161c91'
const REPO = { owner: 'daytona', repo: 'clients' }

function mockGithub({
  role = 'write',
  fork = false,
  base = 'release/v0',
  files = [{ filename: 'sdk-go/a.go' }],
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
            head: { sha: SHA, repo: { full_name: fork ? 'someone/clients' : 'daytona/clients' } },
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

test('parseEvals', () => {
  assert.deepEqual(plan.parseEvals(''), [])
  assert.deepEqual(plan.parseEvals(undefined), [])
  assert.deepEqual(plan.parseEvals(' python, TS  go'), ['python', 'ts', 'go'])
})

test('selectByPaths', () => {
  assert.deepEqual(plan.selectByPaths(config, ['sdk-python/src/x.py', 'README.md']), ['python'])
  assert.deepEqual(plan.selectByPaths(config, ['api-client-go/x.go']), ['go', 'cli'])
  assert.deepEqual(plan.selectByPaths(config, ['go.work']), ['go', 'cli'])
  assert.deepEqual(plan.selectByPaths(config, ['docs.md']), [])
  assert.equal(plan.selectByPaths(config, ['openapi-specs/api.json']).length, Object.keys(config.evals).length)
})

test('plan: changed SDKs for a write user', async () => {
  const github = mockGithub()
  const outputs = await runPlan(github, dispatchContext(''))
  assert.deepEqual(names(outputs), ['go'])
  assert.equal(outputs.sha, SHA)
  assert.equal(outputs.pr, '7')
  assert.match(github.calls[0][1], /Running/)
})

test('plan: label trigger', async () => {
  const context = {
    ...dispatchContext(''),
    eventName: 'pull_request',
    payload: { pull_request: { number: 7 }, sender: { login: 'dev' } },
  }
  assert.deepEqual(names(await runPlan(mockGithub(), context)), ['go'])
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

test('plan: forks, unknown evals, selection, renames, nothing changed', async () => {
  assert.match((await runPlan(mockGithub({ fork: true }), dispatchContext('all'))).notice, /forks/)
  const toMain = await runPlan(mockGithub({ base: 'main' }), dispatchContext('all'))
  assert.match(toMain.notice, /targets main/)
  assert.equal(toMain.matrix, '[]')
  assert.match((await runPlan(mockGithub(), dispatchContext('rust'))).failed, /Unknown eval/)
  assert.deepEqual(names(await runPlan(mockGithub(), dispatchContext('py, ts python'))), ['python', 'typescript'])
  assert.equal(
    JSON.parse((await runPlan(mockGithub(), dispatchContext('all'))).matrix).length,
    Object.keys(config.evals).length,
  )
  const renamed = mockGithub({ files: [{ filename: 'legacy/x.rb', previous_filename: 'sdk-ruby/x.rb' }] })
  assert.deepEqual(names(await runPlan(renamed, dispatchContext(''))), ['ruby'])
  const docsOnly = mockGithub({ files: [{ filename: 'README.md' }] })
  assert.equal((await runPlan(docsOnly, dispatchContext(''))).matrix, '[]')
  assert.match(docsOnly.calls[0][1], /nothing was evaluated/)
  assert.match(docsOnly.calls[0][1], /--ref release\/v0 -f pr=7 -f evals=all/)
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

  github = mockGithub({ comments: comments.slice(1) })
  github.rest.issues.deleteComment = async () => {
    throw Object.assign(new Error('Not Found'), { status: 404 })
  }
  assert.equal(await upsertComment(github, REPO, 7, 300, ['x']), true)
})

test('loadResults: reads <eval>.json from one directory, missing ones as not started', () => {
  const dir = fs.mkdtempSync(path.join(require('node:os').tmpdir(), 'ax-results-'))
  fs.writeFileSync(
    path.join(dir, 'typescript.json'),
    JSON.stringify({ name: 'typescript', finalStatus: 'completed', outcome: 'succeeded' }),
  )
  const results = loadResults(dir, [
    { name: 'typescript', title: 'TypeScript SDK' },
    { name: 'go', title: 'Go SDK' },
  ])
  assert.deepEqual(
    results.map((r) => [r.title, r.finalStatus, r.outcome]),
    [
      ['TypeScript SDK', 'completed', 'succeeded'],
      ['Go SDK', '', undefined],
    ],
  )
})

test('workflow: results are written and downloaded as <eval>.json in one directory', () => {
  const workflow = fs.readFileSync(path.join(__dirname, '..', '..', '.github', 'workflows', 'ax_evals.yaml'), 'utf8')
  const evalJob = workflow.slice(workflow.indexOf('  eval:'), workflow.indexOf('  report:'))
  const reportJob = workflow.slice(workflow.indexOf('  report:'))
  assert.match(evalJob, /save-result\.js "result\/\$\{NAME\}\.json"/)
  assert.match(evalJob, /path: result\/\$\{\{ matrix\.eval\.name \}\}\.json/)
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
    ],
  }).join('\n')
  const expected = {
    A: '❌ goal_not_met',
    B: '💥 Failed',
    C: '↪️ Superseded',
    D: '⏱️ Timed out',
    E: '⚠️ Not started',
    F: '⚠️ Succeeded, checks failed',
    G: '✅ Succeeded',
    H: '⚠️ Succeeded, checks missing',
  }
  for (const [title, label] of Object.entries(expected))
    assert.ok(out.includes(`| ${title} | ${label} |`), `${title}: ${label}`)
  assert.doesNotMatch(out, /All evals succeeded/)
})

test('render: re-run hint names the PR and release/v0', () => {
  const out = render({ sha: SHA, runUrl: 'u', pr: '42', results: [] }).join('\n')
  assert.match(out, /gh workflow run ax_evals\.yaml --repo daytona\/clients --ref release\/v0 -f pr=42 -f evals=all/)
})

test('render: escapes agent text', () => {
  const out = render({
    sha: SHA,
    runUrl: 'u',
    results: [{ title: 'Go', finalStatus: 'failed', failureReason: 'ping @daytona/team a\\|b | c' }],
  }).join('\n')
  const line = out.split('\n').find((l) => l.startsWith('- **Go'))
  assert.ok(line.includes('@\u200bdaytona/team'))
  assert.ok(line.includes('a\\\\\\|b \\| c'))
})

test('workflow: fork PRs never run PR-controlled code on label events', () => {
  const workflow = fs.readFileSync(path.join(__dirname, '..', '..', '.github', 'workflows', 'ax_evals.yaml'), 'utf8')
  const plan = workflow.slice(workflow.indexOf('  plan:'), workflow.indexOf('    steps:'))
  const labelClause = /github\.event_name == 'pull_request'[^|]*\)/.exec(plan)
  assert.ok(labelClause, 'plan job has a pull_request clause')
  assert.match(labelClause[0], /github\.event\.pull_request\.head\.repo\.full_name == github\.repository/)
})

test('setup: commit under test is parsed from URL_MAP, never silently release/v0', () => {
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
  assert.equal(ref('x.com=https://a.b,git-ref.invalid=https://ABC1234.invalid'), 'abc1234')
  assert.equal(ref(''), 'release/v0')
  assert.equal(ref('docs.example.com=https://preview.example.com'), 'release/v0')
  assert.equal(ref('{"git-ref.invalid":"https://abc1234.invalid"}'), 'ERROR')
  assert.equal(ref('git-ref.invalid=https://not-a-sha.invalid'), 'ERROR')
})
