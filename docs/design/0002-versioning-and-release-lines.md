# 0002. One version line and two release lines

Status: implemented

## Context

Three artifacts ship from this repository: the Python SDK on PyPI, the
TypeScript SDK on npm, and the `daytona` CLI on GitHub Releases and Homebrew.
Each talks to the Daytona API. Users need to know which API an installed
package was built for, and maintainers need a release process that can't
publish mismatched versions or a stable release by accident.

The repository also carries two generations of the SDKs: the current 2.x SDK
and the 0.x SDK that existing users still depend on. Both need to keep
publishing to the same package names.

## Decision

**One version line.** Both SDKs and the CLI carry the platform version: an SDK
release has the version of the platform release whose API it was built for.
Python and TypeScript always release together on the same number. There's no
per-SDK patch release. The full policy is in
[`../../VERSIONING.md`](../../VERSIONING.md).

**The contract decides the number.** The next version follows the diff of the
API contracts under [`contract/`](../../contract/) since the last release:
breaking spec changes bump MAJOR, additive ones bump MINOR, and everything
else is a PATCH. Before `2.0.0`, every spec change takes the next
`2.0.0-alpha.N` instead.

**No version bumps in git.** The `version` fields in
[`../../sdk-python/pyproject.toml`](../../sdk-python/pyproject.toml),
[`../../sdk-typescript/package.json`](../../sdk-typescript/package.json) and
[`../../sdk-typescript/src/version.ts`](../../sdk-typescript/src/version.ts) are
placeholders. The CLI's `Version` in
[`../../cli/internal/app/app.go`](../../cli/internal/app/app.go) defaults to
`0.0.0-dev`. The publish workflow,
[`../../.github/workflows/sdk_publish.yaml`](../../.github/workflows/sdk_publish.yaml),
takes one `vX.Y.Z[-pre.N]` input, stamps it into the manifests inside the
runner, and commits nothing. A successful run tags the published commit with
that version. [`../../scripts/sdk-version.sh`](../../scripts/sdk-version.sh)
derives each registry's spelling: `v2.0.0-alpha.3` becomes `2.0.0-alpha.3` on
npm and the CLI, and `2.0.0a3` (PEP 440) on PyPI.

**Pre-release channels follow the version.** The version's pre-release
identifier picks the npm dist-tag, so nobody chooses it by hand:

| Version | npm dist-tag | PyPI |
|---|---|---|
| `vX.Y.Z` | `latest` | normal release |
| `vX.Y.Z-alpha.N` | `alpha` | `X.Y.ZaN` |
| `vX.Y.Z-beta.N` | `beta` | `X.Y.ZbN` |
| `vX.Y.Z-rc.N` | `next` | `X.Y.ZrcN` |

A pre-release never becomes `latest`.

**Alpha only until general availability.** The publish gate rejects any
version that isn't an alpha. Widening to release candidates, and then to
stable, means relaxing that gate and nothing else: the dist-tag already
follows the version.

**Two release lines, same-named workflows.**

| Line | Branch | Workflows |
|---|---|---|
| 2.x | `main` | `prepare-release.yaml`, `sdk_publish.yaml` |
| 0.x | `release/v0` | that branch's own `prepare-release.yaml`, `release.yaml`, `sdk_publish.yaml` |

PyPI and npm Trusted Publishing bind a package to a repository, a workflow
file name and an environment. Keeping the same file names on both lines lets
one registration per package serve both. The dispatch ref picks the line, and
the 2.x gate refuses a 0.x version. Details are in
[`../../RELEASING.md`](../../RELEASING.md).

Unreleased changes live as one fragment per user-visible change under
[`changelog.d/`](../../changelog.d/), and a prepared release folds them into
[`../../CHANGELOG.md`](../../CHANGELOG.md).

## Alternatives

**Independent versions per SDK and for the CLI.** Each artifact could follow
its own SemVer line. Users would then need a compatibility matrix to know
which SDK matches which API. Sharing the platform's number makes
`pip show daytona` answer that question directly.

**Bump versions in a commit before each release.** Version-bump commits
conflict across branches, can be forgotten, and leave the repository claiming
a version that may never publish. Stamping at publish time keeps git free of
release bookkeeping.

**One publish workflow on `main` that builds either line.** The 0.x and 2.x
trees share no build tooling, so a single file would branch on every step.
Separate copies with the same file names keep each line simple without a
second Trusted Publishing registration.

**Choose the npm dist-tag as a workflow input.** A manual choice can put a
pre-release on `latest`. Deriving it from the version removes the mistake.

## Consequences

- A fix in one SDK ships only when the next release publishes both.
- Contributors never edit version fields. Maintainers pick no numbers by hand
  either: the contract diff and the version input decide.
- Every release before general availability is an alpha. npm users install it
  with `npm install @daytona/sdk@alpha`; Python users pin the exact alpha,
  because a release candidate published to PyPI earlier sorts above every
  alpha.
- Registries refuse to re-upload a version. A half-finished publish resumes
  with `force_republish`, which skips targets that already landed and never
  overwrites one.
- Renaming `sdk_publish.yaml`, or the `release` environment, breaks
  publishing until both registries are updated. Changing it on one line
  requires the same change on the other.
