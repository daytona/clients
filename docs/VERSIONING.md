# Versioning Policy

The Daytona SDKs — Python [`daytona`](https://pypi.org/project/daytona/)
(import `daytona`) and TypeScript [`@daytona/sdk`](https://www.npmjs.com/package/@daytona/sdk) —
and the `daytona` CLI carry the **platform version**: a
[Semantic Versioning 2.0.0](https://semver.org/) `MAJOR.MINOR.PATCH`, with a
`-alpha.N` / `-rc.N` pre-release identifier while the platform is pre-stable
(PyPI uses the PEP 440 spelling of the same number, see below).

## One version line

The SDKs and the CLI share one version line with the Daytona platform: an SDK
release carries the version of the platform release whose API it was built
for. `pip show daytona` and `npm ls @daytona/sdk` therefore name the API
version the installed package targets (`npm view @daytona/sdk version`
reports the registry's `latest` dist-tag, which the alpha channel never
moves), and the platform reports the same number from its health endpoint.

What follows from that:

- **No version is ever bumped in git.** The `version` fields in
  `sdk-python/pyproject.toml`, `sdk-typescript/package.json` and
  `sdk-typescript/src/version.ts` are placeholders, realigned to the latest
  published release when convenient and overwritten at publish time.
- **One version, every target.** The publish workflow
  ([RELEASING.md](RELEASING.md)) takes a single `vX.Y.Z[-pre.N]` input and
  derives each registry's spelling from it. A successful run records the
  version as the tag `vX.Y.Z[-pre.N]` on the published commit; that tag and
  the run's Summary page are the record.
- **Not every platform release republishes the SDKs.** A platform release that
  changes nothing the SDKs can see may skip publishing, and the installed SDK
  then stays one release behind, within the compatibility window below.

## What the numbers mean

The next version is decided by the **diff of the public API specifications**
(the Daytona API and toolbox API contracts under [`contract/`](../contract/)) since
the last published release, not by what changed in the SDK trees. Given `MAJOR.MINOR.PATCH`,
the next release increments:

- **MAJOR** — a breaking change to the API spec: an endpoint, field or error
  code removed or renamed, a type narrowed, established behavior changed in a
  way existing callers cannot absorb.
- **MINOR** — an additive change to the API spec: new endpoints, new optional
  fields or parameters, new error codes, new capabilities that leave existing
  calls working.
- **PATCH** — a compatible change: a spec diff that alters no shape callers
  depend on (descriptions, examples, tightened documentation), or no spec
  change at all together with fixes in the implementation.

Pre-stable, every spec change takes the next alpha counter instead (see
[Pre-stable](#pre-stable-alpha)).

### When the SDK surface moves without the spec

Both SDKs are thin over the spec, so a breaking SDK change without a spec
change is not expected: a method rename or a signature change in the SDK
follows a contract change on the wire. When one is unavoidable, the person
publishing the release picks the increment the SDK break warrants, and the
release notes say why. The same override covers an SDK-only fix that cannot
wait for the next spec change: cut the next PATCH by hand and say so in the
notes.

### Public API surface

The stability contract covers everything exported from the package entry point:

- **TypeScript:** the exports of `@daytona/sdk` (`src/index.ts`) and of the
  `@daytona/sdk/v2` namespace (`src/v2/index.ts`) — classes, functions, and their
  public/type signatures.
- **Python:** the names exported from `daytona` (`src/daytona/__init__.py` `__all__`) and
  from the `daytona.v2` namespace (`src/daytona/v2/__init__.py` `__all__`), and their
  signatures.
- The `v0` namespace (`daytona.v0`, `@daytona/sdk/v0`) is the bundled v0 SDK release; its
  surface is that release's, versioned by the v0 SDK, minus the client classes (`Daytona`,
  `AsyncDaytona`, `DaytonaConfig`): SDK 2.x builds a v0 client only through the root's `.v0`.
  The pin fixes v0's source and its generated API clients (exact versions); v0's
  third-party dependencies keep the ranges that release declares, so they float
  within those ranges exactly as for a standalone 0.x install.

Not covered (may change in any release): anything under an `_`-prefixed name,
internal modules not re-exported from the entry point, the `X-Daytona-Source` /
`X-Daytona-SDK-Version` / `User-Agent` header values, test/build tooling, and examples.

## Pre-stable (alpha)

Before `2.0.0`, every release was a `2.0.0-alpha.N` pre-release, the SDK
surface was considered unstable, and **breaking changes could land in any
alpha.** The spec diff was not classified into major/minor/patch then: a spec
change drafted the next alpha counter.

Pre-releases after `2.0.0` (`-alpha.N`, `-rc.N`, see
[Release channels](#release-channels)) keep those rules for the release they
lead up to: **breaking changes may land between the pre-releases of the next
MAJOR, never in a stable MINOR or PATCH.** A pre-release of a MINOR may change
what that MINOR adds until its stable release, but not what a stable release
already shipped.

Either way, breaking changes are called out in the changelog, as a
`<id>.breaking.md` fragment under [`changelog.d/`](../changelog.d/), which the
release assembles into the `### BREAKING` section of
[CHANGELOG.md](../CHANGELOG.md). Since `2.0.0`, a breaking change in a stable
release requires a MAJOR bump.

## Two SDKs, one version line

Python and TypeScript are **always released together on the same version
number**. There is
no per-SDK patch: a fix in one SDK ships when the next release is published,
and the other SDK is republished at the same number so that the two never
diverge. Note which SDK a change affects in the PR's changelog fragment.

Version sources of truth:

| What | Where | Role |
|------|-------|------|
| The version | the `version` input of the publish run, recorded as the `v*` tag on the published commit | **authoritative** |
| Python manifest | `sdk-python/pyproject.toml` → `[project].version` (PEP 440 spelling) | placeholder, restamped from the tag at publish |
| TypeScript manifest | `sdk-typescript/package.json` → `version`, mirrored into `sdk-typescript/src/version.ts` by `npm run stamp-version` | placeholder, restamped from the tag at publish |
| CLI | `cli/internal/app/app.go` `Version` (`0.0.0-dev` default) | stamped from the tag by `hack/build.sh` ldflags at publish |

The derivation from the version, per target:

| Target | From `v2.0.0-alpha.3` |
|---|---|
| npm `@daytona/sdk` | `2.0.0-alpha.3` |
| CLI GitHub Release (and tag) | `v2.0.0-alpha.3` |
| PyPI `daytona` | `2.0.0a3` (PEP 440: `-alpha.N` → `aN`, `-beta.N` → `bN`, `-rc.N` → `rcN`; stable unchanged) |
| npm `@daytona/upgrade`, PyPI `daytona-upgrade` (the migration tool) | `2.0.0-alpha.3`, `2.0.0a3` — the SDK's spellings |

The migration tool is versioned with the SDKs and migrates projects to the SDK
release of its own version (pinned for a pre-release, `^X.Y.Z` /
`>=X.Y.Z,<X+1` for a stable one); see [RELEASING.md](RELEASING.md), "The
migration tool".

## Compatibility between an SDK and the API

An installed SDK is expected to work against an API that is **ahead of it by
up to one MINOR within the same MAJOR** — the API's additive changes do not
break an older SDK, and the SDKs tolerate unknown response fields
everywhere. An SDK that is a MAJOR behind, or ahead of the API it talks to,
is outside the supported window. Pre-stable, the same expectation holds
across alpha numbers of `2.0.0`, with the caveat that alphas may break.

The skew is observable: every SDK request carries `X-Daytona-Source` and
`X-Daytona-SDK-Version` (and `User-Agent: <source>/<version>`), so the API
can see which SDK version each call came from.

## API replacement

When an API needs to change:

1. Land the best current public shape in both SDKs, in the same PR as the
   contract update.
2. Call out the breaking change explicitly in the PR's changelog fragment
   (`changelog.d/<id>.breaking.md`).
3. The version follows the spec diff at the next release — nothing to bump
   in the PR.

## Release channels

The version's pre-release identifier picks the channel, so it never has to be
chosen by hand: the publish workflow's gate derives the channel from the
version ([`scripts/release-channel.sh`](../scripts/release-channel.sh)), and
the npm dist-tag, the kind of the CLI's GitHub Release and the Homebrew
formula follow from it ([RELEASING.md](RELEASING.md), "What a version
derives").

- **Stable** (`vX.Y.Z`): npm `latest`, a normal PyPI release, a normal GitHub
  Release of the CLI (GitHub's **Latest** while it is the highest stable
  release), Homebrew `daytona`. The CLI installers install the highest stable
  release by default.
- **Release candidate** (`vX.Y.Z-rc.N`): npm `next`, PyPI `X.Y.ZrcN`, a
  pre-release GitHub Release, Homebrew `daytona-rc`, installer channel `rc`.
- **Alpha** (`vX.Y.Z-alpha.N`): npm `alpha`, PyPI `X.Y.ZaN`, a pre-release
  GitHub Release, Homebrew `daytona-alpha`, installer channel `alpha`.
- No other pre-release kind is published (no formula or installer channel
  follows betas). Pre-releases never become npm `latest` or GitHub's
  **Latest**. The CLI installers take them only with an explicit channel or
  version, with one exception before `2.0.0`: while no stable 2.x release
  exists, the default installs the newest pre-release and says so.
- The 0.x line publishes from `release/v0` under `daytona@0` (Homebrew) and,
  once a stable 2.x release exists, the npm line tag `v0-latest`.

**The published `2.0.0-rc.1` / `2.0.0rc1` outrank every alpha.** Both
registries already carry a release-candidate from before this policy: npm
`@daytona/sdk@2.0.0-rc.1` and PyPI `daytona==2.0.0rc1`. SemVer and PEP 440
both sort `rc` above `alpha`, so for a resolver those two artifacts beat every
`2.0.0-alpha.N` / `2.0.0aN` that is published now: `npm update` and a
`^2.0.0-alpha.N` range resolve to `2.0.0-rc.1`, and `pip install -U --pre
daytona` resolves to `2.0.0rc1` (the PyPI half and the pin-the-exact-alpha
advice are in [RELEASING.md](RELEASING.md), "The version picks the channel").
Users on the rc never move to an alpha through an upgrade command; they
install the alpha explicitly (`npm install @daytona/sdk@alpha`,
`pip install daytona==2.0.0aN`). The collision also binds the version space:
a future `v2.0.0-rc.1` can never publish, because the publish workflow's gate
refuses a version that already exists on either registry
(its `force_republish` resume switch skips an already-published target, it
never overwrites one) — the first rc that ships will be `v2.0.0-rc.2` or
later. Deprecating the npm
rc (`npm deprecate`) or yanking the PyPI one is the maintainers' call and is
not done by any workflow.

## See also

- [CHANGELOG.md](../CHANGELOG.md) — what changed in each version; unreleased
  changes are fragments in [`changelog.d/`](../changelog.d/).
- [RELEASING.md](RELEASING.md) — how to publish.
