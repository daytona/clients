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
  Within 2.x the pin moves only to a 0.x release that stays compatible for
  `.v0` callers: code that works against the bundled release keeps working,
  and the new release needs no newer Python or Node.js than the 2.x SDK
  supports (see [Supported runtimes](#supported-runtimes)). A release that
  moves the pin is at least a MINOR.

Two rules narrow what that covers:

- **Constructors of exported classes are not public API.** Instances come from
  the client and its services (`d.v2.create()`, `d.v2.get(id)`,
  `sandbox.process.connect(...)`, `ImageBuilder.from_registry(...)` /
  `ImageBuilder.fromRegistry(...)`); the class names are public for type annotations
  and `isinstance` / `instanceof` checks, but their constructor parameters may
  change in any release. The exceptions are the only classes you build
  yourself; their constructor parameters are public signatures like any
  other:
  - the root client (`Daytona`, Python's `AsyncDaytona`) and Python's
    `DaytonaConfig` (in TypeScript the config is a plain object);
  - the error classes;
  - in Python, the option dataclasses you build to pass to a public method,
    such as `Resources`, `VolumeMount`, `TerminalSize` and
    `ComputerUseRegion` (TypeScript takes plain objects for these). Some of
    them also appear as fields of results (`TerminalSize` in a process's
    info); that doesn't change their status.

  Every other exported class is built by the SDK: by the client, a service, or
  a factory such as `ImageBuilder.from_registry(...)`. That includes the
  handles, builders and models a method returns, even when another method
  accepts them back, such as an interpreter context, an `ImageBuilder` or an
  `Image`.

- **Generated wire types are never public.** The types generated from the
  contracts under [`contract/`](../contract/) (Python `daytona.v2._gen`,
  TypeScript `src/v2/gen/`, the CLI's `cli/gen/`) change whenever a contract
  does. From `2.0.0` on, no public export or public signature uses one: every
  type the SDKs expose is written in the SDK, so a contract change reaches you
  only through the surface described here.

Not covered (may change in any release): anything under an `_`-prefixed name,
internal modules not re-exported from the entry point, the `X-Daytona-Source` /
`X-Daytona-SDK-Version` / `User-Agent` header values, test/build tooling, and examples.

### The migration tool

The migration tool (`npx @daytona/upgrade`, `uvx daytona-upgrade`) is covered
too. From `2.0.0` on (pre-releases may still break, see
[Pre-stable](#pre-stable-alpha)), within a major version these stay compatible:

- its flags, and what the exit codes `0`, `1` and `2` mean;
- its `--json` report: every field and value in
  [`report.schema.json`](../upgrade/mapping/report.schema.json) keeps its name,
  type and meaning.

A minor release can add flags, add fields to the report and add values to its
`language`, `mode`, `kind`, `refused.reason` and `suggestion.category`
vocabularies. A program that reads the report must ignore fields and values it
doesn't know; the schema accepts them. What the tool does to a project can also
improve in a minor release: it can rewrite more calls, report new manual items
(which `--strict` counts), and refuse a case it newly recognizes as unsafe,
with a new `refused.reason`.

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

**The API operations a stable 2.x SDK calls stay valid for all of 2.x.** From
`2.0.0` until `3.0.0`, neither the Daytona API nor the toolbox API removes or
renames an operation (a method and its path) that any stable 2.x SDK release
calls (pre-releases may still break, see [Pre-stable](#pre-stable-alpha)). An
older 2.x SDK outside the window above can miss newer behavior, but it does not
start failing because an operation it uses went away.

## Supported runtimes

| Package | Runtime |
|---|---|
| Python `daytona` | Python 3.10 or later (`requires-python = ">=3.10"`) |
| TypeScript `@daytona/sdk` | Node.js 22.15 or later (`engines.node`) |
| `daytona` CLI | none: a static binary for Linux, macOS and Windows on amd64 and arm64 |
| Migration tool, PyPI `daytona-upgrade` | Python 3.9 or later |
| Migration tool, npm `@daytona/upgrade` | Node.js 20 or later |

The TypeScript SDK also runs in browsers with `WebSocket` and in other
JavaScript runtimes such as Bun, Deno and edge workers. CI tests them against
the packed package, but they have no versioned floor.

A runtime version here means a release line: a Python `3.x` version, or a
Node.js major version. A MINOR release may drop a release line that has reached
its upstream end of life, or require a later release within a line that upstream
still supports (for example Node.js 22.15 → 22.18) when a dependency needs it;
the changelog announces either. Dropping a release line that upstream still
supports takes a MAJOR. The floor covers the bundled `v0` namespace too: the
[v0 pin](#public-api-surface) never moves to a 0.x release that needs a newer
runtime than the SDK declares.

## The CLI

The `daytona` CLI has no import surface; its stability contract is what
scripts depend on. From `2.0.0` on (pre-releases may still break, see
[Pre-stable](#pre-stable-alpha)), within a major version these stay compatible:

- command paths (`daytona sandbox create`, `daytona volume list`, …);
- flags: their names, the values they accept, and what they do;
- the fields of `--output json` (and `yaml`) output: every field keeps its
  name, type and meaning;
- exit codes and what each one means;
- the environment variables the CLI reads;
- its configuration files: a newer CLI reads what an older one of the same
  major wrote;
- the request bodies `--file` / `-f` accepts.

A MINOR release can add commands, flags, output fields and values; a script
that reads JSON or YAML output must ignore fields it doesn't know. Not covered (may
change in any release): the layout of `--output table`, help text, and
progress output.

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

## See also

- [CHANGELOG.md](../CHANGELOG.md) — what changed in each version; unreleased
  changes are fragments in [`changelog.d/`](../changelog.d/).
- [RELEASING.md](RELEASING.md) — how to publish.
