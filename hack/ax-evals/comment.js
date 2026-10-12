// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

// Sticky PR comment shared by every AX eval run. Each body records the workflow run
// that wrote it, so a slower, older run never overwrites a newer one.

const MARKER = '<!-- ax-evals -->'
const RUN_ID_PATTERN = /<!-- ax-evals-run:(\d+) -->/

async function upsertComment(github, repo, prNumber, runId, lines) {
  const body = [MARKER, `<!-- ax-evals-run:${runId} -->`, ...lines].join('\n')
  const comments = await github.paginate(github.rest.issues.listComments, {
    ...repo,
    issue_number: prNumber,
    per_page: 100,
  })
  const existing = comments.find((comment) => comment.body && comment.body.startsWith(MARKER))
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
