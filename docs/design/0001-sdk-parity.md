# 0001. One interface in two languages

Status: implemented

## Context

Daytona ships two SDKs: Python (`daytona`) and TypeScript (`@daytona/sdk`).
Users move between them, examples get ported from one to the other, and
support questions rarely say which language they came from. When the two
SDKs drift, a method that exists in one is missing in the other, an error
caught by type in Python surfaces as a generic error in TypeScript, and a new
server value crashes one SDK while the other shrugs it off.

Both SDKs talk to the same API, described by the contract files under
[`contract/`](../../contract/). The question this record answers is how
closely the two SDKs have to match, and what keeps them matched over time.

## Decision

**Same shape, same release.** Every feature or fix lands in both SDKs in the
same pull request, with tests in both. The client, the sandbox handle and its
namespaces (`process`, `interpreter`, `fs`, `git`, `ports`, `lsp`, and so on)
have the same structure and the same method set in both languages. Compare
[`../../python/src/daytona/v2/sandbox.py`](../../python/src/daytona/v2/sandbox.py)
with [`../../typescript/src/v2/Sandbox.ts`](../../typescript/src/v2/Sandbox.ts).

**Naming follows each language.** Python uses `snake_case` and keyword-only
arguments; TypeScript uses `camelCase` and options objects. Python timeouts
are seconds and TypeScript timeouts are milliseconds, each following its
platform's convention. These are spelling differences, not shape differences:
`sandbox.run_code(...)` and `sandbox.runCode(...)` take the same inputs and
return the same result.

**One error hierarchy.** Both SDKs root every error at `DaytonaError`. Below
it sits one base class per HTTP status category (`DaytonaNotFoundError`,
`DaytonaConflictError`, `DaytonaRateLimitError`, and so on), and below those,
one subclass per error code the contract defines. A caller can catch broadly
by status or narrowly by code, and the same `except` or `instanceof` works
in both languages. Each SDK has a codes module,
[`../../python/src/daytona/v2/codes.py`](../../python/src/daytona/v2/codes.py)
and [`../../typescript/src/v2/codes.ts`](../../typescript/src/v2/codes.ts),
with one catalog per error source (the API, the in-sandbox toolbox, the
toolbox proxy and the API router) plus a shared generic table. Errors are
dispatched on the pair `(source, code)`, so two sources can reuse a code name
without colliding. The classes live in
[`../../python/src/daytona/v2/errors.py`](../../python/src/daytona/v2/errors.py)
and
[`../../typescript/src/v2/errors/DaytonaError.ts`](../../typescript/src/v2/errors/DaytonaError.ts).

**Contract drift tests.** Each SDK's test suite reads the committed contract
and fails when the SDK falls out of step with it:

- A new error code on any source fails both suites until both catalogs know
  it.
- Every route literal in SDK source must resolve to exactly one operation in
  [`../../contract/api-openapi.json`](../../contract/api-openapi.json) or
  [`../../contract/toolbox-openapi.json`](../../contract/toolbox-openapi.json),
  and every toolbox operation is either called or listed on an allow-list
  with a reason. See
  [`../../python/tests/test_core.py`](../../python/tests/test_core.py) and
  [`../../typescript/tests/unit.test.ts`](../../typescript/tests/unit.test.ts).

Wire types are generated from the same contract for both SDKs
(`typescript/src/v2/gen/`, `python/src/daytona/v2/_gen/`) by
[`../../scripts/gen-contract-types.sh`](../../scripts/gen-contract-types.sh),
and CI checks that the generated files are current.

**Python is async-native, with a generated sync surface.** Python code is
written once, as `async def` methods on classes named `Async*`
(`AsyncDaytona`, `AsyncSandbox`). The synchronous surface (`Daytona`,
`Sandbox`) is generated from it by
[`../../python/gen/gen_sync.py`](../../python/gen/gen_sync.py) into
[`../../python/src/daytona/v2/_sync/`](../../python/src/daytona/v2/_sync/).
[`../../python/tests/test_sync_surface.py`](../../python/tests/test_sync_surface.py)
fails if the generated files are stale or if any public async method lacks a
sync twin with identical parameters. TypeScript is async by nature and has a
single surface.

**Forward compatibility.** The API grows faster than SDKs get upgraded, so
an older SDK has to survive values it has never seen:

- Unknown response fields are tolerated everywhere.
- Every string enum in the contract is tagged as open
  (`x-daytona-open-enum`) or exact (`x-daytona-exact-enum`), and every
  generator reads the tag. Open enums stay open in the generated wire types:
  a new server value parses instead of failing.
- Public SDK fields backed by a contract enum are closed: the known values
  plus `'unknown'`. The SDK normalizes at the boundary and keeps the original
  string on a sibling field (`raw_<field>` in Python, `raw<Field>` in
  TypeScript). See
  [`../../typescript/src/v2/knownValue.ts`](../../typescript/src/v2/knownValue.ts).

Callers get exhaustive `match` and `switch` over a closed set, and still see
what the server actually sent.

## Alternatives

**Generate both SDKs from the OpenAPI spec.** Generated clients mirror the
wire, not the task: they expose pagination envelopes, raw DTOs and one method
per endpoint, and they give each language a different flavor of the same
awkwardness. The SDKs use generated types at the wire boundary and write the
public surface by hand.

**Let each SDK evolve on its own and reconcile later.** This is the default
outcome without a rule, and reconciliation never catches up. Same-PR parity
costs more per change and keeps the gap at zero.

**Identical names in both languages.** Forcing `camelCase` into Python or
seconds into TypeScript makes each SDK feel foreign to its own users. Shape
parity matters to people porting code; spelling parity doesn't.

**Hand-maintained sync and async Python classes.** Two copies of every method
drift within weeks. Generating one from the other keeps them identical by
construction.

**Closed wire enums.** Rejecting unknown values at parse time turns every
additive server change into a crash in older SDKs. Open on the wire, closed in
public with `'unknown'`, gives both safety and type precision.

## Consequences

- Contributors change two SDKs per feature. A pull request that touches only
  one is incomplete unless the change is language-specific (a packaging fix,
  for example).
- A new error code, route or enum value in the contract shows up as a failing
  test in both suites. Maintainers can't quietly ship a contract change that
  one SDK ignores.
- The Python sync surface can't be edited directly. Change the async class
  and regenerate.
- Users can rely on catching `DaytonaError` subclasses by status or by code in
  either language, and on `'unknown'` instead of a crash when the server adds
  a value. Code that switches over an enum field should handle `'unknown'`.
- Adding a value to a public enum is additive. Removing one, or making an open
  enum exact, is a breaking change.
