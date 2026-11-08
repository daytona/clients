# Daytona Python SDK — development

Contributor notes. Not shipped in the package (`pyproject.toml` `readme` points
at `README.md` only). The user-facing README must stay free of internal
details — see `../AGENTS.md`.

## Package layout

`src/daytona/` is the Python package. The root (`__init__.py` + `_client.py`)
exports the two root clients (`Daytona`, `AsyncDaytona`), their shared
`DaytonaConfig`, and the error hierarchy. Every operation goes through a
version namespace:

- **`src/daytona/v2/`** — the v2 SDK. The authored classes are async-native:
  every network-touching method is `async def` and the async names carry the
  `Async` prefix (`AsyncDaytona`, `AsyncSandbox`, `AsyncProcessHandle`,
  `AsyncLspServer`, …; the clients `Daytona` / `AsyncDaytona` are not exported
  from `daytona.v2`, the root builds them). The synchronous surface (`Daytona`, `Sandbox`,
  `ProcessHandle`, `LspServer`, …) lives in `src/daytona/v2/_sync/`, which is
  **generated from the async classes** by `gen/gen_sync.py`. Never edit files
  in `_sync/` except `_sync/_runtime.py` (the hand-written sync bridge).
  Change the async class, run `python gen/gen_sync.py`, commit both.
  `tests/test_sync_surface.py` fails on drift and proves every public async
  method has a bare-name twin with identical parameters.

- **`src/daytona/v0/`** — the publish-time v0 bundle, copied from a
  `daytona/clients` release tag by `scripts/bundle-v0.sh` and **never
  committed** (git-ignored; absent in a source checkout). The `hatch artifacts`
  setting in `pyproject.toml` includes it in wheel and sdist at publish time
  despite the gitignore.

## Volumes

`services.AsyncVolumesService` implements the platform `volumes` namespace:
list/iterate, get by name or ID (optionally create on a miss), create and
delete. `volume.py` re-exports it. `types.py` owns `Volume`, `VolumeMount`,
`VolumeState` and `VolumeType`. `_api_core.py` parses volume responses with
the generated contract models; unknown enum values remain available through
`raw_status` and `raw_type`.

`AsyncSandbox.volumes` lazily creates `AsyncSandboxVolumesService` against
the sandbox's transport and clears the cached service when that transport
changes. `sandbox_volumes.py` owns the shared pull request validation and
response parser. Pull submits or checks once; callers retain the request ID
and decide when to retry. Tests cover sync/async HTTP parity, wait budgets,
validation, pending replay, error metadata and unknown statuses.

`create()` and `create_macos()` serialize `volumes` mounts;
`create_from_build()` forwards the option to `create()`. macOS accepts only
read-write disk mounts and cannot combine them with `persistent=True`.
`gen/gen_sync.py` includes both volume services; regenerate `_sync/` after
changing their async APIs. Volume names are exported by `daytona.v2`, with
`VolumeMountV2` / `VolumeMountV0` distinguishing the shared type names.

## Bundling v0

`Daytona().v0` is the v0 Python SDK (from `daytona/clients`) bundled under
`daytona.v0`. The bundle is produced at publish time, not committed, and must
never be hand-edited. The tooling lives in `scripts/`.

**Bundle command:**

```bash
scripts/bundle-v0.sh [--lang python|typescript|all] [--src DIR] [TAG]
scripts/bundle-v0.sh --clean
```

- `TAG` defaults to the pinned 0.x tag in `scripts/v0-sdk-version` (line 1,
  today `v0.230.0`; line 2 is the commit the tag peels to, which the scripts
  verify), the single source of truth for releases. Bumping the v0
  namespace is a two-line PR to that file plus a 2.x release. Passing `TAG` or
  setting `DAYTONA_V0_TAG=<tag>` (shape `vX.Y.Z`) overrides it for testing only.
  `scripts/resolve-v0-tag.sh [--verify]` prints the effective tag and,
  with `--verify`, fails unless it exists in `daytona/clients` (an sdk-ci check).
- `--src DIR` uses an existing local checkout of `daytona/clients` at that tag
  instead of cloning one.
- `--clean` removes the bundle from `src/daytona/v0/` and empties the `v0-sdk`
  extra in `pyproject.toml`. Restore `sdk-typescript/package.json` and
  `sdk-typescript/package-lock.json` with `git` if needed.

**What the Python helper (`scripts/bundle_v0_python.py`) does:**

1. Copies `<sdk-python>/src/daytona/` to `sdk-python/src/daytona/v0/`.
2. Rewrites every absolute `daytona` import to `daytona.v0` and fails if any
   absolute import remains.
3. Replaces `importlib.metadata.version("daytona")` (which would otherwise
   return the 2.x distribution version) with a fixed function that returns the
   bundled release's own version string, so the v0 SDK's `X-Daytona-SDK-Version`
   header reports the bundled release.
4. Hides v0's client classes (`Daytona`, `AsyncDaytona`, `DaytonaConfig`)
   behind the private names the root's `.v0` reads (`_Daytona`, …); the public
   names raise an `ImportError` naming `Daytona().v0`.
5. Appends `…V0` aliases for the names `daytona.v2` exports too (e.g.,
   `SandboxV0`, `ImageV0`), and writes `_bundle.py` with provenance metadata.
6. Fills the `v0-sdk` extra in `pyproject.toml` with v0's runtime dependencies and
   its six generated API clients, each pinned to the bundled release's version.

**Guard:** `scripts/check-no-bundled-v0.sh` fails CI if the bundle or
its `pyproject.toml` fills were committed. It checks the git index (so
`.gitignore` cannot hide a force-added file). The Python `v0-sdk` extra must be
committed as `v0-sdk = []`; the bundle script fills it at publish time.

**Local smoke test:**

```bash
# 1. Bundle v0 into the Python package
scripts/bundle-v0.sh --lang python

# 2. Install into a scratch venv and verify the import works
cd sdk-python
uv venv /tmp/daytona-v0-smoke
uv pip install --python /tmp/daytona-v0-smoke/bin/python -e '.[v0-sdk]'
/tmp/daytona-v0-smoke/bin/python -c "
from daytona import Daytona, DaytonaConfig
assert callable(Daytona(DaytonaConfig(api_key='x')).v0.list)
print('ok')
"

# 3. Clean up (empties the v0-sdk extra and removes src/daytona/v0/)
../scripts/bundle-v0.sh --clean
```

## Design references

- Python ↔ TypeScript parity contract: [`../AGENTS.md`](../AGENTS.md).

## Implementation notes

### Delete completion

`AsyncSandbox.delete(timeout=60)` (and its generated sync twin) requests
deletion, then waits for `destroyed`. A typed 404 on any read during that wait
also means completion; the handle is updated to `destroyed` (raw state
`deleted`). Ordinary `refresh_data()` and other waits still propagate 404. The
private waiter (`_wait_for_state`) treats `destroying` as progress when
targeting `destroyed`. `stop()` similarly waits for `stopped`.

All lifecycle methods take `timeout=` in seconds (default 60, `0` unbounded).
The v2 client's methods delegate to the handle, including when given an id.
Timeout raises `RequestTimeoutError` (source `SDK`); a terminal state raises
`SandboxTerminalStateError`. Server errors decide class and persistence
support, without client-side lifecycle prechecks.

An observed `destroyed` state does not guarantee that deleting the sandbox's
image or snapshot will succeed without a conflict. Callers must still handle
`ConflictError` (409) from those operations.

### Create, fork and public metadata

Once the create or fork request has succeeded, create and fork return their
handle however the wait ends: budget spent, terminal state or a failed read
(any `DaytonaError`; `_wait_without_raising_for_its_end`). Non-SDK errors and
cancellation propagate; a cancelled create may already have created a
sandbox. A raised create or fork means the request itself failed, except for
`SandboxCreateRequestTimeoutError` and `SandboxCreateConnectionError` (they
carry the idempotency key because a sandbox may exist), caller cancellation,
and an unexpected non-SDK error during the wait. The fork's detail read counts
against `wait_seconds`; if it fails, the handle is built from the fork
response (id, status, `forked_from`, class) and no wait follows. Once a
lifecycle wait has observed its target, a failed metadata refresh is
suppressed. Create uses whole-second `wait_seconds`;
fork permits fractions and uses `0` to skip waiting. Linux forks refresh the
source before submission when waiting: running sources wait for `started`,
paused sources retaining memory wait for `paused`, and other sources wait for
`stopped`. macOS forks wait for `started` or `stopped`.

`create_from_build()` submits the build as an ordinary request (bounded by
`request_timeout`), then always waits for the image with its independent
`build_timeout` budget (`None` or `0` is unbounded), starting after the
submission, then creates with the caller's full `wait_seconds`. An image in
`failed` or `error` is a failed build. Build failure raises `ImageBuildFailedError`
with `image_id` and `build_error`; build timeout raises `RequestTimeoutError`.

All create sizing lives in `resources` (`Resources` / `MacosResources`).
`_core.gib_to_mib` refuses GiB values that do not convert to whole MiB, and
`mib_to_gib` reads back exactly. Public models are frozen keyword-only
dataclasses with tuples and timezone-aware datetimes, mapped from the
generated wire models in `_api_core.py`. The handle's metadata is read-only
properties over an immutable `_SandboxInfo` snapshot that `_update(...)`
replaces; `gen_sync.py` emits direct getters for the names in
`_SYNC_DIRECT_PROPERTIES`. Constructors of the exported handle and service
classes take `_`-prefixed parameters and are internal; the model dataclasses
stay constructible (keyword-only). `to_json()` returns a
`SandboxHandleJSON` with known metadata but no URLs or credentials; callers
reconnect with `get(json["sandboxId"])`.

Long polls (sandbox waits, image pulls, `images.get` / `snapshots.get` with
a budget) are governed by the wait budget, not `request_timeout`: each server
hold is the smaller of its slice (60 s for artifacts) and the remaining
budget, in whole milliseconds, and each read is capped at the smaller of the
hold plus a few seconds of slack and the time left before the wait's
deadline, so no read runs past it. That holds for sandbox waits
(`_refresh_data_within(deadline=...)`, as TypeScript's `refreshDataWithin`),
image pulls and artifact gets alike; a read the deadline cut is the wait
running out (`RequestTimeoutError`, source `SDK`). Artifact gets repeat holds
until the artifact settles, and raise that error when the budget runs out
first, never returning an unsettled observation; a budget above the timer
maximum repeats them without a deadline (`services._get_artifact`).
`ListResult` / `SearchResult` copy
`items` into a tuple in their constructors. A sandbox-token mint answered
with 405/501 disables minting for the handle; a 404 falls back to the API
credential for that request only.

`list()` / `iter()` accept only `cursor` and `limit`. `search()` carries the
filters, including public `states`, which map to the API's status vocabulary
at the boundary. Named creates permanently support get-or-create; the
name-key helpers are internal. `find_by_name()` returns all matches and is
the explicit exception to the one-page list rule.

### Other implementation notes

- Interpreter runs code through
  `POST /processes {kind:"code", code, language, sessionId?}`.
  `timeout_ms` is client-side only. Context-free runs omit `sessionId` and
  share per-language interpreter state, whose `env` is fixed on first use.
- Pre-signed file URLs are minted server-side: the SDK POSTs
  `{method, path, expires_at}` to `/sandboxes/{id}/files/sign`
  and composes the link from its own toolbox
  base plus the returned `signature` / `expires_at`; the signing key never reaches
  the client. `expires_in_seconds` is validated to 1 to 604800 inclusive
  (`SIGNED_FILE_MAX_TTL_SECONDS`) before the request. A direct `toolbox_url`
  override is rejected because pre-signed URLs require the sandbox's public
  endpoint. The raw decoded sign body goes through
  `filesystem._signed_file_url_from_payload` unshaped, so a JSON `null`, a
  list or a malformed object all raise the same 500 invalid-payload error
  (parity with TS). `sandbox.rotate_signing_key()` POSTs
  `/sandboxes/{id}/signing-key/rotate` with no body and returns `None` after
  the API's empty 204 response. The key is never returned; every outstanding
  link for the sandbox becomes invalid. Like minting, it is refused
  locally on a direct `toolbox_url` handle.
- Typed errors: status-level base classes plus code-specific subclasses;
  the code tables (`v2/_codes.py`, public read-only view `daytona.v2.codes`)
  are drift-tested against the committed catalogs under `../contract/`.
  Constructors are `XError(message, *, status_code=None, code=None,
  source=None, ...)`. Errors the SDK raises itself carry `source="SDK"`, and
  the catalog's status and code where it has one (`create_error(message,
  status, code, "SDK")` dispatches a `(source, code)` pair); client timeouts
  default to 408 `REQUEST_TIMEOUT`, connection failures carry the source only.
  One deadline covers the whole call (the bearer, the request, a 401 refresh
  and its retry): `AsyncTransport.send` / `request` / `open_stream` hold it.
- Request timeout: `request_timeout` (default 60 s, `0` = none) bounds the
  total time of one ordinary request (`_transport._request_deadline`, an
  `anyio.fail_after` around the httpx call, whose own timeouts are per phase).
  A spec with its own budget sets `timeout`, which replaces the request
  timeout with the same total-time meaning (`_transport._total_seconds`;
  `0` or above the timer maximum = none), or `unbounded_read` for a hold
  with no budget; file
  transfers set `RequestSpec(transfer=True)` or call
  `transport.request(..., transfer=True)` and are not bounded, whatever
  timeout they carry; streams (`open_stream`, `open_exclusive_stream`) are
  bounded only until their response headers arrive, by the spec's own
  `timeout` when it has one, else the request timeout
  (`_transport._stream_open_seconds`; `0` or above the timer maximum =
  none). The transport bounds no stream's body as a total, but
  `open_exclusive_stream` keeps an explicit finite per-call `timeout` as
  httpx's idle-read bound on the body unless the spec sets `unbounded_read`;
  with no per-call `timeout` it clears the read bound, so the request timeout
  bounds no gap after the headers (a real-socket test in
  `test_client_config.py` pins each case). `build_logs._discover` and
  `read_build_logs` bound their body read themselves (`anyio.move_on_after`
  with a deadline from their timeout). The deadline starts before the bearer is resolved; the project-token
  exchange request is also bounded by the client's request timeout. The TypeScript transport
  implements the same rule.
- Build logs use URL-only discovery (`{"url": "https://…/build-logs/…"}`).
  `build_logs()` reads the discovered endpoint with `follow=false&format=text`;
  `follow_build_logs()` uses `follow=true&format=sse` and the processed cursor.
  State events carry status, optional error and deadline; availability is a
  separate `unavailable` event. Validate the destination before attaching the
  project key and refuse redirects. Both modes preserve bounded reads and deadlines.
  Match discovered resources by UUID identity, including sandbox base58 IDs and
  region tags, because discovery canonicalizes the caller's ID spelling.
- Reserved sandbox `env` names: platform-reserved prefixes plus every
  `DAYTONA_*` name other than
  `DAYTONA_SANDBOX_TOKEN`, `DAYTONA_API_URL`, `DAYTONA_OTEL_ENDPOINT` and
  `DAYTONA_DOCKER_START`. The API rejects the create with `400` naming the
  key.

## Local verification

```bash
uv venv && uv pip install -e '.[dev]' && .venv/bin/python -m pytest -x -q
```

The suite also runs from the sdist, which unpacks as `daytona-<version>/`
with this project's `src/`, `tests/`, `pyproject.toml` and the two `gen/`
scripts the tests import at its root, and no `sdk-python/` directory or
anything else from the repository. A test that reads a file outside
`sdk-python/` (a contract under `contract/`, the TypeScript SDK, `scripts/`,
`upgrade/`) gets its path from `_repo_file("contract/…")` or
`_repo_scripts()` in `tests/_repo.py`: in a checkout they return the path (a
missing file still fails), outside one they skip that test, naming the file.
Never build such a path from `Path(__file__).parents[…]` directly.

## Streaming response ownership

Build-log discovery, follow and one-shot reads go through
`AsyncTransport.open_exclusive_stream`, which gives each response a fresh
HTTPX `AsyncClient` of its own on an SDK-owned transport. Scoped transports
inherit the factory, not an active stream client. Other streams (process-log
iteration, filesystem watches) and ordinary API requests keep using the
root's shared client.

Cancellation needs no socket shutdown on the async path: the reader is a
suspended coroutine, so cancelling it (task cancel, a cancel scope, the
caller's `timeout`) releases the response from its `finally`, shielded, and
closes the exclusive client with it. Nothing else shares that connection.
Successful streaming responses keep bounded header timeouts but clear the
documented request `timeout.read` extension before consuming their body;
finite JSON and error bodies keep whole-body budgets enforced by cancel
scopes. The root transport tracks its exclusive responses across scopes:
closing the root also closes active observations, while closing a borrowed
scope does not cancel its siblings.

An injected `client=` is borrowed. Without `stream_client_factory=`, build-log
streams run on it (cancellation still works, since no socket is shut down);
with a factory, the factory must return a **fresh client with a nonshared
connection pool**, preserving the integration's TLS/proxy settings.

A cancelled follower re-raises the original `CancelledError` with its
`BuildLogInterruptedError(code="BUILD_LOG_CANCELED")` attached
(`errors._attach_interrupt_error`). `SyncBridge.call` surfaces that error in
place of `KeyboardInterrupt` once the cancelled task has unwound, which is how
`Sandbox.follow_build_logs` keeps the last checkpoint on Ctrl-C. On the
sync surface the `on_logs` / `on_event` callbacks run on the bridge's loop
thread, like every other callback of `Daytona`: a callback that blocks
stalls the client's other calls until it returns.
