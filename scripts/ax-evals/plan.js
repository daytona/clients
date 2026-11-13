// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

// Decides whether and what to evaluate for an `ax-eval` label or a manual workflow run.
// Sets outputs: matrix (JSON list of {name, title, promptId}), sha, pr.

const fs = require('fs')
const path = require('path')
const { upsertComment } = require('./comment.js')

const ALIASES = { ts: 'typescript', js: 'typescript', py: 'python' }
// The endpoint's base `permission` (admin, write, read or none): `role_name` can be a
// custom role's name, and maintain and triage report as write and read.
const WRITE_PERMISSIONS = new Set(['admin', 'write'])
// evals.json paths describe the 2.x layout; release/v0 has its own AX evals.
const BASE_BRANCH = 'main'
const HEADING = '### AX evals (Firegoat)'
const MAX_LISTED_FILES = 3000
const DISPATCH_HINT = `gh workflow run ax_evals.yaml --repo daytona/clients --ref ${BASE_BRANCH} -f pr=<number> -f evals=all`

function loadConfig() {
  return JSON.parse(fs.readFileSync(path.join(__dirname, 'evals.json'), 'utf8'))
}

// Every eval as {name, surface, task}; name is <surface>-<task>.
function evalsOf(config) {
  return Object.keys(config.surfaces).flatMap((surface) =>
    Object.keys(config.tasks).map((task) => ({ name: `${surface}-${task}`, surface, task })),
  )
}

function parseEvals(input) {
  return (input || '')
    .split(/[\s,]+/)
    .filter(Boolean)
    .map((token) => token.toLowerCase())
}

// A token selects every eval of a surface (`python`, alias `py`), every surface of a
// task (`preview-url`), or one eval (`cli-preview-url`). Returns { unknown } for an unknown token.
function selectByTokens(config, tokens) {
  const all = evalsOf(config)
  const selected = new Set()
  for (const raw of tokens) {
    const token = ALIASES[raw] || raw
    const matches = all.filter((e) => token === 'all' || e.name === token || e.surface === token || e.task === token)
    if (matches.length === 0) return { unknown: raw }
    matches.forEach((e) => selected.add(e.name))
  }
  return { names: all.map((e) => e.name).filter((name) => selected.has(name)) }
}

// Every task of each surface whose sources the PR changes; all evals for a shared path.
// An entry ending in `/` is a directory and matches by prefix; any other entry is one file.
function selectByPaths(config, files) {
  const matches = (file, entry) => (entry.endsWith('/') ? file.startsWith(entry) : file === entry)
  const touches = (entries) => files.some((file) => entries.some((entry) => matches(file, entry)))
  const all = evalsOf(config)
  if (touches(config.sharedPaths)) return all.map((e) => e.name)
  return all.filter((e) => touches(config.surfaces[e.surface].paths)).map((e) => e.name)
}

function titleOf(config, name) {
  const e = evalsOf(config).find((candidate) => candidate.name === name)
  return `${config.surfaces[e.surface].name}: ${config.tasks[e.task].name}`
}

async function run({ github, context, core }) {
  const config = loadConfig()
  const repo = context.repo
  const isDispatch = context.eventName === 'workflow_dispatch'
  const prNumber = isDispatch ? Number(context.payload.inputs.pr) : context.payload.pull_request.number
  const actor = context.payload.sender.login
  core.setOutput('matrix', '[]')
  if (!Number.isInteger(prNumber) || prNumber <= 0) {
    return core.setFailed(`Not a pull request number: ${context.payload.inputs?.pr}`)
  }
  const requested = isDispatch ? parseEvals(context.payload.inputs.evals) : []

  let role = 'none'
  try {
    const { data: permission } = await github.rest.repos.getCollaboratorPermissionLevel({ ...repo, username: actor })
    role = permission.permission
  } catch (error) {
    // 404 for users who are not collaborators at all.
    core.info(`Could not read @${actor}'s permission: ${error.message}`)
  }
  if (!WRITE_PERMISSIONS.has(role)) {
    return core.notice(`@${actor} has '${role}' access; AX evals need write access`)
  }

  const { data: pr } = await github.rest.pulls.get({ ...repo, pull_number: prNumber })
  if (pr.state !== 'open') return core.notice(`PR #${prNumber} is ${pr.state}`)
  if (pr.base.ref !== BASE_BRANCH) {
    return core.notice(`PR #${prNumber} targets ${pr.base.ref}; these AX evals only cover PRs against ${BASE_BRANCH}`)
  }
  if (!pr.head.repo || pr.head.repo.full_name !== `${repo.owner}/${repo.repo}`) {
    return core.notice('AX evals are not supported on pull requests from forks')
  }

  let selected
  let files = null
  if (requested.length > 0) {
    const { names, unknown } = selectByTokens(config, requested)
    if (unknown) {
      const valid = ['all', ...Object.keys(config.surfaces), ...Object.keys(config.tasks), '<surface>-<task>']
      return core.setFailed(`Unknown eval '${unknown}'. Valid: ${valid.join(', ')}`)
    }
    selected = names
  } else {
    files = await github.paginate(github.rest.pulls.listFiles, { ...repo, pull_number: prNumber, per_page: 100 })
    // The API lists at most 3,000 files; past that, the changed surfaces are unknown.
    selected =
      files.length >= MAX_LISTED_FILES
        ? evalsOf(config).map((e) => e.name)
        : selectByPaths(
            config,
            files.flatMap((file) => [file.filename, file.previous_filename].filter(Boolean)),
          )
  }

  const sha = pr.head.sha
  const runUrl = `${context.serverUrl}/${repo.owner}/${repo.repo}/actions/runs/${context.runId}`
  // A label run uses main's harness. The GitHub side (workflow, scripts, selection)
  // can be tested by dispatching from the PR's branch; prompts and setup scripts live
  // on Firegoat and only change when a merge to main syncs them.
  const names = (files || []).flatMap((file) => [file.filename, file.previous_filename].filter(Boolean))
  const harness = names.some(
    (name) => (name.startsWith('scripts/ax-evals/') && !name.startsWith('scripts/ax-evals/setup/')) || name === '.github/workflows/ax_evals.yaml',
  )
  const prompts = names.some((name) => name.startsWith('scripts/ax-evals/setup/') || name === 'scripts/ax-evals/evals.json')
  // Named only when it is plain, so it is safe both in the shell and in Markdown.
  const ref = /^[A-Za-z0-9._/-]+$/.test(pr.head.ref) ? pr.head.ref : "<this PR's branch>"
  const harnessNotes = [
    ...(harness
      ? [`This PR changes the AX evals workflow or scripts, which a run from \`main\` does not use: to test them, dispatch with \`--ref ${ref}\` instead.`]
      : []),
    ...(prompts
      ? ['Prompt text, checks and setup scripts are stored on Firegoat and change only when a merge to `main` syncs them, so no run tests those edits before merge.']
      : []),
  ]
  if (selected.length === 0) {
    await upsertComment(github, repo, prNumber, context.runId, [
      HEADING,
      '',
      `This PR doesn't change any SDK or CLI sources (commit \`${sha.slice(0, 7)}\`), so nothing was evaluated.`,
      `To run evals anyway: \`${DISPATCH_HINT.replace('<number>', prNumber)}\` (replace \`all\` with a surface such as \`cli\`, a task such as \`preview-url\`, or one eval such as \`cli-preview-url\`).`,
      ...harnessNotes,
    ]).catch((error) => core.warning(`Could not write the AX evals comment: ${error.message}`))
    return
  }

  const missing = selected.filter((name) => !config.promptIds[name])
  if (missing.length > 0) {
    return core.setFailed(`No Firegoat prompt for ${missing.join(', ')}: run scripts/ax-evals/sync.py to create it`)
  }
  const matrix = selected.map((name) => ({ name, title: titleOf(config, name), promptId: config.promptIds[name] }))
  core.setOutput('matrix', JSON.stringify(matrix))
  core.setOutput('sha', sha)
  core.setOutput('pr', String(prNumber))
  core.setOutput('notes', JSON.stringify(harnessNotes))
  // The pending comment is cosmetic (the report job rewrites it): a failed write must
  // not fail the plan, which would drop the evals.
  await upsertComment(github, repo, prNumber, context.runId, [
    HEADING,
    '',
    `⏳ Running ${matrix.length} eval${matrix.length === 1 ? '' : 's'} on commit \`${sha.slice(0, 7)}\`: ${matrix.map((m) => m.title).join(', ')}. Results usually take 10–20 minutes, longer when the Firegoat queue is busy ([workflow run](${runUrl})).`,
    ...harnessNotes,
  ]).catch((error) => core.warning(`Could not write the pending AX evals comment: ${error.message}`))
}

module.exports = run
module.exports.evalsOf = evalsOf
module.exports.parseEvals = parseEvals
module.exports.selectByTokens = selectByTokens
module.exports.selectByPaths = selectByPaths
module.exports.HEADING = HEADING
module.exports.DISPATCH_HINT = DISPATCH_HINT
