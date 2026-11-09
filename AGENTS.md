# AGENTS.md

Instructions for AI agents (and humans) working in this repository: the
Daytona Python SDK (`daytona`), TypeScript SDK (`@daytona/sdk`) and the
`daytona` CLI.

**If `AGENTS.local.md` exists in the repository root, read it first; it
overrides this file.** It is git-ignored and never committed.

## Who this file is for

This repository is public and takes contributions from anyone. Everything
below works with what is in the repository plus, optionally, a Daytona account:

- **Live checks gate on credentials, not on who you are.** When
  `DAYTONA_API_KEY` (and `DAYTONA_V2_API_URL`, for a deployment other than the
  default) are set, run the live suites (`./e2e/run.sh`); when they are not,
  skip them and say so in the PR. Never ask a contributor for credentials, and
  never paste a key, an endpoint that is not public, or a request trace into an
  issue, a PR, a commit message or a workflow input.
- **Contract files come from maintainers.** `contract/` is refreshed by
  maintainers when the platform changes. A change that needs new or different
  API surface is a feature request to the platform: open an issue describing
  the need; do not hand-edit the contract to invent it.
- **Keep it public.** Commits, PRs, issues, code and docs here describe the
  SDKs and the CLI only. Do not name or link non-public systems, repositories,
  hostnames, deployments or internal tickets.

## Secret scanning — enable the hooks before your first commit

Every clone and every worktree runs `scripts/secret-scan.sh install` once,
before its first commit. It downloads the pinned gitleaks (checksum-verified,
stored under the clone's `.git/`; on a platform without a published build it
uses a gitleaks you put on `PATH`) and points this worktree's
`core.hooksPath` at `.githooks/`. Hooks from the path it replaces keep running.
The setting is per worktree (`extensions.worktreeConfig`), so a clone-wide
write by another tool (husky's `postinstall` on the 0.x line) cannot switch
the scan off; worktrees added later from an installed main worktree inherit
it (git 2.37+), which also means a 0.x worktree added from there runs no
husky hooks. Scans need git 2.36 or later.

- `pre-commit` scans the staged changes; `pre-push` scans the commits the
  remote does not have yet (merge commits included), which catches commits
  made before the hooks were enabled. The `Secret scan` workflow runs the same scan on every pull request
  and on pushes to `main`. The rules are `.gitleaks.toml`:
  the gitleaks defaults plus Daytona API keys, organization keys, sandbox
  tokens and invitation tokens.
- **Never bypass a block** (`--no-verify`, removing the hooks path, editing
  the rules to fit your change). Remove the secret instead — read it from an
  environment variable. A secret that was pushed or shared anywhere is leaked:
  say so, so it can be rotated; rewriting history does not un-leak it.
- A false positive (a fabricated fixture, a documented example) gets a
  `gitleaks:allow` comment on that line or an exact-value `[[allowlists]]`
  entry in `.gitleaks.toml` with its reason, never a file- or rule-wide one.
  Test credentials are built at run time where possible
  (`scripts/secret-scan_test.sh` shows how).
- Patterns that must not be published (non-public hostnames or project
  names) belong in `info/gitleaks-private.toml` under the clone's common git
  directory (`git rev-parse --git-common-dir`; `.git/info/` in the main
  worktree), a complete gitleaks config that every scan in every worktree of
  that clone also runs, with no `gitleaks:allow` exception. It is never
  committed.
- Changing `scripts/secret-scan.sh`, `.githooks/` or `.gitleaks.toml`: run
  `bash scripts/secret-scan_test.sh`.

## Repository layout

| Path | What |
|---|---|
| `sdk-python/` | `daytona` (module `daytona`) — root client + `daytona.v2` + `daytona.v0` (bundled at publish) |
| `sdk-typescript/` | `@daytona/sdk` — root client + `/v2` + `/v0` (bundled at publish), ESM + CJS |
| `cli/` | the `daytona` CLI (Go) |
| `contract/` | the machine-readable API contracts the SDKs and the CLI are built against |
| `scripts/` | v0 bundling (+ the `scripts/v0-sdk-version` pin), release helpers, the changelog tool |
| `changelog.d/` | changelog fragments, one per user-visible change (see below) |
| `examples/` | runnable examples for both SDKs |
| `upgrade/` | `daytona-upgrade`, the 0.x → 2.x migration tool (npm + PyPI) |
| `e2e/` | live end-to-end suites, `e2e/run.sh` (need `DAYTONA_API_KEY`) |
| `docs/` | release and versioning policy, contribution prep, `adr/` (architecture decision records) |

## PARITY CONTRACT (the rule that matters)

Any feature or fix lands in BOTH SDKs in the same PR, with tests in both, plus
one or more changelog fragments under `changelog.d/`. Naming follows each
language: Python `snake_case` ↔ TS `camelCase`. One deliberate difference in
the root config's v0 routing: Python forwards `connection_pool_maxsize` to
`d.v0`, TypeScript keeps `connectionPoolMaxsize` v2-only, because the 0.x
TypeScript SDK has no pool-size setting (`ROOT_CONFIG_ROUTING` in both SDKs
records it). Both roots copy the config's fields once, when the client is
built (TS `snapshotConfig`, Python `_snapshot_config`): later edits to the
caller's object have no effect, and URL fields are refused before and after
the copy (a getter or property could otherwise add one while it is read).

Versions are NOT bumped per PR, nor anywhere else in git: the tracked
`version` fields in `sdk-python/pyproject.toml`, `sdk-typescript/package.json` and
`sdk-typescript/src/version.ts` are placeholders, overwritten in the publish run
([RELEASING.md](docs/RELEASING.md), [VERSIONING.md](docs/VERSIONING.md)).

**Changelog fragments.** Never edit `CHANGELOG.md` for unreleased work — it
has no `Unreleased` section. Add `changelog.d/<PR>.<type>.md`,
`<PR>-<slug>.<type>.md` (several bullets of one type in one PR) or
`<slug>.<type>.md` (PR number not known yet), one bullet's markdown per file,
no leading `- `. Rules: [`changelog.d/README.md`](changelog.d/README.md);
check: `python3 scripts/changelog/assemble.py check --dir changelog.d`.

**One error hierarchy, by construction.** Status-level bases → source-code
subclasses; each SDK carries a codes catalog (`_codes.py` behind the public
read-only `daytona.v2.codes`; `codes.ts`, exported as the `codes` namespace)
holding one catalog per error source declared in the contracts plus the shared
generic table, dispatched on `(source, code)`. Drift tests per SDK hold those
catalogs to the committed contract: `contract/api-openapi.json`,
`contract/toolbox-openapi.json`, `contract/proxy-errors.json` and
`contract/router-errors.json` — a new code on any source (including its
per-code `extras`) fails both suites until both SDKs learn it.

**Route drift.** Both suites check that every hard-coded route literal in the
SDK source (`transport.get('/…')`, `RequestSpec("GET", "/…")`, templated
segments normalized to `{param}`) resolves to exactly one operation in
`contract/{toolbox,api}-openapi.json`, and that every toolbox operation is
either called or on a small allow-list with a reason (an allow-listed route
the SDK does call fails as stale).

## The contract artifacts — `contract/`

| File | What |
|---|---|
| `contract/api-openapi.json` | the Daytona API surface |
| `contract/toolbox-openapi.json` | the client-callable in-sandbox toolbox surface, including the complete error-code catalog (`components.schemas.ErrorCode` + the `x-daytona-error-codes` / `x-daytona-generic-codes` status metadata) |
| `contract/proxy-errors.json`, `contract/router-errors.json` | the error catalogs of the toolbox proxy and the API router |
| `contract/sandbox-events-protocol.json` | the sandbox state events WebSocket protocol, as example frames both SDKs' event hubs are tested against |

- **Never hand-edit these files.** Maintainers refresh them from the platform.
  New surface appearing in the contract does **not** need immediate SDK
  exposure — unexposed spec is fine. Surface the SDKs already ship does not
  break; if a drift test fails without an SDK change, treat it as a bug and
  flag it.
- **Vendored wire types are generated from them — never hand-edit those
  either.** `sdk-typescript/src/v2/gen/{toolbox,api}.ts`,
  `sdk-python/src/daytona/v2/_gen/{toolbox,api}/` (pydantic v2 models) and
  `cli/gen/` type each wire boundary. After a contract refresh run
  `scripts/gen-contract-types.sh` (needs node, a JRE and Go) and let
  typecheck / mypy / `go build` surface what must adapt; CI runs it with
  `--check`.

Enums: which are open is decided once, in the contract — every string enum
in `contract/*.json` carries `x-daytona-open-enum` or `x-daytona-exact-enum`,
and every generator reads the tag. The generated wire types are OPEN where tagged (TS `(string & {})` via
`sdk-typescript/scripts/gen-contract.mjs`, which also checks its own output;
Python `UNKNOWN_DEFAULT_OPEN_API` pseudo-members carrying the wire string, and
no closed validator on an open inline enum), and every public SDK field backed by
a contract enum is CLOSED known values + `'unknown'`, normalized at the
boundary, with the raw string on a `raw<Field>` / `raw_<field>` sibling.
Vocabularies the contract does not declare as an enum (`ImageStatus`,
`MacBootMode`, …) stay open until it does, as do the two the
SDKs leave open on purpose (`LifecycleAction` on `lifecycle`, the interpreter
`language`). Known lists come from `sdk-typescript/src/v2/gen/*-enums.ts` / the
`daytona/v2/_gen` enums; never cast an open wire value into a closed public type.

TypeScript: consumers import the alias layers as namespaces (`import type *
as toolbox from './gen/toolbox-types.js'` → `toolbox.ProcessDto`;
`api.Sandbox`) — the
surface reads at every use and generated names never collide with the public
SDK types; `gen/toolbox-types.ts` / `gen/api-types.ts` are the only handwritten
files in gen dirs (null→absent transform for the toolbox surface, friendly
names for the Daytona API) — edit those, not the generated output. Python:
pydantic v2 models (`from daytona.v2._gen.toolbox import ProcessDto`;
`ProcessDto.model_validate(payload)` at the boundary, which also validates);
regeneration needs node + a JRE (openapi-generator, tooling pinned under
`sdk-python/gen/`).

## The CLI — `cli/`

Both SDKs, the CLI and the examples support volumes: the client `volume`
service manages them, `volumes` attaches them on create, and `sandbox.volumes`
provides pull operations. The CLI exposes `volume` and `sandbox create --volume`
(also on `create-macos`). Keep these surfaces in parity across the SDKs.

The CLI's own API URL order, whatever the endpoint notes under "Version
namespaces" say about the SDKs: `--api-url`, then `DAYTONA_V2_API_URL`, then
the profile's saved URL, then `app.DefaultAPIURL` (equal to the SDKs'
`DEFAULT_V2_API_URL`). The CLI never reads `DAYTONA_API_URL` or
`DAYTONA_REGION` (regions come from `--region` only).

The `daytona` CLI (Go) shares the SDKs' version line but is not part of the
Python↔TS parity contract. Its wire types (`cli/gen/`) are generated from
`contract/*.json` by `cli/hack/gen.sh`; its README is public-facing. Verify:
`(cd cli && go vet ./... && go test ./...)`, lint with `golangci-lint run`
(config `cli/.golangci.yml`). Release binaries: `cli/hack/build.sh` (six
targets + `SHA256SUMS`, each binary checked with `go version -m` against
`cli/go.mod`'s `go` line), consumed by `install.sh` / `install.ps1` (highest
stable 2.x by default; pre-releases only with a channel or version, except
the highest pre-release while no stable 2.x release exists) and the
Homebrew formulae of the `daytonaio/homebrew-cli` tap
(`cli/hack/homebrew/README.md`). A release's channel (npm dist-tag, GitHub
Release kind, formula) comes from its version through
`scripts/release-channel.sh`.

## Version namespaces

Both packages are a thin ROOT over one namespace per API major version:

- **Root** (`sdk-python/src/daytona/__init__.py` + `_client.py`;
  `sdk-typescript/src/index.ts` + `Daytona.ts`) exports only the client
  (`Daytona`, plus Python's `AsyncDaytona`), the flat shared `DaytonaConfig`
  (no API URL, no region; `target` v0-only and deprecated — it warns,
  pointing at v0's per-call `target` / `region_id`), the SHARED error
  hierarchy (below), `DEFAULT_V2_API_URL` (`https://api.daytona.com`, production) and `V0_API_URL`. `d.v0` / `d.v2` are lazy and cached; the root has
  NO resource methods. Python's module `__getattr__` imports `daytona.v0` on
  demand (so `from daytona import v0` works) and turns names a pre-2.0 root
  exported (`from daytona import Sandbox`; the 0.x list is
  `_v0_root_names.py`, drift-checked by `test_v0_bundle.py` on a pin bump) into
  a guiding `ImportError`; any other name is a plain `AttributeError`. In TS,
  `d.v0` is the 0.x SDK's own client (see "TS `d.v0` is the real v0 client"), so
  every v0 behaviour, including unbound detached methods, is 0.x's.
- **`v2`** (`sdk-python/src/daytona/v2/`, `sdk-typescript/src/v2/`) is the SDK this
  repo writes; everything below this section describes it. Its client has NO
  API URL field either: it reads `DAYTONA_V2_API_URL` at construction, else
  `DEFAULT_V2_API_URL` (TS through a computed `globalThis.process` lookup, so
  bundlers cannot inline it — browser workers that shim `process.env` rely
  on that). There is no client-level toolbox URL
  either: every sandbox response carries its own, and only a
  per-sandbox `toolbox_url` / `toolboxUrl` override remains.
- **`v0`** (`sdk-python/src/daytona/v0/`, `sdk-typescript/src/v0/`) is the 0.x SDK
  vendored from this repository's `release/v0` line at the PINNED tag and commit in
  `scripts/v0-sdk-version` (line 1 the tag, line 2 the commit it peels to; the single
  source of truth — a moved tag fails `resolve-v0-tag.sh --verify`,
  `bundle-v0.sh` and the publish gate) by `scripts/bundle-v0.sh` at publish time —
  git-ignored, NEVER committed or hand-edited (`scripts/check-no-bundled-v0.sh`
  is an sdk-ci gate). Bumping the pin is a manual PR (docs/RELEASING.md, "The v0 pin") plus a 2.x release;
  `scripts/resolve-v0-tag.sh --verify` (sdk-ci) proves the pinned tag exists
  at the pinned commit. `scripts/v0-publish-mode.sh` (run by the publish
  gate) decides per published tree: a tree with neither the namespace
  code nor the pin (a pre-namespaces release) publishes without v0 (notice);
  namespace code without a pin, or a pin without the code, fails the gate.
  Publish builds also set `DAYTONA_REQUIRE_V0_BUNDLE=1`, which makes TS
  `build:v0` fail instead of skipping when `src/v0` is missing. Overriding the
  pin (`DAYTONA_V0_TAG`, a TAG argument, a `--src` at another commit) makes
  `bundle-v0.sh` print an UNVERIFIED-testing-output warning.
  `DAYTONA_V0_TAG` / the publish workflow's `v0_tag` input override the pin
  for testing only. Nothing is patched into v0's behavior: features such as a
  per-call v0 `target` / `region_id` (in `v0.224.0`) arrive by
  bumping the pin to a 0.x release that has them. With that pin, config
  `target` is deprecated in 2.x only (Python `DeprecationWarning`, TS
  `@deprecated` + one `process.emitWarning`, the same text); the 0.x SDK
  never deprecates it. `sdk-python/tests/test_v0_bundle.py` and
  `sdk-typescript/scripts/v0-bundle-smoke.cjs` run against the real bundle in
  sdk-ci and publish (skipped without a bundle). `d.v0` always passes `V0_API_URL`, so
  `DAYTONA_API_URL` (the 0.x SDK's variable) never reaches v0; nothing in 2.x
  reads it. Python: v0's dependencies are the `v0-sdk`
  extra (never plain `v0`: pip < 26 on 3.10 parses `extra == 'v0'` as a
  version and crashes every install), committed EMPTY and filled by the bundle; hatch `artifacts` keeps the
  ignored tree in wheel/sdist. TypeScript: `src/v0` compiles separately
  (`tsconfig.v0.json`, lax CJS) to `dist/cjs/v0`; the root imports it through
  the package self-reference `@daytona/sdk/v0`, which `tsconfig.json` `paths`
  map to the built declarations or, unbundled, to `src/v0-unbundled.ts`.
  v0's runtime imports that its manifests leave undeclared
  (`@opentelemetry/resources`, `@opentelemetry/otlp-exporter-base`) are pinned
  to the single version v0's `yarn.lock` resolves — what the published v0
  package declares (3.0.0 / 0.300.0 at `v0.230.0`) — so strict layouts
  (pnpm) resolve them; an ambiguous lockfile fails the bundle. The bundle also
  rewrites v0's `getPackageInfo()` to the bundled release, so its
  `X-Daytona-SDK-Version` / `User-Agent` report it (bundled one directory
  deeper, v0's `../../package.json` would be `dist/cjs/package.json`).
- **One error hierarchy for both namespaces.** The root defines `DaytonaError`
  plus the 12 categories v0 and v2 share (BadRequest, Authentication,
  Forbidden, NotFound, Timeout, Conflict, Gone, UnprocessableEntity,
  RateLimit, InternalServer, ServiceUnavailable, Connection), with no
  synonyms (`from daytona import DaytonaValidationError` /
  `DaytonaAuthorizationError` keep the 0.x guiding `ImportError`). Python
  (`daytona/_errors.py`): stateless marker classes that v2's classes also
  inherit in source and that v0's inherit through the bundle's anchored
  rewrite of `common/errors.py`, which FAILS if a class is missing. TypeScript
  (`src/errors.ts`): `static [Symbol.hasInstance]` consults a per-category
  registry on `globalThis[Symbol.for('@daytona/sdk/error-registry')]`, keyed
  by category name, so the ESM root and the CJS-only `./v0` copy share it; v2
  registers at module load, and the bundled v0 entry registers v0's classes
  when it loads (appended by `sdk-typescript/scripts/bundle-v0.mjs`), so v0 error
  classes imported from `@daytona/sdk/v0` are caught with no `d.v0` access.
  Version-specific errors are not
  re-exported at the root. Python's root `__getattr__` raises `ImportError`
  (not `AttributeError`) on purpose so `from daytona import X` keeps its
  guidance; the cost is that `hasattr(daytona, "Sandbox")` raises.
- **The TS registries are process-global and unversioned on purpose:** any
  `@daytona/sdk` copy loaded in the process (ESM + CJS, the bundled v0, a
  second installed copy) joins the same `instanceof` universe. That is what
  cross-copy matching needs, and the root classes are catch-alls. A future
  major that changes the error shape must change the symbol keys
  (`@daytona/sdk/error-registry`, `@daytona/sdk/v2-error-classes`).
- **v2's own error classes are dual-load safe too:** `src/v2/errors/DaytonaError.ts`
  keys every v2 error class (an explicit string table, never `class.name`) in
  `globalThis[Symbol.for('@daytona/sdk/v2-error-classes')]`, and
  `DaytonaError[Symbol.hasInstance]` matches the same-key class of another
  loaded copy (ESM + CJS in one process) after the native check; user
  subclasses keep the native check only. A jest drift test fails if an exported
  error class is not keyed. `exports` gives `.` and `./v2` conditional types
  (`require.types` → the CJS declarations in `dist/cjs`), checked by the
  node16 fixture `sdk-typescript/scripts/cjs-types-fixture/` in the tarball smoke.
- **Dev / non-prod endpoints (test environments only; the published READMEs mention them only as such):** v2 resolves `DAYTONA_V2_API_URL` → `DEFAULT_V2_API_URL` (the CLI's default, `app.DefaultAPIURL`, must stay equal); v0 resolves `DAYTONA_V0_API_URL` → `V0_API_URL` (passed explicitly as `api_url`/`apiUrl`). `DAYTONA_API_URL` belongs to the 0.x SDKs and CLI: nothing in 2.x reads it. Blank = unset; no config field.
- **Ambient configuration is normalised the same way in both SDKs.**
  `DAYTONA_API_KEY`, `DAYTONA_JWT_TOKEN`, `DAYTONA_PROJECT_ID` and
  `DAYTONA_V2_API_URL` are trimmed and a blank value is unset; explicit config
  values are used as given (checked against `undefined` / `None`, never
  truthiness); an explicitly empty `api_key` / `jwt_token` / `project_id`
  (`apiKey` / `jwtToken` / `projectId`) is a `BadRequestError` (400,
  `BAD_REQUEST`, source `SDK`), like every invalid-configuration error,
  including TS's refused `apiUrl` / `serverUrl` / `toolboxUrl` keys. Missing
  credentials are `AuthenticationError` (401, `UNAUTHORIZED`, `SDK`). The
  TS root validates the copy it keeps: every enumerable key (own or
  inherited) must be known, each known key and the nested `events` are read
  once. The deprecated root `target` stays, deprecated, for all of 2.x.
- **TS `d.v0` is the real v0 client, loaded synchronously.** The getter
  builds the 0.x SDK's `Daytona` on first access through the package-internal
  `#v0-loader` import (package.json `"imports"`): `loaders/v0-node.cjs`
  (a literal `require('../dist/cjs/v0/index.js')`) for the ESM build,
  `loaders/cjs/v0-node.cjs` (a literal `require('../v0/index.js')`) copied
  into `dist/cjs/loaders/` and mapped by
  `dist/cjs/package.json` (written by `scripts/write-cjs-package.mjs`; Node
  rejects `..` in an imports target, so the CJS scope carries its own) for the
  CJS build. Both are literal relative requires of the file `exports["./v0"]`
  points at, so esbuild (and plain Node) inline v0: a bundled Node app needs
  no `node_modules`, and a static `@daytona/sdk/v0` import (types, errors)
  shares the same module (`instanceof` holds). webpack resolves a `webpack` condition to
  `loaders/v0-node-webpack.cjs`, which requires `@daytona/sdk/v0` from
  `node_modules` at run time (`__non_webpack_require__`): inlining v0's
  dependency graph trips a webpack bug in braces / picomatch and fails every
  production build. sdk-ci's tarball smoke runs both shapes. A `browser`
  condition maps to `loaders/v0-browser.mjs` / `loaders/cjs/v0-browser.cjs`,
  which import the ESM v0 build (`dist/esm/v0`) statically, so `d.v0` works
  in browsers and stays synchronous; a browser bundle evaluates v0 when the
  root loads. In Node nothing
  loads v0 at root import (the tarball smoke checks v2-only use in a fresh
  process). A failed load is not cached; `close()` disposes the cached v0.
- **Browser apps:** a browser bundle of the root carries v0 (about 850 kB
  minified with webpack; `@daytona/sdk/v2` alone is about 64 kB and carries
  none). `./v0` has a `browser` condition pointing at an ESM build of the
  bundle (`dist/esm/v0`, built by `scripts/build-v0.mjs` with the 0.x SDK's
  `__esmRequire` shim over every `require(`, so bundlers do not try to pull
  v0's lazily required Node modules, and with every relative import fully
  specified, which webpack's strict ES module resolution needs) and
  package.json carries v0's own `browser` stub map (`bundle-v0.mjs` fails if
  it drifts from the pinned release's). `bundle-v0.mjs` also defers the one
  `Buffer` v0 builds at module load, so the root loads in a browser with no
  polyfills; sdk-ci builds it with esbuild and webpack `--target web` and runs
  it with `Buffer` / `process` / `require` shadowed. v0 operations that use
  Node APIs still need the 0.x SDK's polyfills. Every browser client comes
  from the root. A browser app that needs no v0 can alias the root's
  `#v0-loader` to a stub in its bundler config, so it carries no v0.
- **The v0 client is reachable only through the root.** The bundle
  steps hide v0's client classes: Python `Daytona` / `AsyncDaytona` /
  `DaytonaConfig` resolve only as `_Daytona` / `_AsyncDaytona` /
  `_DaytonaConfig` (the public names raise an `ImportError` naming
  `Daytona().v0`; the list is `HIDDEN_CLIENTS` in `v2/_exports.py`), and TS
  `Daytona` / `DaytonaConfig` are re-exported as `RootOnlyClientV0` /
  `RootOnlyConfigV0` (names unlike the public ones, so no compiler suggests
  them for a failed import; a CJS `require('@daytona/sdk/v0').Daytona` throws the same
  guidance when called, through a non-enumerable stub `build-v0.mjs` appends;
  a stub, not a throwing getter, so `jest.mock` automocking still works). The root's
  `.v0` reads the private names; `DaytonaV0Client` (TS root) types a stored
  client. Both roots' URL-field errors say "SDK 2.x targets Daytona Cloud. For
  a custom API URL, stay on SDK 0.x."
- **The v2 client is reachable only through the root too.** Python
  `daytona.v2` does not bind `Daytona` / `AsyncDaytona` / `DaytonaConfig` (or
  their `…V2` aliases); the root imports them from `v2._sync` / `v2.daytona` /
  `v2.types`, and a runtime-only `daytona.v2.__getattr__` raises `ImportError`
  naming `Daytona().v2`. TS `src/v2/index.ts` exports neither `Daytona`,
  `DaytonaV2` nor the `DaytonaConfig` type; `scripts/write-cjs-package.mjs`
  appends non-enumerable throwing stubs for `Daytona` / `DaytonaV2` to the
  CJS v2 entry, and the root exports `DaytonaV2Client`. The Python root's
  `DaytonaV2Client` / `AsyncDaytonaV2Client` / `DaytonaV0Client` /
  `AsyncDaytonaV0Client` are annotation-only: under `TYPE_CHECKING` they alias
  the real classes, at runtime they are `_AnnotationOnly` stand-ins
  (`_client.py`) whose call and subclassing raise `TypeError` and whose `isinstance` checks
  delegate to the real class (they are not mock specs). The root's `.v2` /
  `.v0` must build through the private names (`_DaytonaV2`, `_load_v0`), never
  through these. Internal tests that
  drive the v2 client import it from its module (`v2/Daytona.ts`,
  `daytona.v2._sync`), never from the namespace.
- **Unbundled TS installs** (source checkouts, git installs, `npm pack` without
  `scripts/bundle-v0.sh`) get a stub as `./v0`: `scripts/build-v0.mjs` compiles
  `src/v0-unbundled.ts` into `dist/{cjs,esm}/v0` whenever `src/v0` is absent,
  so `exports["./v0"]` resolves and strict root-only consumers type-check. The
  stub exports `__daytonaV0Unbundled`, so the root's `d.v0` throws the 501
  `NotSupportedError` ("not included in this build") on first access; its
  `RootOnlyClientV0` throws a plain `Error` with the same text if constructed.
  Published packages always carry the
  bundle (the publish job checks it).
- Value exports (classes, enums) shared by both namespaces carry suffixed
  aliases (`SandboxV0` / `SandboxV2`); type-only names do not. The v2 lists
  are committed (`v2/_exports.py` `SHARED_WITH_V0`, `src/v2/index.ts`), the v0
  ones are appended by the bundle, and both bundlers fail when the committed
  v2 list and the pinned v0 release's shared set disagree. The hidden client
  classes get no suffixed alias in either namespace.

## Where to look

Paths below are inside the v2 namespace.

| Concern | Python (`sdk-python/src/daytona/v2/`) | TypeScript (`sdk-typescript/src/v2/`) |
|---------|---------------------------|-------------------------------|
| Public exports | `__init__.py`, `_exports.py` | `index.ts` |
| v2 client / sandbox handle (async-native `AsyncDaytona` / `AsyncSandbox`) | `daytona.py`, `sandbox.py` | `Daytona.ts`, `Sandbox.ts` |
| Synchronous surface (`Daytona`, `Sandbox`, …) — GENERATED | `_sync/` package (one file per async module; `_sync/_runtime.py` is the hand-written glue) ← `sdk-python/gen/gen_sync.py`; bridge in `_bridge.py` | *(n/a — TS is async-only)* |
| Sans-I/O request/parse layer | `_core.py` | (inline per namespace + `http.ts`) |
| Transports (sync + async / axios + fetch-SSE) | `_transport.py`, `http.py` | `http.ts` |
| Process engine namespace | `process.py` | `Process.ts` |
| Interpreter (kernel-backed code) | `interpreter.py` | `Interpreter.ts` |
| fs / git | `filesystem.py`, `git.py` | `FileSystem.ts`, `Git.ts` |
| Platform services | `services.py`, `snapshot.py`, `secret.py` | `Images.ts`, `Snapshots.ts`, `Volume.ts`, `Secret.ts` |
| Volume models / sandbox volume operations | `volume.py`, `sandbox_volumes.py` | `Volume.ts`, `SandboxVolumes.ts` |
| Errors + code catalog | `errors.py`, `_codes.py` (public view `codes.py`) | `errors/DaytonaError.ts`, `codes.ts` |

## The migration tool — `upgrade/`

`daytona-upgrade` moves 0.x code onto SDK 2.x: an npm package in
`upgrade/typescript/` (`npx @daytona/upgrade`, ts-morph) and a PyPI package in
`upgrade/python/` (`uvx daytona-upgrade`, LibCST). Same flags, exit codes,
comment format and JSON report (`upgrade/mapping/report.schema.json`) in both:
a behavior change lands in both packages in the same PR, like the SDK parity
contract (deliberate exceptions: `.gitignore` handling and generated-file
skipping exist only in the TypeScript tool).

- **One table.** Both read `upgrade/mapping/mapping.json` (what stays at the
  root, the moved client members, the 0.x config fields, and one v0 → v2
  suggestion per public 0.x method). Edit it there, then run
  `node upgrade/scripts/sync-mapping.mjs` (copies it into both packages,
  drift-tested in each suite) and `node upgrade/scripts/gen-guide.mjs`
  (renders `upgrade/MIGRATION.md`); never hand-edit the copies or the guide.
- **Coverage checks.** `node upgrade/scripts/check-v0-surface.mjs --src <0.x
  checkout>` fails when a public 0.x method at the pinned tag has no entry, so
  a pin bump that adds 0.x surface needs mapping rows.
  `node upgrade/scripts/check-v2-surface.mjs` checks every v2 suggestion's call
  and argument names against the SDK sources in this repository.
- **The `--json` report is public.** `upgrade/mapping/report.schema.json`
  stays open so reports from later 2.x releases still validate: no
  `additionalProperties: false`, and each vocabulary lists its values under
  `examples`. Both suites validate the tools' output against a strict copy
  derived from it (`tightened` in `upgrade/typescript/tests/report.test.ts`,
  `upgrade/python/tests/report_schema.py`), so a new field or value must be
  added to the schema in the same PR. Renaming or removing a field or value
  needs a major version ([VERSIONING.md](docs/VERSIONING.md), "The migration tool").
- **Runtime guard.** The moved client members are also the root clients'
  runtime guard (`_MOVED_TO_V0` in `sdk-python/src/daytona/_client.py`,
  `MOVED_TO_V0` in `sdk-typescript/src/Daytona.ts`); a test in each SDK pins them
  to the mapping.
- The tools never rewrite v0 code to v2: `--suggest-v2` only writes
  `daytona-v2:` comments. `docs.base` in the mapping is the Daytona docs home
  until the migration guide has its own page; `sdk_publish.yaml` refuses to
  publish the tool while `docs.placeholder` is true.
- **Lockstep versions.** `sdk_publish.yaml` publishes the tool with the SDKs at
  the same version (`publish_upgrade`), and the tool targets that SDK
  release (`sdk-target.ts` / `sdk_target.py`: a pre-release pins it, a stable
  `X.Y.Z` writes `^X.Y.Z` / `>=X.Y.Z,<X+1`; the placeholder `0.0.0` keeps the
  mapping's `range` / `specifier`). The SDKs' moved-member guidance names the
  tool release of their own version. A publish run only builds and packs
  the tool (`build-upgrade`); each tool package uploads once its SDK version
  is visible on that registry.
- **The project's 0.x version** (`v0-version.ts` / `v0_version.py`): read from
  the closest lockfile, else the manifest's lower bound, and compared with the
  mapping's `v0Version` (the pin; `check-v0-surface.mjs` keeps them equal and
  `--write-v0-exports` updates it). Older and newer-patch versions warn
  (`warnings` in the report, never a manual item), a newer minor refuses
  (`newer-v0`, exit 2) unless `--allow-newer-v0`. Messages are identical in
  both tools (`upgrade/mapping/v0-version-messages.json` holds shared
  expectations both suites assert). A pin bump is a manual PR
  (docs/RELEASING.md, "The v0 pin").
- **Verify:** `scripts/test-upgrade.sh`, and for the pinned 0.x examples end to
  end `bash upgrade/scripts/check-examples.sh --src <0.x checkout> --py-wheel
  <built 2.x wheel> --ts-tarball <packed 2.x tarball>`
  (`.github/workflows/upgrade-tool-ci.yaml` shows how CI builds them).

## Load-bearing conventions

- **Code executions never send `timeoutMs`.** The toolbox API rejects it
  (`UNSUPPORTED_OPERATION`). The SDKs
  implement interpreter `timeout` CLIENT-side — wait to the deadline, then
  interrupt the kernel (INT, ^C semantics) and return the interrupted result
  (`wait_or_interrupt` / `waitOrInterrupt`).
- **Every duration is in seconds** (the rule; the TypeScript exceptions are listed below). Python keeps bare names (`timeout`,
  `execution_timeout`, `request_timeout`, `escalate_after`,
  `reconcile_interval`, `idle_close`, ...); API-stored durations keep their
  `_seconds` names. TypeScript names end in `Seconds` (`timeoutSeconds`,
  `executionTimeoutSeconds`, `requestTimeoutSeconds`,
  `reconcileIntervalSeconds`, `idleCloseSeconds`, ...). Fractions are allowed
  unless the wire needs whole seconds (then they are validated). The wire
  keeps its own units (`timeoutMs` for toolbox process execs/waits).
  Timestamps are not durations. Known exceptions at this point: the
  per-call TypeScript `timeout` / `executionTimeout` / `escalateAfter` options
  (process, interpreter, docker, build-log, image, snapshot and volume calls)
  still take milliseconds until their area is renamed.
- **The request timeout is a total-time bound, the same in both SDKs.**
  Configured once on the root client (`DaytonaConfig.request_timeout` /
  `new Daytona({ requestTimeoutSeconds })`), default 60 s; `0`, or a value
  above 2,147,483.647 s (the longest JavaScript timer), means no limit (the
  events hub's durations clamp to that maximum instead). It bounds
  one ordinary call from sending it to reading the last byte of the response,
  with ONE deadline for the whole call: resolving the bearer (a token
  exchange or sandbox-token mint), the request, a 401 refresh and its retry
  share it (Python `_request_deadline` around the transport call, TS an
  abort-signal timer armed in the axios interceptor whose `deadlineAt` survives
  the retry; httpx's and axios' own timeouts are per phase or idle timers, not
  this). Every timer the SDK arms for it treats a delay above
  `MAX_TIMER_DELAY_MS` as no bound (`boundedTimerDelay`). Not cut by it: waits, long polls and holds
  (they pass their own `timeout`, or `0`; Python `/wait` paths and
  `unbounded_read`), streams (bounded only until their response headers
  arrive), and file transfers (Python `RequestSpec(transfer=True)` /
  `transport.request(..., transfer=True)`, TS `transferRequest(...)` on axios
  or `fetch(..., { transfer: true })`). A new call of one of those kinds must
  opt out the same way in both SDKs. File transfers are exempt from the total
  deadline but idle-bounded: one fails with `ClientTimeoutError` once no
  bytes move for the request timeout (connecting, the request body, the
  response headers, every body gap; `0` = no idle bound), and one that keeps
  moving bytes is never cut. Python gives a transfer httpx's per-phase timeout
  of that length (`_client_timeout`); TypeScript arms an idle watchdog
  (`IdleWatchdog`), kicked by upload progress where the runtime reports it
  (Node's http adapter, a streamed `fetch` body) and by every response chunk;
  where it cannot see upload progress (the fetch adapter, a request body over
  64 KiB) the bound starts at the response headers.
  **Every response body the SDK buffers whole is capped**, through both
  transports in both SDKs (JSON, the in-memory reads, multipart bulk reads,
  error bodies): `max_response_bytes` / `maxResponseBytes` on the client
  config (default 1 GiB, `0` = no limit, an integer up to 2^53 − 1) and per
  call on the in-memory reads (`read_bytes` / `readBytes`, `read_text` /
  `readText`, `read_many` / `readMany` (the whole multipart body), the
  recording download). A declared `Content-Length` over the cap is refused
  before the body is read, otherwise reading stops once it passes, and the
  connection is closed (Python `_body_limit`, TS `boundedAdapter.ts` /
  `readFetchText`). A refused success response is `PayloadTooLargeError`
  (413, `PAYLOAD_TOO_LARGE`, `SDK`); an error response keeps its status's
  error. TypeScript sends every axios request through `boundedAdapter`:
  Node's http adapter where the runtime has it, else its own `fetchAdapter`
  (never axios' xhr or fetch adapters, which buffer before checking in some
  runtimes); the runtime suite's `capOk` check holds every runtime to it.
  Streamed bodies (`download_file` / `downloadFile`, log, event and
  build-log streams, watches) are never capped. For large files, or content
  from a sandbox you don't trust, use `download_file` / `downloadFile`, which
  stream to disk with progress and cancellation. TypeScript's `downloadFile`
  is Node.js-only (a browser build throws `NotSupportedError`), so in a
  browser the in-memory reads, under their cap, are the only option.
  A per-call timeout (a wait's budget plus its slack, a git remote call, the
  Docker status poll, the events ticket mint; Python `RequestSpec(timeout=…)`
  / `transport.request(..., timeout=seconds)`, TS `config.timeout` / `fetch`'s
  `timeout`) replaces the request timeout for that call with the same
  meaning: one deadline from before credential resolution (the bearer
  exchange or mint, the request, a 401 refresh and its retry) to the last
  byte, `0` or a value above the timer maximum meaning no limit (Python
  `_total_seconds`, TS `armRequestDeadline`). The project-token exchange
  request is itself also bounded by the client's request timeout (it is
  shared by concurrent calls), so a per-call timeout longer than the request
  timeout does not lengthen it. On a stream the per-call timeout likewise
  bounds opening it, until its response headers (Python
  `_stream_open_seconds`, on `open_stream` and `open_exclusive_stream`);
  the transport bounds no stream's body as a total, by either timeout. One
  transport exception: Python's `open_exclusive_stream` keeps an explicit
  finite per-call `timeout` as httpx's idle-read bound on the body unless the
  spec sets `unbounded_read` (the build-log follower does); with no per-call
  `timeout` it clears the read bound, so the request timeout bounds no gap
  after the headers. Callers may bound a body themselves: Python's build-log
  discovery (`_discover`) and one-shot read (`read_build_logs`) wrap their
  body read in `anyio.move_on_after` with a deadline from their timeout. A
  transfer ignores any timeout it carries.
- **TypeScript rejects unknown option keys at runtime** (the rule; applied so
  far to the client configuration, see below). A covered options object
  declares its known keys once, with
  `optionKeys<T>()({ ... })` from `src/v2/options.ts` (a compile error when the
  list misses a key of `T` or names one `T` lacks), and its entry point calls
  `assertKnownOptions('TypeName', options, KEYS)` (or `snapshotOptions`, which
  also copies it), which throws `BadRequestError` (400, `BAD_REQUEST`, source
  `SDK`) naming the key; inherited enumerable keys count (symbol keys too), and a value that is
  not an options object (`null`, a boolean, an array: an older positional
  argument; a built-in such as a `Date` or a `Map`) is refused (the root
  constructor alone reads `null` as no configuration, as Python does `None`). A key in
  `RENAMED_OPTIONS` (`timeout`, `executionTimeout`, `escalateAfter`,
  `requestTimeout`, the 0.x `requestTimeoutMs`, `reconcileIntervalMs`,
  `idleCloseMs`) whose replacement is
  a known key gets "`timeout` was renamed to `timeoutSeconds` (seconds)".
  Pass-through maps inside options (`env`, `labels`, ...) are never checked.
  Python needs no helper: unknown keywords already raise `TypeError`. Applied
  so far to the client configuration (`DaytonaConfig`, `SandboxEventsConfig`);
  per-call options adopt it area by area, each with its duration renames.
- **Errors have one shape.** v2 category names are identical in both SDKs
  (`AuthenticationError` 401, `ForbiddenError` 403, `ClientTimeoutError` for
  the client's own timeout, ...); no `Daytona…` synonyms in v2. Errors carry
  `.code` only (no `error_code` / `errorCode`); `code` and `source` are open
  vocabularies (an unknown code is kept verbatim, the class falls back to the
  HTTP status). Constructors take the message plus options: TS
  `new XError(message, options?: ErrorOptions)`, Python
  `XError(message, *, status_code=None, code=None, source=None, ...)`, with the
  same defaults (`TransferCancelledError()` → "file transfer cancelled", 499,
  `REQUEST_CANCELLED`, `SDK`; `ClientTimeoutError` → 408, `REQUEST_TIMEOUT`,
  `SDK` on every transport path, message "operation timed out" by default; a
  field passed as `None` / `undefined` takes its default; `ConnectionError`
  → source `SDK`). Errors the SDK raises itself carry `source='SDK'`, and the
  catalog's status, code and class where the catalog has one (an invalid
  argument is `BadRequestError` 400 `BAD_REQUEST`; build them with
  `create_error` / `badRequest(message)` / `createDaytonaError(message,
  status, code, 'SDK')`, never `new DaytonaError(...)`, which a TS guard test,
  `tests/sdk-raised-errors.test.ts`, holds to a shrinking list); local
  I/O and protocol failures carry the source only. A response that breaks the
  contract is `BadRequestError` 400 `BAD_REQUEST` `SDK` naming the field (the
  rule; known exceptions at this point, which still raise
  `InternalServerError` 500 with no code: network policy, sandbox secrets,
  the computer-use status mappers, and the filesystem watch events, until
  their areas adopt it; the sandbox access-token mint and the pre-signed file
  URL follow the rule). Python callers should not star-import
  `daytona.v2` errors: `ConnectionError` / `FileNotFoundError` shadow the
  built-ins.
- **Generated wire types must not reach the public surface** (the rule; the
  remaining exceptions are the guards' `ALLOWED` lists). No public export,
  and no type in a public signature, comes from `daytona.v2._gen` /
  `src/v2/gen` (alias layers included). Inherited members, index signatures
  and base types count. `tests/test_public_surface_generated.py`
  and `tests/public-surface-generated.test.ts` walk everything reachable from
  the entry points and fail on a new violation or a stale entry in their
  `ALLOWED` lists, which only shrink.
- **Signals are bare names on the surface, `SIG*` on the wire** — the mapping
  lives in exactly one place per SDK (`signal_spec` / `signalWire`), and wait
  results strip the prefix on the way back.
- **Output is channel-filtered.** `stdout`/`stderr` come from their frame
  channels; rich interpreter outputs split into `results[]`, `logs`, and
  `error`; `execute_result` alone sets `isMainResult=true`; `setup` frames are
  provisioning narration, never part of `output`.
- **Rehydrated code handles are replay/control only.** A serialized
  `ProcessHandle` with `kind=code` still supports `logs`/`wait`/`kill`, but it
  does not yield a decomposed `InterpreterResult` on another worker; document
  rich code outputs only on the interpreter run path.
- **Root aliases exist only for first-guess discovery.** `sandbox.exec(...)`
  delegates to `process.run(...)`; `sandbox.run_code(...)` /
  `sandbox.runCode(...)` delegates to `interpreter.run(...)`.
- **Context-free interpreter runs share state.** `interpreter.run()` /
  `sandbox.run_code()` / `sandbox.runCode()` send no `sessionId` unless the
  caller names one or asks for `isolated`. Repeated runs share state within
  each language. `create_context` / `createContext` is how callers get independent
  state.
- **Explicit interpreter contexts surface loss, they do not self-heal.** If a
  named context is no longer available, `context.run(...)` / an
  explicit-context `interpreter.run(...)` must surface the typed
  `SESSION_NOT_FOUND` error unchanged. The caller decides whether to recreate
  the context and what code is safe to rerun.
- **Every pageable public list returns one envelope page.** Hidden pagination
  loops are forbidden; multi-page traversal lives in explicit iterators
  (`iter()`), each with a cursor-cycle guard. Non-pageable list surfaces such
  as `interpreter.list_contexts()` / `listContexts()` stay one-shot arrays.
- **SDK request attribution uses the Daytona header triple.** `X-Daytona-Source`
  (`sdk-typescript`, `sdk-python`, `sdk-python-async`),
  `X-Daytona-SDK-Version`, and `User-Agent: <source>/<version>` on every request.
  Python reads the version from `importlib.metadata`; TS
  from the generated, tracked `src/version.ts` (`npm run stamp-version`; runs
  automatically on `prebuild` so the publish workflow's `npm version` stamp
  reaches the bundle — never hand-edit it). Commit the regenerated file
  whenever the placeholder in `package.json` is realigned: consumers that
  compile the SDK source run no npm hooks, and sdk-ci fails on
  `git diff -- src/version.ts` drift. Browsers keep the
  `X-Daytona-*` pair and drop only `User-Agent`.
- **Region is per create, never config.** `region=` / `{ region }` on every
  create and on `find_by_name` / `findByName`; neither the root nor the v2
  config carries a region or reads `DAYTONA_TARGET`.
- **`create()` has one typed source.** Python takes `source: str | Image | None`
  positionally (`str` = snapshot name); TypeScript takes `snapshot` or
  `image: Image`. `sandbox.fork()` is the only fork entrypoint, and
  `create()` with no source sends no image field so the server can resolve the
  platform default.
- **Ports own preview exposure.** Preview/public port verbs live under
  `sandbox.ports` in both SDKs.
- **LSP is handle-shaped.** `lsp.start(...)` returns an `LspServer` handle;
  do not reintroduce flat per-request `(language, path, ...)` APIs.
- Python is **authored once, async**: every network-touching method is
  `async def` on the caller's loop, on classes named `Async*` (`AsyncDaytona`,
  `AsyncSandbox`, …). **`_sync/` is generated — never edit
  it (except `_sync/_runtime.py`).** Change the async class, run `python gen/gen_sync.py`, commit both;
  `tests/test_sync_surface.py` fails on drift and proves every public async
  method has a sync twin — the bare name (`AsyncSandbox` → `Sandbox`) —
  with identical parameters. Deadlines read
  `_clock.monotonic()` (tests patch that, not `time`, or they would also
  drive asyncio's scheduler). Sans-I/O `_core` carries the logic; keyword-only
  publics; no `Any` in public signatures. TS: options objects,
  no builder classes, no `any`/ts-ignore, discriminated unions for results.
- Tolerate unknown response fields everywhere.

## Shipped READMEs are public

`sdk-python/README.md` and `sdk-typescript/README.md` are packaged (PyPI `readme`,
npm `files`) and render on the registry pages. They are user-facing only:
install, quickstart, surface overview, errors, `DAYTONA_*` env vars. They must
not describe toolbox or proxy internals or wire shapes, ports, `/tmp` paths
or binaries. Contributor material (implementation notes, local verification)
lives in the unpackaged `sdk-python/DEVELOPMENT.md` / `sdk-typescript/DEVELOPMENT.md`.

## Verification

```bash
(cd sdk-python && uv venv && uv pip install -e ".[dev]" && .venv/bin/python -m pytest -x -q)
(cd sdk-typescript && npm install && npm run typecheck && npm test && npm run lint && npm run build)
(cd cli && go vet ./... && go test ./...)
python3 scripts/changelog/assemble.py check --dir changelog.d
scripts/gen-contract-types.sh --check     # only after touching contract/ or a generator
```

TypeScript runtime compatibility (the packed package in Node, Bun, Deno,
bundlers, Next.js, Nuxt, Remix, workerd, Lambda, Azure Functions and a
browser, against a local mock API; CI workflow `TypeScript runtimes`):
`(cd sdk-typescript && npm run build && npm run test:runtime)`. Run it after
touching the transports in `src/v2/http.ts`, the build, or `package.json`
`exports` / `imports`; see `sdk-typescript/runtime-tests/README.md`.

Before opening a PR, review the branch with `.claude/skills/deep-review` (adversarial, local-only,
never posted).

Patch coverage of a branch (target 60%; skill: `.claude/skills/coverage-preflight`):
generate the reports listed in `scripts/patch_coverage.py`, then
`python3 scripts/patch_coverage.py`.

Live suites, when credentials are available:
`DAYTONA_API_KEY=… ./e2e/run.sh` (both SDKs; `py` / `ts` for one).

## Pull requests

- The 2.x line lives on `main`: branch from it and target pull requests at it.
  The 0.x SDKs and CLI live on `release/v0`.
- Sign off every commit (`git commit -s`); sign the CLA once (the CLA check runs on every PR). See [CONTRIBUTING.md](CONTRIBUTING.md).
- E2E runs against the hosted deployment and skips when its secrets are not
  available (pull requests from forks). A PR that depends on API surface that
  is not deployed yet has red e2e — that is expected: **keep it a draft until
  its checks pass**, then mark it ready. Maintainers coordinate deployments;
  contributors don't need to.
- Do not merge with failing checks; a red non-draft PR is a bug report.
- `CI passed` is the check to require for test results: it waits for every
  other `pull_request` workflow that ran on the PR and fails if any of them did
  (E2E excluded; `.github/workflows/ci-passed.yaml`). A new CI workflow is
  counted automatically; add its name to `ci-passed-rerun.yaml` too.

## Architecture decision records — `docs/adr/`

Decisions about the SDK and CLI surface that outlive a PR (the parity rules,
versioning, the v0 → v2 migration, …) are recorded as
`docs/adr/NNNN-<title>.md` with a `Status:` line (`proposed`, `accepted`,
`implemented`, `superseded by NNNN`). Write them for an outside reader and
cover only SDK and CLI behavior. A PR that changes a recorded decision
updates its record.

## Licensing

`cli/` is AGPL-3.0 (`cli/LICENSE`); everything else is Apache-2.0 (`LICENSE`,
`COPYRIGHT`, `NOTICE`). Every handwritten source file starts with its SPDX
header — `Copyright Daytona Platforms Inc.` plus `SPDX-License-Identifier:
AGPL-3.0` under `cli/`, `Apache-2.0` elsewhere — and the `License headers`
check enforces it (`.licenserc.yaml`). Add the header to every new file, or
run `license-eye -c .licenserc.yaml header fix`. Generated files are exempt
(they say so in their first line), except the synchronous Python surface,
whose generator (`sdk-python/gen/gen_sync.py`) emits the header itself.
Contributions need a DCO sign-off on every commit and a one-time CLA
signature ([CONTRIBUTING.md](CONTRIBUTING.md)).

## Releases

[RELEASING.md](docs/RELEASING.md) · [VERSIONING.md](docs/VERSIONING.md) ·
[CHANGELOG.md](CHANGELOG.md).
