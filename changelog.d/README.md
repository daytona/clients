# Changelog fragments for the SDKs

Unreleased SDK changes live here, **one file per user-visible change** (one
changelog bullet each; a PR with several changes adds several files), instead
of an `## [Unreleased]` section in [`../CHANGELOG.md`](../CHANGELOG.md) — so
concurrent PRs never conflict on the changelog. At release time the
prepare-release workflow runs
`python3 scripts/changelog/assemble.py assemble --dir changelog.d --changelog CHANGELOG.md --version X.Y.Z --pr-links`,
which folds every fragment into `CHANGELOG.md` under the new version heading
and deletes the fragments. The rules:

- **Name:** `<id>.<type>.md`, where `<type>` is one of `breaking`, `added`,
  `changed`, `deprecated`, `removed`, `fixed`, `security`, and `<id>` is the PR
  number (`1234.added.md`), the PR number plus a slug when one PR needs several
  fragments of the same type (`1234-watch-exit.added.md`), or a plain lowercase
  slug when the PR number is not known yet (`signed-preview-links.fixed.md`).
  This `README.md` is the only file the tool ignores.
- **Content:** the markdown for one bullet, without the leading `- ` and without
  indentation; hard-wrap continuation lines at column 0. Start with a bold
  title (`**Signed preview links** — …`), as the existing entries do.
- **`(#PR)` convention:** the SDK changelog appends ` (#N)` to every bullet whose
  id starts with PR number `N` (`--pr-links`). You may write `(#N)` yourself —
  the tool does not duplicate a reference that is already in the text — but you
  do not have to.

`python3 scripts/changelog/assemble.py check --dir changelog.d` (also run in CI)
fails on a misnamed or empty fragment.
