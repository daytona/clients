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

Until the platform cuts `2.0.0`, every release is a `2.0.0-alpha.N`
pre-release and the SDK surface is considered unstable: **breaking changes
may land in any alpha.** Pre-stable, the spec diff is not classified into
major/minor/patch — a spec change drafts the next alpha counter. We still
call breaking changes out explicitly in the changelog, as a
`<id>.breaking.md` fragment under [`changelog.d/`](../changelog.d/), which the
release assembles into the `### BREAKING` section of
[CHANGELOG.md](../CHANGELOG.md).

Cutting `2.0.0` is a platform decision; after it, breaking changes require a
MAJOR bump.

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
chosen by hand: the publish workflow's gate derives the npm dist-tag from the
version (output `npm_dist_tag`) and the publish job's npm step publishes with it.

- **Stable** (`vX.Y.Z`): published to the `latest` tag (npm) / normal release (PyPI).
- **Pre-release**: each stage has its own npm dist-tag so it never becomes
  `latest`: `vX.Y.Z-alpha.N` → `alpha`, `vX.Y.Z-beta.N` → `beta`,
  `vX.Y.Z-rc.N` → `next`; PyPI uses the PEP 440 forms `X.Y.ZaN` / `X.Y.ZbN` /
  `X.Y.ZrcN` and recognizes them automatically as pre-releases.
- **Current policy (until general availability): alpha only.** The publish workflow
  ([RELEASING.md](RELEASING.md)) rejects a version that is not an alpha, so
  today every npm publish lands on `alpha`; widening to rc, then stable, is
  decided together with the platform's stable release, and relaxes only that
  gate — the dist-tag follows the version.

**The published `2.0.0-rc.1` / `2.0.0rc1` outrank every alpha.** Both
registries already carry a release-candidate from before this policy: npm
`@daytona/sdk@2.0.0-rc.1` and PyPI `daytona==2.0.0rc1`. SemVer and PEP 440
both sort `rc` above `alpha`, so for a resolver those two artifacts beat every
`2.0.0-alpha.N` / `2.0.0aN` that is published now: `npm update` and a
`^2.0.0-alpha.N` range resolve to `2.0.0-rc.1`, and `pip install -U --pre
daytona` resolves to `2.0.0rc1` (the PyPI half and the pin-the-exact-alpha
advice are in [RELEASING.md](RELEASING.md), "ALPHA-ONLY until general availability").
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

## Breaking-change check

From the first stable release on, every pull request is compared with the
latest stable release tag (the highest `vX.Y.Z` with X ≥ 2 and no pre-release
suffix, skipping any release GitHub flags as a pre-release) by the
`Breaking changes` workflow (`scripts/breaking/breaking_changes.py`).
It diffs the committed public-surface snapshots (`sdk-typescript/api-surface/`,
`sdk-python/api-surface/`, `cli/api-surface/`, and `upgrade/api-surface/` for
the migration tool's flags and `--json` report fields and values; each kept
current by its CI) and `contract/*.json` (oasdiff for the OpenAPI contracts;
values may be added to enums the contract marks open). A removed or
incompatibly changed export, signature, command, flag, environment variable,
report field or value, endpoint or error code fails the check, and so does a
new required member of a type callers pass in. In the SDK and CLI surfaces,
every type position has a direction. A value callers supply (a parameter of
an SDK function, a member of a type only passed in, the return value of a
callback they implement) may widen but not narrow. A value the SDK supplies
(a return value, a member of a returned type, an argument the SDK passes to
a callers' callback) may narrow (an optional member may become always
present), and may widen only by adding an enum value or a member to an
existing union, because clients must handle values they do not know; it may
never gain `undefined`, `null` or `None` (`Literal[None]` included). A
position used both ways, or whose direction the check cannot tell (a type
alias), may only gain enum values or union members. In any of them, an
object type written inline may gain optional members. Overloads: a call
resolves against the first overload it matches. For a function callers call
(one the SDK implements, or a method of a type that goes both ways), the
existing overloads keep their order, a new overload may only come after them,
only the last overload may widen, an earlier one may only narrow its return
type (renaming a TypeScript parameter aside), and none may be dropped: when the
later overload a dropped one's calls now reach (the first that accepts them)
takes all of them and returns the same or a narrower type, acknowledge the
finding. For a signature callers implement (a callback, the methods of a type
they pass in), no overload or call signature may be added or dropped: every
implementation would have to change. In
`contract/*.json`, oasdiff compares the
contracts as they are; the only finding the `x-daytona-open-enum` tag drops is
a value added on the response side to an enum tagged open both at the release
and now. Removing a value callers
send stays a break wherever they send it, and an enum the contract does not
mark open may not gain a response value. The migration
tool's report fields keep their types: it may only gain fields, values, flags
and short aliases for its flags. A real break
needs a `changelog.d/<id>.breaking.md` fragment in the pull request; the
maintainer publishing the next release then decides the MAJOR bump. A change
the check misreads as breaking is acknowledged in
`scripts/breaking/compatible-changes.txt` instead, with a one-line reason, and
puts nothing in the changelog. A pull request counts only the breaks it adds,
so one that deletes a breaking fragment while the base branch already carries
that break is caught by the run on `main` after it merges. Until a stable tag
exists the check reports "no stable release yet" and passes.

## See also

- [CHANGELOG.md](../CHANGELOG.md) — what changed in each version; unreleased
  changes are fragments in [`changelog.d/`](../changelog.d/).
- [RELEASING.md](RELEASING.md) — how to publish.
