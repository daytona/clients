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

`services.AsyncVolumeService` implements the platform `volume` namespace:
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

`AsyncSandbox.delete()` (and its generated sync twin) requests deletion
without waiting for completion. The v2 client's `delete()` delegates to it.
`wait_for_state(['destroyed'])` waits for an observed lifecycle state, but
deleting the sandbox's image or snapshot immediately afterwards can still
raise `ConflictError` (409).

The existing waiter treats `destroying` as progress when targeting
`destroyed`. Its refresh still propagates a typed 404; the 404-to-`destroyed`
mapping is local to `AsyncSandbox.delete()`'s single follow-up refresh, not
`refresh_data()` or the waiter.

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
  none). The deadline starts before the bearer is resolved; the project-token
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
