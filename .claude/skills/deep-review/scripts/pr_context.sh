#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0
# Read public PR metadata and complete review conversations; never fetch or post.
set -euo pipefail

if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then
  cat <<'HELP'
Usage: pr_context.sh <pr-identifier>
Reads daytona/clients only. Requires Git, jq, and authenticated gh read access.
Exact PR head/base commits must already exist locally. Does not fetch.
Prints JSON with local diff context, PR metadata/checks, and paginated feedback.
HELP
  exit 0
fi
[[ $# -eq 1 && "$1" =~ ^[0-9]+$ ]] || { echo 'Expected one numeric PR identifier' >&2; exit 2; }
for tool in git jq gh; do
  command -v "$tool" >/dev/null || { echo "Missing tool: $tool" >&2; exit 2; }
done
gh auth status >/dev/null 2>&1 || { echo 'GitHub CLI read access is unavailable' >&2; exit 2; }
pr="$1"
repo=daytona/clients
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
gh pr view "$pr" --repo "$repo" \
  --json title,state,isDraft,url,body,baseRefName,baseRefOid,headRefName,headRefOid,mergeable,mergeStateStatus,statusCheckRollup \
  > "$tmp/meta.json"
head_sha="$(jq -er .headRefOid "$tmp/meta.json")"
base_sha="$(jq -er .baseRefOid "$tmp/meta.json")"
for sha in "$head_sha" "$base_sha"; do
  git cat-file -e "$sha^{commit}" 2>/dev/null || {
    echo "Missing pinned commit $sha; ask the author to supply current refs" >&2
    exit 2
  }
done
bash "$(dirname "$0")/branch_context.sh" "$head_sha" "$base_sha" > "$tmp/context.json"

# Paginate each connection separately; nested thread replies have their own cursor.
threads_query='query($pr:Int!,$endCursor:String){repository(owner:"daytona",name:"clients"){
  pullRequest(number:$pr){reviewThreads(first:100,after:$endCursor){
    pageInfo{hasNextPage endCursor} nodes{id isResolved isOutdated path line
      comments(first:100){pageInfo{hasNextPage endCursor}
        nodes{author{login} body createdAt url}}}}}}}'
replies_query='query($id:ID!,$endCursor:String){node(id:$id){... on PullRequestReviewThread{
  comments(first:100,after:$endCursor){pageInfo{hasNextPage endCursor}
    nodes{author{login} body createdAt url}}}}}'
gh api graphql --paginate -f query="$threads_query" -F pr="$pr" \
  --jq '.data.repository.pullRequest.reviewThreads.nodes[]' | jq -s . > "$tmp/threads.json"
jq -r '.[] | select(.comments.pageInfo.hasNextPage) |
  [.id, .comments.pageInfo.endCursor] | @tsv' "$tmp/threads.json" > "$tmp/cursors.tsv"
while IFS=$'\t' read -r id cursor; do
  gh api graphql --paginate -f query="$replies_query" -f id="$id" -f endCursor="$cursor" \
    --jq '.data.node.comments.nodes[]' | jq -s . > "$tmp/replies.json"
  jq --arg id "$id" --slurpfile more "$tmp/replies.json" \
    'map(if .id == $id then .comments.nodes += $more[0] |
      .comments.pageInfo.hasNextPage = false else . end)' \
    "$tmp/threads.json" > "$tmp/threads.next.json"
  mv "$tmp/threads.next.json" "$tmp/threads.json"
done < "$tmp/cursors.tsv"
gh api --paginate --slurp "repos/$repo/pulls/$pr/reviews?per_page=100" \
  | jq 'add // []' > "$tmp/reviews.json"
gh api --paginate --slurp "repos/$repo/issues/$pr/comments?per_page=100" \
  | jq 'add // []' > "$tmp/comments.json"
latest="$(gh pr view "$pr" --repo "$repo" --json headRefOid,baseRefOid)"
jq -e --arg head "$head_sha" --arg base "$base_sha" \
  '.headRefOid == $head and .baseRefOid == $base' <<< "$latest" >/dev/null || {
  echo 'PR head or base moved during collection; collect again' >&2; exit 2;
}
jq -n --slurpfile meta "$tmp/meta.json" --slurpfile context "$tmp/context.json" \
  --slurpfile threads "$tmp/threads.json" --slurpfile reviews "$tmp/reviews.json" \
  --slurpfile comments "$tmp/comments.json" \
  '{repository: "daytona/clients", pr: $meta[0], context: $context[0],
    threads: $threads[0], reviews: $reviews[0], comments: $comments[0]}'
