# 0004. Version namespaces and the move from 0.x

Status: implemented

## Context

SDK 2.x targets a new API with a different surface. Existing users have
code written against the 0.x SDK under the same package names, `daytona` on
PyPI and `@daytona/sdk` on npm. Making 2.x a clean break would force every
0.x user to rewrite before upgrading. Keeping 0.x as a separate package would
split the install and the error handling across two packages.

The question is how one package serves both APIs, so users can upgrade the
package first and move code over one call at a time.

## Decision

**A thin root over one namespace per API version.** The package root exports
the client (`Daytona`, plus Python's `AsyncDaytona`), a flat shared
`DaytonaConfig`, the shared error hierarchy, and the API URL constants. The
root client has no resource methods. It exposes two lazy, cached
properties: `.v0` builds a 0.x client and `.v2` builds a 2.x client.

```python
from daytona import Daytona

d = Daytona()
sandbox = d.v2.create()        # 2.x API
sandbox_v0 = d.v0.create()     # 0.x API, same package
```

The roots are
[`../../sdk-python/src/daytona/__init__.py`](../../sdk-python/src/daytona/__init__.py)
with [`../../sdk-python/src/daytona/_client.py`](../../sdk-python/src/daytona/_client.py),
and [`../../sdk-typescript/src/index.ts`](../../sdk-typescript/src/index.ts) with
[`../../sdk-typescript/src/Daytona.ts`](../../sdk-typescript/src/Daytona.ts). In
Python and in Node, importing the root doesn't load v0: it loads on the first
use of `.v0` or an import of the `v0` namespace. A browser bundle of the
TypeScript root includes v0, so that `.v0` stays synchronous there too (see
Consequences).

**v0 is bundled at publish time from a pinned 0.x tag.** The `v0` namespace
(`daytona.v0`, `@daytona/sdk/v0`) is the 0.x SDK from this repository's
`release/v0` line, at the tag and commit named in
[`../../scripts/v0-sdk-version`](../../scripts/v0-sdk-version).
[`../../scripts/bundle-v0.sh`](../../scripts/bundle-v0.sh) copies it into the
package when publishing. The bundled tree is never committed, and
[`../../scripts/resolve-v0-tag.sh`](../../scripts/resolve-v0-tag.sh) fails CI
if the pinned tag moves. Nothing is patched into v0's behavior: a v0 fix
reaches 2.x users by releasing it on the 0.x line and bumping the pin, which
is a manual pull request (the pin, plus the migration tool's v0 data; see
RELEASING, "The v0 pin") and a 2.x release. Within 2.x the pin moves only to a
0.x release that stays compatible for `.v0` callers, runtime floor included
([`../VERSIONING.md`](../VERSIONING.md), "Public API surface").

**One shared error hierarchy.** The root defines `DaytonaError` and the
twelve status categories both versions share (`DaytonaBadRequestError`,
`DaytonaAuthenticationError`, `DaytonaNotFoundError`, `DaytonaRateLimitError`,
and the rest). Errors from either namespace match them:

```python
from daytona import DaytonaNotFoundError

try:
    d.v0.get("missing")
except DaytonaNotFoundError:
    ...
```

In Python, see
[`../../sdk-python/src/daytona/_errors.py`](../../sdk-python/src/daytona/_errors.py);
both namespaces' classes inherit the root markers. In TypeScript,
[`../../sdk-typescript/src/errors.ts`](../../sdk-typescript/src/errors.ts) matches
through a process-wide registry, so `instanceof` works across the ESM and
CommonJS builds and the bundled v0. Version-specific errors stay in their
namespace.

**Clients only through the root.** Neither namespace exports its own client
class. `from daytona.v2 import Daytona` and `from daytona.v0 import Daytona`
raise an `ImportError` that points at `Daytona().v2` or `Daytona().v0`. In
TypeScript, `@daytona/sdk/v2` and `@daytona/sdk/v0` don't export `Daytona`.
For type annotations, the roots export `DaytonaV2Client` and
`DaytonaV0Client` (plus Python's async variants). Classes both namespaces
export carry suffixed aliases, such as `SandboxV0` and `SandboxV2`, so one
module can import both.

**The 2.x client targets Daytona Cloud.** Neither the root nor the v2 config
takes an API URL. A config that passes one fails with a message that points
custom-URL users at SDK 0.x. `d.v0` always uses `V0_API_URL`.

**What 0.x users change.**

1. Upgrade the package. Construct the client from the root as before:
   `Daytona(DaytonaConfig(...))`.
2. Call the 0.x API through `d.v0`: `d.create(...)` becomes
   `d.v0.create(...)`. Root imports of 0.x names such as
   `from daytona import Sandbox` now raise an `ImportError` that names the new
   location (`daytona.v0`). The full list is in
   [`../../sdk-python/src/daytona/_v0_root_names.py`](../../sdk-python/src/daytona/_v0_root_names.py).
3. Catch errors from the root. `except DaytonaError` and the status classes
   keep working for both namespaces.
4. Move calls to `d.v2` when ready. Region is now a per-create argument, not
   config, and the config's `target` field is deprecated with a warning that
   points at v0's per-call `target`.

## Alternatives

**Publish 2.x as a new package.** A new name would keep 0.x untouched but
split installs, documentation and error handling, and leave the familiar
package name on an old API. One package with namespaces keeps a single
install.

**Ship 2.x as a clean break under the same name.** Every 0.x user would have
to rewrite before upgrading. Namespaces let them upgrade first and migrate
gradually.

**Commit a copy of the v0 source to `main`.** A vendored copy drifts from the
0.x line and invites local patches. Bundling from a pinned tag at publish
time keeps one source of truth for v0 and makes every v0 change a reviewed
pin bump.

**Separate error hierarchies per namespace.** Callers with mixed v0 and v2
code would need two `except` clauses for every failure. A shared root lets
existing handlers keep working.

**Export clients from each namespace.** Two ways to build a client means two
configurations and two places for defaults to diverge. A single root entry
point keeps configuration in one place.

## Consequences

- 0.x users upgrade the package and change call sites to `d.v0` without
  rewriting their logic. Code that catches the shared error classes keeps
  working.
- The v0 surface is frozen at the pinned 0.x release. New v0 behavior arrives
  only through a 0.x release and a pin bump.
- Source checkouts and unbundled installs have no v0: in TypeScript, `d.v0`
  throws a "not included in this build" error. Published packages always
  carry the bundle.
- In browsers, a bundle of the TypeScript root includes v0, even for an app
  that uses only 2.x: the root is the only way to build a client, and
  importing types from `@daytona/sdk/v2` doesn't change what the root pulls
  in. A browser app that needs no v0 can alias the root's package-internal
  `#v0-loader` import to a stub module in its bundler configuration; its
  bundle then carries no v0. The stub exports a `loadV0()` that returns
  `{ __daytonaV0Unbundled: true }`, so `.v0` throws the "not included in this
  build" error. The hook is internal: its shape may change in any release
  ([`../VERSIONING.md`](../VERSIONING.md), "Public API surface").
- In Python, `hasattr(daytona, "Sandbox")` raises `ImportError` instead of
  returning `False`, the cost of guiding `from daytona import Sandbox`.
- The shared error classes are a public contract between both namespaces.
  Changing their shape in a future major also means changing the TypeScript
  registry keys, so old and new copies don't match each other's errors.
