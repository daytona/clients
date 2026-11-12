// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

// Sticky PR comment shared by every AX eval run. Each body records the workflow run
// that wrote it, so a slower, older run never overwrites a newer one. Report jobs are
// serialized per PR by the workflow's concurrency group; if two runs still race to
// create the comment, the next write keeps the oldest one and deletes the rest.

const MARKER = '<!-- ax-evals -->'
const RUN_ID_PATTERN = /<!-- ax-evals-run:(\d+) -->/
const BOT_LOGIN = 'github-actions[bot]'

async function upsertComment(github, repo, prNumber, runId, lines) {
  const body = [MARKER, `<!-- ax-evals-run:${runId} -->`, ...lines].join('\n')
  const comments = await github.paginate(github.rest.issues.listComments, {
    ...repo,
    issue_number: prNumber,
    per_page: 100,
  })
  const ours = comments.filter(
    (comment) => comment.user && comment.user.login === BOT_LOGIN && comment.body && comment.body.startsWith(MARKER),
  )
  const [existing, ...duplicates] = ours
  for (const duplicate of duplicates) {
    // A concurrent run may have deleted it already; a leftover duplicate is cosmetic.
    await github.rest.issues.deleteComment({ ...repo, comment_id: duplicate.id }).catch(() => undefined)
  }
  if (!existing) {
    await github.rest.issues.createComment({ ...repo, issue_number: prNumber, body })
    return true
  }
  const writtenBy = RUN_ID_PATTERN.exec(existing.body)
  if (writtenBy && Number(writtenBy[1]) > Number(runId)) return false
  await github.rest.issues.updateComment({ ...repo, comment_id: existing.id, body })
  return true
}

module.exports = { MARKER, upsertComment }
