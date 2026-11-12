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
[`../VERSIONING.md`](../VERSIONING.md).

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

**Channels follow the version.** The version's pre-release identifier picks
the channel, so nobody chooses a dist-tag, a release kind or a Homebrew
formula by hand ([`../../scripts/release-channel.sh`](../../scripts/release-channel.sh)):

| Version | npm dist-tag | PyPI | CLI GitHub Release | Homebrew formula |
|---|---|---|---|---|
| stable 2.x `v2.Y.Z` | `latest` | normal release | normal release (Latest when it is the highest stable release) | `daytona` |
| `vX.Y.Z-rc.N` | `next` | `X.Y.ZrcN` | pre-release | `daytona-rc` |
| `vX.Y.Z-alpha.N` | `alpha` | `X.Y.ZaN` | pre-release | `daytona-alpha` |

A pre-release never becomes `latest` or the Latest release. The publish gate
takes alpha, rc and stable versions; no channel follows betas, so the gate
refuses them, and it refuses a stable 2.x version below the highest stable
2.x release (no stable backports). Stable 0.x releases are the exception to
the stable row: they
publish from `release/v0` to the `daytona@0` formula and, once a stable 2.x
release exists, under the npm line tag `v0-latest`.

**Two release lines, same-named workflows.**

| Line | Branch | Workflows |
|---|---|---|
| 2.x | `main` | `prepare-release.yaml`, `sdk_publish.yaml` |
| 0.x | `release/v0` | that branch's own `prepare-release.yaml`, `release.yaml`, `sdk_publish.yaml` |

PyPI and npm Trusted Publishing bind a package to a repository, a workflow
file name and an environment. Keeping the same file names on both lines lets
one registration per package serve both. The dispatch ref picks the line, and
the 2.x gate refuses a 0.x version. Details are in
[`../RELEASING.md`](../RELEASING.md).

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
- Before `2.0.0`, 2.x releases are pre-releases: alphas, the npm
  `2.0.0-rc.1` / PyPI `2.0.0rc1` published before the current policy, and
  release candidates from `2.0.0-rc.2` on. Pre-release users install a
  channel explicitly (`npm install @daytona/sdk@alpha` or `@next`, a pinned
  PyPI version, `--channel alpha` for the CLI installer); Python users pin the
  exact pre-release, because a release candidate published to PyPI before
  `2.0.0` sorts above every `2.0.0` alpha.
- Registries refuse to re-upload a version. A half-finished publish resumes
  with `force_republish`, which skips targets that already landed and never
  overwrites one.
- Renaming `sdk_publish.yaml`, or the `release` environment, breaks
  publishing until both registries are updated. Changing it on one line
  requires the same change on the other.
