# Daytona Python SDK

The official Python SDK for Daytona sandboxes. Import module and package name
are both `daytona`.

> **Alpha.** This SDK is published as alpha prereleases. Pin the exact
> version you want (see the release history on PyPI) — a bare
> `pip install --pre daytona` picks the highest prerelease on PyPI, which is
> not necessarily the newest alpha. The API may still change between alphas.

## Install

```bash
pip install daytona==X.Y.ZaN          # substitute the alpha you want
pip install 'daytona[otel]==X.Y.ZaN'  # adds OpenTelemetry request spans
pip install 'daytona[v0-sdk]==X.Y.ZaN'    # adds the v0 namespace (Daytona().v0)
```

The legacy package name `daytona-sdk` (`daytona_sdk`) is published only from
the 0.x line: installing or upgrading it never gives you 2.x. Install
`daytona`.

Python 3.10+.

## Quickstart

`Daytona` is the synchronous client. `AsyncDaytona` is the asynchronous one.
Every operation goes through a version namespace: `d.v2` for the current API,
or `d.v0` for the previous Daytona API (requires `daytona[v0-sdk]`; see
[v0 API](#v0-api) below).

Resource types (`Sandbox`, `Image`, `ImageBuilder`, …) live in `daytona.v2` or
`daytona.v0`. Importing them from the package root raises an `ImportError` that
names where to import from. The clients are the other way round: the root is the
only way to build one (`Daytona().v2`, `Daytona().v0`), and neither namespace
exports `Daytona`, `AsyncDaytona` or `DaytonaConfig` (`from daytona.v2 import
Daytona` raises an `ImportError` naming the root form). To annotate a stored
client, import its type from the root: `DaytonaV2Client` / `AsyncDaytonaV2Client`
(and `DaytonaV0Client` / `AsyncDaytonaV0Client`, which need the `v0-sdk`
extra).

```python
from daytona import Daytona, DaytonaConfig

with Daytona(DaytonaConfig(api_key="dtn_...")) as d:
    d.v2.images.pull("python:3.12", name="py312")  # waits until ready
    sandbox = d.v2.create("py312", wait_seconds=60)
    result = sandbox.exec("python -V")
    print(result.exit_code, result.output)
    d.v2.delete(sandbox)
```

`images.pull()` waits until ready by default. Set `wait=False` to skip the
readiness wait, or `timeout=120` for a 120-second budget (`None` or `0` is
unbounded). A failed import raises `ImageBuildFailedError` with the server's
error message. `images.build()` remains non-waiting.

`with Daytona(...) as d:` releases the client's connection pools when the block
ends (also on cancellation). A short script may instead let the interpreter
finish — teardown drops the sockets. In a long-lived process, prefer one
`Daytona` for the process's lifetime, or close each one you create: a client
you drop without closing keeps its idle keep-alive connections open until
interpreter exit.

`d.v2` on the sync `Daytona` runs the async implementation on a private
background event loop and blocks the calling thread. Calling it from inside a
running event loop raises immediately instead of silently stalling that loop —
use `AsyncDaytona` there. `d.v0` keeps the 0.x SDK's own behaviour: its sync
calls run (and block) wherever they are made, as they always did; from async
code use `AsyncDaytona().v0`.
A client created before `fork()` must not be used in the child; construct a new
one.

The root owns its namespace clients: close the root (`with Daytona(...)` or
`root.close()`), not `root.v0` / `root.v2` directly; a namespace client closed
on its own stays cached and fails on later calls. Closing is optional.
`with Daytona(...)` or `close()` releases the v2 client's
HTTP pools and stops its loop thread right away, and closes the v0 client's
event socket (and its OpenTelemetry provider, with `otel_enabled`); a client you never close is shut down the
same way at interpreter exit, so a short script can simply fall off the end.
Close explicitly in long-running processes that create many clients, or when you
need the connections gone now. Waits share one WebSocket per
regional API endpoint among clients with matching connection settings and events configuration
on the same event loop; closing the last of them closes it.

### AsyncDaytona

`AsyncDaytona` is the same SDK with coroutines instead of blocking calls. It
returns `AsyncSandbox`, `AsyncProcessHandle`, and so on — the async twin of
every type `Daytona` returns.

Fan out the way an orchestrator does — every handle is cheap and shares the
client's connection pool:

```python
import asyncio

from daytona import AsyncDaytona, DaytonaConfig


async def main() -> None:
    async with AsyncDaytona(DaytonaConfig(api_key="dtn_...")) as d:
        sandboxes = await asyncio.gather(*(d.v2.create("worker-snapshot") for _ in range(100)))
        results = await asyncio.gather(*(sb.exec("python train_step.py") for sb in sandboxes))


asyncio.run(main())
```

`async with` releases the client's connection pools when the block ends.

`DaytonaConfig` reads `DAYTONA_API_KEY` and `DAYTONA_JWT_TOKEN` from the
environment when the fields are omitted. Both credentials serve `d.v0` and
`d.v2`. Additional env vars:

- `DAYTONA_ORGANIZATION_ID` — scopes a JWT for the v0 namespace.
- `DAYTONA_PROJECT_ID` — scopes a JWT for the v2 namespace.
- `DAYTONA_API_URL` — overrides the v2 API endpoint.
  When unset, the SDK connects to the Daytona API. `d.v0` always uses
  its own production URL and ignores `DAYTONA_API_URL`. Migrating from the
  0.x SDK, which documented `DAYTONA_API_URL` as its own API URL: unset it,
  or `d.v2` sends requests to that (v0) host.
- Credentials: `d.v2` reads `DAYTONA_*` variables from the process
  environment only. `d.v0` keeps the 0.x SDK's behaviour of also reading
  `.env.local` / `.env`, so a key kept only in a `.env` file reaches `d.v0` but
  not `d.v2`; export it, or pass `api_key=` in `DaytonaConfig`.
- `DAYTONA_OTEL_ENABLED` — enable client spans when `daytona[otel]` is
  installed.

Set `request_timeout=` **(seconds)** to bound individual HTTP calls,
`connection_pool_maxsize=` to cap both live and keep-alive HTTP connections,
and `otel_enabled=True` to emit client spans when `daytona[otel]` is installed.
The extra brings only `opentelemetry-api`; spans are exported only once your
application configures an OpenTelemetry SDK tracer provider (and exporter) —
the SDK never installs one itself.

Use **exactly one** authentication mode:

- `api_key="dtn_..."` for a project API key.
- `jwt_token="...", project_id="..."` to exchange a signed-in user's JWT for a
  short-lived project-scoped platform token automatically.

The `target` field on `DaytonaConfig` is deprecated and applies only to
`d.v0`: pass `target=` to each v0 sandbox create
(`CreateSandboxFromSnapshotParams(target="eu")`) or `region_id=` to each v0
snapshot create instead. For v2, pass `region=` to each create.

### v0 API

`d.v0` gives access to the previous Daytona API through the 0.x Daytona SDK,
vendored into this package at a pinned release. It requires the `v0-sdk` extra —
accessing it without the extra raises `ImportError` naming the install command.
A program that only uses `d.v2` never imports v0 code.

```bash
pip install 'daytona[v0-sdk]==X.Y.ZaN'
```

```python
from daytona import Daytona, DaytonaConfig
from daytona.v0 import Sandbox  # resource types live in the namespace

with Daytona(DaytonaConfig(api_key="dtn_...")) as d:
    sandboxes = d.v0.list()
```

The root client is the only way to build a v0 client: `daytona.v0` exports the
resource types and v0's own errors, but not `Daytona`, `AsyncDaytona` or
`DaytonaConfig`. `from daytona.v0 import Daytona` raises an `ImportError` that
names the root form.

Names that exist in both namespaces are also exported with a version suffix, so
a single file can use both without renaming:

```python
from daytona.v0 import Sandbox as SandboxV0  # or import SandboxV0 directly
from daytona.v2 import Sandbox as SandboxV2  # or import SandboxV2 directly
```

**Migrating from v0:**

| v0 code | SDK 2.x |
|---|---|
| `pip install daytona` | `pip install "daytona[v0-sdk]"` |
| `daytona.create(...)` | `daytona.v0.create(...)` |
| `from daytona import Sandbox` | `from daytona.v0 import Sandbox` |
| `from daytona import Daytona` then `Daytona()` | `from daytona import Daytona` then `Daytona().v0` (not `daytona.v0.Daytona`, which SDK 2.x does not export) |
| `DaytonaConfig(api_url=...)` / `DAYTONA_API_URL` for a custom API URL | stay on SDK 0.x: SDK 2.x targets Daytona Cloud, and the root's `.v0` ignores `DAYTONA_API_URL` |

### Creating without an image

`d.v2.create()` with no image boots the platform default image, a Linux image
Daytona publishes for every project. `d.v2.images.list(platform="true")`
lists the platform images; the default one has `default=True`. A platform image
can also be passed by name, for example `d.v2.create("daytona-base")`. When no
default is configured, the create raises `DEFAULT_IMAGE_NOT_CONFIGURED` (400).

### Regions and safe create retries

`d.v2.create()` accepts an optional `region=` argument that creates the sandbox
in a region from `GET /regions`. Each entry includes
`id`, `name`, `available`, and optionally `api_base_url`.
Omitting `region` falls back to the project's default, then the
organization's, then `earth`, which means anywhere and may land in any
geography. Every `d.v2.create*()` method accepts `region=`, including
`create_from_snapshot` and `create_from_build`.

```python
d.v2.images.pull("python:3.12", name="py312", region="eu")
sandbox = d.v2.create("py312", region="eu", wait_seconds=60)
```

A known region that cannot currently accept sandbox creates raises
`REGION_UNAVAILABLE` (409). Image creates need a narrower region than `earth`
or a broad area; the API rejects a too-broad image region with 400.

Every `create*()` sends an idempotency key: the one you pass as
`idempotency_key`, else one derived from `name` (next section), else a random
one per invocation. If the create request times out or loses its connection,
`SandboxCreateRequestTimeoutError` / `SandboxCreateConnectionError` exposes
`idempotency_key`. Retry with that key and **the identical source and create
arguments**; do not start a fresh-key create to recover a lost response. A
changed request under the same key is a 409, never a silent replay of the
original parameters. Keys are scoped to the project and the API
endpoint that served the create, not checked across regions;
changing regions requires a new operation and key. Deleting a sandbox releases
its key at delete intent, so the same key (or the same name) creates a fresh
sandbox afterwards.

### Names, get-or-create and `find_by_name()`

Sandbox names are **not unique** — they are display metadata — but a named
create is also the handle of the one-sandbox-per-end-user pattern. Every
`create*(name=…)`:

- stamps the name as a label, `labels["name"] == name`, next to any labels you
  pass (an explicit `labels["name"]` that disagrees with `name=` is rejected
  before any request);
- derives its idempotency key from the name unless you pass `idempotency_key`:
  `idempotency_key_for_name(name)` is `"name:" + name` when that is a key the
  API accepts verbatim (printable ASCII, no surrounding whitespace, ≤ 256
  characters in total), else `"name:sha256:" + hex(sha256(name))`. The
  derivation is identical in the TypeScript SDK, so a retry can use either SDK
  without creating a second sandbox.

A repeated `create("py312", name="example-user")` is therefore a **get-or-create**: the
same sandbox comes back (its `access_token` only on the first call), not a
second sandbox and not a 409 — v2 has no "409 on duplicate name". The exact
semantics, all server-side:

- **Region-scoped.** Reusing a key with a different regional API endpoint can
  create a second sandbox; use the same `region` and API endpoint for retries.
- **Exact.** `create("py312", name="example-user", cpu=2)` followed by
  `create("py312", name="example-user", cpu=8)` is a 409, not a silent replay of the
  2-CPU sandbox. Change the arguments and you need a new name or an explicit
  key.
- **Released on delete.** Delete the sandbox and `create(..., name="example-user")`
  makes a new one.

`find_by_name(name, region=…)` returns the sandboxes carrying that exact `name`
label, newest first, as a list (names are not unique); `deleted`/`deleting`
ones are left out. It reads sandbox search with an exact `name` label match,
in the region a create with the same `region` lands in (`region` defaults to
none, i.e., the server's default, exactly as on `create*()`). A new sandbox
appears in search shortly after its create, so a lookup right after a
create can miss it — the get-or-create below stays safe, because the repeat
create derives the same key and returns the same sandbox. A deployment
without search raises `SearchNotConfiguredError` (501). Together:

```python
existing = d.v2.find_by_name("example-user")
sandbox = existing[0] if existing else d.v2.create("py312", name="example-user")
# Two callers racing on an empty result still get ONE sandbox: both creates
# carry the key "name:example-user", and the second is a replay of the first.
```

On `AsyncDaytona` the same pattern uses `await`:

```python
existing = await d.v2.find_by_name("example-user")
sandbox = existing[0] if existing else await d.v2.create("py312", name="example-user")
```

## What you get

### Follow build logs

Image submission still returns immediately; its existing `on_logs` callback
reports the submitted operation, not build output. Follow that build explicitly:

```python
from daytona.v2 import BuildLogInterruptedError, ImageBuilder

image = d.v2.images.build(ImageBuilder.from_dockerfile("FROM python:3.12\nRUN python -V\n"), name="my-worker")
try:
    result = d.v2.images.follow_build_logs(
        image.id,
        on_logs=lambda chunk: print(chunk, end="", flush=True),
        timeout=600,  # seconds; zero/None adds no observation deadline
    )
    print(result.status, result.reason, result.output_complete)
except BuildLogInterruptedError as error:
    print("Observation interrupted; last checkpoint:", error.cursor)

# For an existing sandbox's build:
result = d.v2.get("sandbox-id").follow_build_logs(
    on_logs=lambda chunk: print(chunk, end="", flush=True),
)
```

`on_logs` is required. Optional `on_event` receives a typed `BuildLogEvent`
union (`event.type` selects the variant), including states, gaps and source
unavailability. Without it, these diagnostics appear as `[build logs: …]`
messages in `on_logs`. Either callback may be a plain function or return an
awaitable, which is awaited before the next event (on `AsyncDaytona`). Build
failure is a result, not a connection error. `output_complete` describes the
requested cursor-to-end interval; gaps keep it false even after reconnection.
`sandbox.build_logs()` remains a one-shot string of currently retained output,
using the same discovered URL as the follower (`await sandbox.build_logs()`
on `AsyncDaytona`). One-shot reads reject responses exceeding 2 MiB of
received body bytes with `BUILD_LOG_PROTOCOL_ERROR`, rather than silently
truncating them.

A deadline in a state event is server execution metadata, not a client abort:
the follower still drains final output and waits for `done` with reason
`deadline`. Your `timeout` remains an independent observation limit.
`request_timeout` bounds discovery, one-shot reads, and error-response bodies,
including after headers arrive; it does not limit a healthy live log stream.
Each live stream uses a connection of its own, so a long observation never
holds up the client's other requests.

Pass `cursor=...` to resume a saved checkpoint. Automatic reconnects preserve
partial UTF-8 characters; a fresh follower starting mid-character can emit a
replacement character. Persist consumed output and its event cursor together
if you need application-level restart safety. Callback exceptions stop without
retry, since their side effects may already have happened. A timeout,
cancellation or keyboard interrupt closes the observation, not the build.
Source changes never restart from zero automatically. Unavailable output is
reported as an `unavailable` event; queued builds continue to be observed
until completion or a deadline.

Cancelling the task that awaits `follow_build_logs` propagates as ordinary
`asyncio.CancelledError`, so timeouts and task groups keep working. On
`Daytona`, `sandbox.follow_build_logs(...)` and
`d.v2.images.follow_build_logs(...)` block until the build finishes, and a
keyboard interrupt is surfaced as `BuildLogInterruptedError` with
`code="BUILD_LOG_CANCELED"` so the last checkpoint remains available.

### Surface overview

- **`Daytona` client** (sync) and **`AsyncDaytona`** (async) — sandbox
  lifecycle through `d.v2`: `create`, `get`, `list`, `iter`, `start`,
  `stop`, `pause`, `resume`, `delete`. Both expose the same
  methods; the examples above use the sync form.
- **Sandbox search** — `d.v2.search(...)` over the eventually consistent,
  all-region search API (`GET /sandboxes/search`), and `d.v2.list(...)` with
  any search filter (`status`, `sandbox_class`, `region`, `labels`,
  resource/timestamp bounds, `sort`, `order`, ...) uses the same search API.
  Timestamp filters are `created_start_at` / `created_end_at`,
  `updated_start_at` / `updated_end_at`, and `last_activity_start_at` /
  `last_activity_end_at`; starts are inclusive and ends are exclusive. Plain
  `d.v2.list()` stays on the read-your-writes `GET /sandboxes` path.
- **`Sandbox`** (sync) / **`AsyncSandbox`** (async) — serializable handle with
  `fork()`, `wait_for_state()` and the `process`, `fs`, `git`, `interpreter`,
  `pty`, `ports`, `volumes`, `lsp`, `docker` and `computer_use` namespaces.
  Sandbox handles borrow the client's shared transport; they do not need
  closing.
- **Network policy** (Linux and macOS sandboxes; the API rejects unsupported
  sandbox configurations) — create-time `network_policy` on
  `create` and `create_macos`, plus
  `get_network_policy()`,
  `update_network_policy(network_policy=..., if_match=...)`, and
  `reset_network_policy(if_match=...)` over `GET|PUT /sandboxes/{id}/network-policy`.
  Responses expose desired vs applied state, the latest quoted ETag, pending or
  failed apply state, and the `applied_start_id` of the sandbox start that last
  applied the policy. Writes are full
  replacement only; reset is an explicit JSON `null`; the SDK does not auto-GET
  or auto-retry around stale `If-Match`. Domain allow-lists imply default-deny:
  omitting `block_all` while setting `allow_domains` lets the API normalize it
  to `true`, while an explicit `block_all=False` is rejected locally with
  `NETWORK_POLICY_DOMAINS_REQUIRE_BLOCK_ALL`. `block_all=True` with
  `allow_cidrs` and no `allow_domains` is the CIDR-only mode, which is **not a
  DNS exfiltration boundary**: DNS A lookups for any name are still sent to
  DNS resolvers and only answers inside `allow_cidrs` come back, so a sandbox can
  carry data out in the names it looks up. When lookups must be contained, set
  `allow_domains` — `allow_domains=[]` resolves no tenant name (a domain list,
  even an empty one, also denies TCP 80/443 to `allow_cidrs` unless the name is
  listed); `block_all=True` with no allow list refuses every lookup except
  platform-managed names.
- **Runtime-managed sandbox secrets** (supported Linux sandbox configurations)
  — `sandbox.get_secrets()`, `sandbox.replace_secrets(secrets=[...], if_match=...)`,
  and root-client `d.v2.get_sandbox_secrets(...)` /
  `d.v2.replace_sandbox_secrets(...)` over `GET|PUT /sandboxes/{id}/secrets`.
  Responses expose desired secret names, desired/applied revisions, the quoted
  ETag, the apply `status` (`pending`/`applied`/`failed`) with its `error`, and
  capture metadata. Writes are full replacement only,
  use caller-supplied `If-Match`, and never send or return secret values.
- **Platform namespaces** — `d.v2.images`, `d.v2.snapshots`, `d.v2.volume`,
  `d.v2.secret`, `d.v2.reservations`, `d.v2.mac_models`.

### Sandbox metadata and helpers

Every fetched sandbox exposes its metadata:
`labels`, `cpu` / `cpu_max` (cores; 1 core = 2 vCPUs; a macOS core is one Mac CPU), `memory` / `memory_max`,
`disk` / `disk_max` (GiB), `sandbox_class`, `region`,
`persistent`, `spot`, `spot_evicted_at`,
`lifecycle_policy`, `forked_from`, `user`, and `error` (network policy is read through `get_network_policy()`).

Use `sandbox.set_labels(labels={...})` to replace the entire label set
(`{}` clears it), and `sandbox.entrypoint_logs()` to read the sandbox's
entrypoint-session log as plain text. On `AsyncDaytona` prefix these with
`await`.

Use `sandbox.update_env({...}, unset=(...))` to update the environment
inherited by processes, PTYs and sessions started afterwards. Running processes
keep their current environment, and `unset` removes only values previously set
through `update_env()`, not image-level environment entries.

### Volumes

Manage volumes with `d.v2.volume.list()`, `iter()`, `get()`, `create()` and
`delete()`. `get(name, create=True)` creates a missing volume; `id_only=True`
on `get()` or `delete()` disables name fallback. Deletion returns when the
API accepts the request, not when deletion finishes.

```python
from daytona.v2 import VolumeMount

volume = d.v2.volume.get("data", create=True)
sandbox = d.v2.create(
    "py312",
    volumes=[VolumeMount(volume.id, "/mnt/data", subpath="project")],
)
```

`create_from_build(..., volumes=[...])` accepts the same mounts. To mount an
S3 bucket, create a bucket volume in the dashboard or with `POST /volumes`,
then attach it through `volumes`. `VolumeMount(..., read_only=True)` is
supported for bucket volumes; disk volumes reject it.

`create_macos(..., volumes=[...])` accepts disk volumes read-write. A macOS
sandbox with volumes cannot use `persistent=True`; bucket volumes and
read-only mounts raise `BadRequestError`.

### Pull an attached volume

```python
result = sandbox.volumes.pull(
    "volume-id",
    request_id="refresh-42",
    timeout=5,
)  # server wait in seconds, 0–30; default 30
```

On `AsyncDaytona` use `await sandbox.volumes.pull(...)`.

This is separate from the platform `d.v2.volume` management service.
IDs contain 1–128 ASCII letters, digits, underscores or hyphens. `PullVolumeResult`
has `request_id`, `status` (`pulled`, `unchanged`, `pending`, or `unknown`) and
nullable `manifest_id`; `raw_status` preserves the server's status string.
Fractional seconds round down to whole milliseconds, but any positive timeout
waits at least 1 ms. Zero timeout submits a new request, or returns a known
request's last outcome without retrying it; expiry does not cancel accepted
work. Retry a `pending` result, or a caught `ServiceUnavailableError` /
`RateLimitError`, with the **same request_id** and a timeout above zero. The
SDK never retries automatically or generates an identity. Rate-limit errors
expose `request_id` and `retry_after_ms` when supplied by the server.

Availability depends on server support: a server may return
`ServiceUnavailableError`. `unchanged` does not assert that local files
have no edits.

### Create sources

`d.v2.create()` is the fast path: pass a registered image name or an `img_…`
id and the SDK sends one `POST /sandboxes` request with your `wait_seconds` budget.

```python
d.v2.images.pull("python:3.12", name="py312")
linux = d.v2.create("py312", wait_seconds=60)
snapshot = d.v2.create_from_snapshot("worker-after-setup", wait_seconds=60)
```

`d.v2.create_from_build()` is the slow path: it builds an anonymous image
first, waits for that image to become ready, then creates the sandbox from the
image id. If the build fails, it raises `ImageBuildFailedError` and no sandbox
exists.

```python
from daytona.v2 import ImageBuilder

builder = ImageBuilder.from_registry("python:3.12").run_commands("pip install numpy")
sandbox = d.v2.create_from_build(builder, cpu=2, memory=4, wait_seconds=60)
```

`wait_seconds` and `create_timeout_seconds` are budgets. The server holds create for up to its own
short cap, then the SDK long-polls the remainder and returns the sandbox in
whatever state it reached. `wait_seconds=0` is the explicit async form.

### macOS sandboxes

A macOS sandbox runs on a dedicated Mac reserved by your organization. Create
one with `d.v2.create_macos(image, ...)` (`POST /sandboxes/macos`);
`create()` makes Linux sandboxes.
`image` is a macOS image name
such as `base-macos`. `cpu` (cores, at least 4), `memory` (GiB, at
least 4) and `disk` (GiB, at least 80: the whole sandbox disk, macOS included)
size the sandbox; each omitted value uses half of the Mac running the
sandbox, rounded down, on a model that runs two sandboxes, or all of it on a
model that runs one. The size is fixed when the sandbox is allocated and reported from
then on (`sandbox.memory` and `sandbox.disk` in exact GiB, `sandbox.reservation_id`
for the reservation). `name`, `idempotency_key`, `wait_seconds` and
`create_timeout_seconds` work as on `create()`: a named create derives its
idempotency key from the name and
stamps `labels["name"]`.

Creating a macOS sandbox, starting a stopped one, reserving a Mac or renewing a
reservation, and turning a reservation's auto-renew on require macOS sandbox
access for your organization: without it the API answers `403`
`FEATURE_NOT_ENABLED` (raised as `FeatureNotEnabledError`). Reading sandboxes
and reservations, turning auto-renew off, `pause()` and `resume()` do not require
this access.

Find available regions and their regional API endpoints with `GET /regions`.
Use `d.v2.mac_models.catalog(region="<region>")` or
`daytona mac-model list --region '<region>'` to discover available Mac models
and current prices. Replace `<region>` and `<model>` in the examples with
values from these catalogs. When using a regional API endpoint, set
`DAYTONA_V2_API_URL` before creating the client. Against an API that does not
serve the region, `create_macos` raises the API's `ConflictError`; the SDK
does not send the create anywhere else.

A sandbox without a size uses the model's default sandbox size:

```python
d.v2.create_macos("base-macos")
```

A reservation reserves one physical Mac of a named model. Set CPU, memory,
and disk sizes when creating each sandbox. `d.v2.mac_models.list()` lists each
model's resources, sandbox disk room, `max_sandboxes`, and `default_size`.
Omitted sizes use half of each resource on a model that runs two sandboxes,
or all of it on a model that runs one. Disk defaults round down to a whole GiB.
Reserve a Mac, then create a sandbox using the model's default size:

```python
reservation = d.v2.reservations.create(region="<region>", model="<model>")
d.v2.create_macos("base-macos", region="<region>", reservation=reservation.id, persistent=True)
```

Stop and pause need a persistent sandbox (`persistent=True`). Stop keeps the disk on the same Mac, and start and resume use that Mac. Delete erases the disk. At reservation end, a persistent sandbox's disk is saved as an anonymous image before the Mac is erased, and an ephemeral sandbox's disk is erased unsaved; a lost Mac saves persistent disks if it comes back within 7 days. If a Mac restarts before it is declared lost, a running persistent sandbox comes back stopped with its disk, a running ephemeral sandbox is lost, and stopped and paused sandboxes are unchanged. Create a sandbox from an image to continue on another Mac.

`reservation` picks the Mac reservation. Omitted, the sandbox goes on any reservation of your
organization in the region with room for its size, or, when none has room and
your organization's auto-reserve setting is on, the server buys a reservation of the
cheapest model that fits. `reservation="new"` always buys such a reservation for the
sandbox (`auto_renew` is accepted only here). A reservation ID targets that
reservation. Acceptance does not reserve room; the sandbox can wait for room
or time out. Reservations are billed per 24-hour period; without `model`,
`reservations.create()` reserves the region's default model and never upgrades
an unavailable model to another one.

`d.v2.mac_models.catalog(region=...)` (`GET /mac-catalog`) shows, per model,
how many Macs a purchase can reserve now (`available`, advisory) and the
`price` of one reservation (`None` when pricing is unavailable in the region).
`reservations.create_bulk()` reserves several Macs of one model in one
request: all of them, or none if fewer are free, unless `min_count` accepts
fewer. Every purchase and renewal carries an idempotency key; pass your own
so a retry after a lost response neither reserves more Macs nor pays for a
second period:

```python
catalog = d.v2.mac_models.catalog(region="<region>")
batch = d.v2.reservations.create_bulk(
    region="<region>", model="<model>", count=3, idempotency_key="nightly-macs"
)
print(f"reserved {batch.reserved} of {batch.requested} Macs")
d.v2.reservations.renew(reservation.id, idempotency_key=f"renew-{reservation.id}-2")
```

The dashboard's desktop view opens the Mac's screen through your authenticated
dashboard session. Click once in the desktop before typing, because macOS
drops keys for the first few seconds of each connection. Customers cannot end
a Mac reservation before its 24-hour period.

Without a reservation to use, a macOS create fails with `ConflictError` and
`code == "NO_ACTIVE_RESERVATION"` (or `NO_FREE_SLOT` when no reservation has room
for the size; `MAC_DISK_FULL` when the reservations with room have no free disk
space; `AUTO_RESERVE_LIMIT_REACHED` when auto-reserve holds its maximum number
of reservations; or `NO_MAC_AVAILABLE` when buying a reservation, with `reservation="new"`
or through auto-reserve, finds no fitting Mac in the region). `MAC_DISK_FULL`
never buys a reservation. Starting a stopped macOS sandbox uses its own disk on the
same Mac and reservation; it never buys a reservation. Reservations report `model`,
`capacity`, `resources_used`, `origin` (`explicit` or `auto`), `auto_renew`
and `renewal_error`; `reservations.update(id, auto_renew=...)` changes renewal
on a live reservation.

`pause()` and `resume()` work on persistent macOS sandboxes. `pause()` saves
the sandbox's memory on its Mac and waits for `paused`; `resume()` puts the
sandbox in `assigned` and waits for `started`. A paused sandbox keeps its Mac
slot, but pausing stops its reservation from auto-renewing: a reservation that
holds only paused sandboxes lapses at the end of its period. After a resume,
`sandbox.mac_boot_mode` reports how the sandbox resumed; `error_reason`
provides error details when available.

A macOS `fork()` copies the disk only: omit `memory` or pass `memory=False`
(`memory=True` answers `NOT_SUPPORTED_FOR_CLASS`). A fork of a running macOS
sandbox boots with a fresh machine identity; a fork of a paused or stopped
one rests `stopped`. A fork needs available capacity for its size; insufficient
capacity can answer `NO_FREE_SLOT` or `MAC_DISK_FULL`. `fork(timeout=…)` waits until
the macOS fork is `started` or `stopped`.

### Processes

`sandbox.exec()` is the root alias of `process.run()`. `process.run()` executes
a command and awaits the result. `process.start()` returns a detached,
serializable `ProcessHandle(sandbox_id, process_id)` with `logs()`,
`stream_logs()`, `wait()`, `write_stdin()`, `close_stdin()`, signal-aware `kill()`,
`delete()` and `resize()`. `write_stdin()` / `close_stdin()` are for exec and
PTY handles only. `timeout=` is the caller wait budget **(seconds)**, and
`timeout=0` means wait unbounded. `execution_timeout=` is the process deadline
**(seconds)** for server-managed runs that support one. Rehydrated `kind='code'`
handles remain replay/control handles (`logs()` / `wait()` / `kill()`), not a
way to recover a parsed `InterpreterResult` on another worker. `pty.list()` is
the PTY-only view of `process.list(kind="pty")`, and `pty.delete(id)` kills
then cleans up that PTY record.

On `AsyncDaytona`, `process.start()` returns `AsyncProcessHandle` and methods
that return results require `await`.

### Sessions

Create a `Session` for one persistent shell. Commands run one at a time, in
order; `cd`, exports and functions carry over. Set `cwd` and `env` at creation,
not on individual commands. The context manager deletes the session and its
commands on exit:

```python
with sandbox.process.create_session(cwd="/app", env={"CI": "1"}) as session:
    session.run("export BUILD_MODE=test")
    result = session.run("npm test", execution_timeout=600)
    command = session.start("npm run dev")
    command.kill(signal="INT")  # Stops this command; the session survives.
    print(session.get().cwd)  # The shell's current directory.
```

With `AsyncDaytona`, use `await sandbox.process.create_session()`, `async with`,
and await `AsyncSession` network methods. `timeout` bounds the caller's wait;
`execution_timeout` stops the command without ending the session (both seconds);
a command that has not stopped 5 seconds after its timeout ends the session
instead of blocking it.
Use `session.to_json()` and `sandbox.process.connect_session(identity)` to
reconnect without a request. `process.list(kind="session")` lists sessions;
`process.list(session_id=session.id)` lists their commands. Sessions survive
pause/resume but not a sandbox stop/start. In dash, BusyBox `sh` and mksh, an interrupt leaves only the
innermost shell function or sourced file; bash and zsh leave the whole command
(bash older than 4.4, macOS's `/bin/bash`, only when no DEBUG trap is set).

### Tags

Group independently started processes with an optional `tag`, a safe identifier
of 1–128 characters using letters, digits, `.`, `_` and `-` (not `.` or `..`):

```python
await sandbox.process.start("python -m api_server", tag="job-123")
await sandbox.process.start(argv=["node", "worker.js"], tag="job-123")
shell = await sandbox.process.create_session(cwd="/app", tag="job-123")
await shell.run("nohup ./watcher.sh &")  # Commands inherit the session's tag.

await sandbox.process.list(tag="job-123")
killed = await sandbox.process.kill_all(tag="job-123")
# Returns once everything the kill covered has stopped. The tag can be reused.

handle = await sandbox.process.start("./start-server.sh")  # Backgrounds a server, exits.
await handle.wait()
await handle.kill()  # Linux: also stops the server it left running.
```

These examples use `AsyncDaytona`; omit `await` with `Daytona`. Tags also work
with `process.run()`, `sandbox.exec()` and `pty.start()`. A tag is immutable;
session commands inherit it, and interpreter code executions cannot be tagged.
`ProcessInfo.tag` exposes it. `kill_all()` keeps records and logs and returns
the ids of the tagged processes that were running, or had running descendants,
when it began; survivors of deleted or expired records are killed too but not
listed. Its `timeout` is in seconds (up to 60, fractions allowed, default 10); `ProcessKillIncompleteError` means the timeout expired before all
work stopped, and retrying is safe. A start racing the kill either precedes it
and is killed, or starts afterwards and keeps running.

Linux and Windows support tags, filtering and tag-wide kills. macOS supports
tags and filtering only: `kill_all()` raises `UnsupportedOperationError`, as it
does on sandboxes without process containment. On Linux with containment,
`kill()` also stops detached descendants (`setsid`, `nohup`, double fork) and
survivors of an exited process; macOS `kill()` is unchanged. Tags can be reused
immediately after `kill_all()`.

### Files

`read_text` / `write_text`, `read_bytes` / `write_bytes`, `upload_file()` /
`download_file()`, bulk `read_many` / `write_many`, `remove()`, `grep()` (file
contents) / `glob()` (file names), `watch_dir()` for directory
watches (`get_new_events()`, a single exit notification via `on_exit=` /
`wait_for_exit()`), and
`presigned_download_url` / `presigned_upload_url` for direct transfers. The
platform mints the link; `expires_in_seconds` (default 3600) must be between 1
and `SIGNED_FILE_MAX_TTL_SECONDS` (7 days) — links always expire.
`sandbox.rotate_signing_key()` retires every pre-signed URL minted so far;
new URLs use the new key, which is never
returned to the client. List-like APIs return `{items, next_cursor}` objects;
`iter()` lazily drains every page.

`upload_file()` streams a local file in chunks, reports `on_progress(bytes,
total)`, accepts `cancel=` via any event-like object with `is_set()`, and can
set `atomic=True` to write through a temp file + rename. `download_file()`
streams to a unique temp file in the destination directory, renames it on
success, reports progress with `Content-Length` when the server sends it, and
removes that temp file on error or cancellation. `write_bytes()`,
`write_text()`, and `write_many()` also accept `atomic=True`. Only with
`atomic=True`, on a filesystem that cannot rename atomically, do
`write_bytes()` / `write_text()` / `upload_file()` raise
`AtomicUploadUnsupportedError`; `write_many(atomic=True)` keeps its
partial-success contract and reports those entries in the returned `errors`
(the failure message names the file and the atomic-rename limitation).

### Interpreter

`sandbox.run_code()` is the root alias of `interpreter.run()`. Runs without an
explicit context share one per-language interpreter state inside the sandbox,
and that state's `env` is fixed when it is first created. Pass `isolated=True`
for a throwaway run or use `create_context()` / `connect_context()` /
`list_contexts()` for an independent named context. If an explicit named
context vanished, `interpreter.run(..., context=...)` / `context.run()` surface
the typed `SessionNotFoundError`; recreate the context yourself and decide what
to rerun. `timeout=` is enforced client-side **(seconds)**: on expiry the kernel
is interrupted. Results are split into `results[]`, `logs{stdout,stderr}`,
`error`, and `process`. That rich result shape comes from the interpreter run
path itself; reconnecting a serialized code-process handle later does not
recreate it.

### Git

`clone()`, `pull()` and `push()` use a dedicated **30-minute** remote-operation
deadline by default, and each accepts a positive per-call `timeout=` override
**(seconds)**.
`clone()` also accepts `depth=` for shallow clones and
`insecure_skip_tls=True` for one clone operation. `pull()` and `push()` accept
`branch=` and `remote=`; `push()` also accepts `set_upstream=True`.
`commit(path, message, *, author=..., email=...)` keeps the identity fields
keyword-only. Pass `allow_empty=True` to create an empty commit when Git would
otherwise refuse it.

### Ports, LSP, computer use

`ports` owns both listener discovery and preview exposure:
`list()`, `is_in_use(port)`, `expose(port, public=...)`,
`set_public(port, public=...)`, `preview_link(port)`,
`signed_preview_link(port, expires_in_seconds=...)`,
`expire_signed_preview_link(port, token)`, paginated `list_endpoints(...)`, and
`iter_endpoints(...)`.
To skip the preview warning page when a headless browser opens a preview URL,
send `{PREVIEW_WARNING_SKIP_HEADER: PREVIEW_WARNING_SKIP_HEADER_VALUE}` (both
exported from `daytona.v2`) with its requests.
`lsp.start(language_id, path_to_project)` returns an `LspServer` handle with
`open_document(uri)`, `close_document(uri)`, `completions(uri, position)`,
`document_symbols(uri)`, `workspace_symbols(query)` and `stop()`.
`computer_use` stays lifecycle-flat (`status()`, `start()`, `stop()`) and
splits actions into `service`, `mouse`, `keyboard`, `screen`, `display`,
`a11y` and `recording`. `screen.screenshot()` captures the whole screen or a
`region=ComputerUseRegion(...)`; any of `format=`, `quality=` or `scale=`
returns a compressed image.

### Docker

Docker Engine starts automatically in sandboxes whose image ships it; there
is no create option. `sandbox.docker.status()` returns its state (`ready`,
`starting`, `failed`, `not_present`, `disabled`, `unmanaged`, `deferred` or
`unsupported`), who owns it, a `reason`, and the `process_id` whose logs hold
Docker's output. `sandbox.docker.wait_until_ready(timeout=60)` polls until
the state is `ready` and returns the status. It raises `DockerNotReadyError`
(`error.status`) as soon as Docker settles in a state that cannot become
`ready` (`failed`, `not_present`, `disabled`, `unmanaged`, or `unsupported`), and
`DockerWaitTimeoutError` when the budget **(seconds)** runs out while Docker
is still `starting` or `deferred`, or a status request is still unanswered at
the deadline. `timeout=0` waits without a deadline. A Docker failure never
makes the sandbox itself unusable.

Set `DAYTONA_DOCKER_START` in the sandbox env to choose how Docker starts:
`auto` (default; start the image's `dockerd` when it ships one), `entrypoint`
(the image's own entrypoint starts Docker) or
`off` (Docker is not started; state `disabled`). If Docker status is unavailable,
`status()` and `wait_until_ready()` raise `NotFoundError` (404).

Some environment variable names are reserved for the platform, among them
every `DAYTONA_*` name other than `DAYTONA_SANDBOX_TOKEN`,
`DAYTONA_API_URL`, `DAYTONA_OTEL_ENDPOINT` and `DAYTONA_DOCKER_START`. A
sandbox `env` that sets a reserved name fails the create with `BadRequestError`
(400) naming the key; the same rule applies to the keys an
`update_env()` call sets or unsets.

### Waiting for lifecycle states

Use `sandbox.wait_for_state(states, terminal_states=('destroying', 'destroyed', 'error'), *, timeout=None)`
to wait for a public sandbox state. `timeout` is the caller wait budget in
seconds; `None` and `0` wait unbounded. If the sandbox reaches a terminal state
before any target state, the SDK raises a typed conflict error with the
observed public state attached as `error.state`. When the server explains
that state, the explanation is `error.reason` and ends the error message.

`sandbox.wait(until='started', timeout=60)` is the one-state convenience form:
it returns the sandbox on success, raises `RequestTimeoutError` when the budget
runs out, and raises `SandboxTerminalStateError` when a terminal state wins
first. `timeout=None` and `timeout=0` wait unbounded.

### Sandbox deletion is asynchronous

`sandbox.delete()` and `d.v2.delete(sandbox)` request deletion and refresh
the sandbox once; they do not wait for teardown to finish. The handle can
still report `destroying` when the call returns.

`sandbox.wait_for_state(['destroyed'], timeout=600)` waits for the observed
lifecycle state. A sandbox with volumes attached stays `destroying` until the
platform has finished with its volumes: usually seconds, but minutes when a
lot of volume data is written at the end. If that takes too long, the sandbox
reports `error` and the wait raises `SandboxTerminalStateError`; when the
server explains why the deletion did not finish, that is its `reason`. Such a sandbox can still reach
`destroyed` later. To keep waiting for it, catch the error, pause for a few
seconds and call `wait_for_state(['destroyed'])` again: while the sandbox
still reports `error`, the call raises again at once.

Deleting the image or snapshot immediately afterwards can still raise
`ConflictError` (409). A missing sandbox during a state wait raises
`NotFoundError`; only the delete method's follow-up refresh maps a 404 to
`destroyed`.

## Errors

The package root exports the error hierarchy both namespaces share:
`DaytonaError` and one class per category — `DaytonaBadRequestError`
(alias `DaytonaValidationError`), `DaytonaAuthenticationError`,
`DaytonaForbiddenError` (alias `DaytonaAuthorizationError`),
`DaytonaNotFoundError`, `DaytonaTimeoutError`, `DaytonaConflictError`,
`DaytonaGoneError`, `DaytonaUnprocessableEntityError`, `DaytonaRateLimitError`,
`DaytonaInternalServerError`, `DaytonaServiceUnavailableError` and
`DaytonaConnectionError`. They catch API errors from `d.v0` and `d.v2` alike,
category for category. One exception: `d.v0`'s transport failures (a refused
connection, a timeout) arrive as v0's plain `DaytonaError`, as in the 0.x SDK,
so catch them with `DaytonaError`, not `DaytonaConnectionError` /
`DaytonaTimeoutError`:

```python
import daytona

try:
    d.v2.get("missing")
except daytona.DaytonaNotFoundError as e:  # also catches a v0 not-found error
    print(e.status_code, e.code)
```

The v2 classes (`from daytona.v2 import NotFoundError, …`) expose `status_code`,
`code`, `source`, and `trace_id`, and have code-specific subclasses
(`ProcessNotFoundError`, `SandboxTerminalStateError`, …) so you can catch as
narrowly as you need. Version-specific errors are imported from `daytona.v2`
(or `daytona.v0`).

A request refused because your organization has hit one of its limits (for
example the maximum number of concurrent sandboxes) raises
`LimitExceededError` — a `RateLimitError` that also carries the limit's name
and counts as `limit`, `current` and `max`. Other rate-limited requests keep
raising plain `RateLimitError`.

## Versioning and releases

See `docs/VERSIONING.md` and `CHANGELOG.md` in the
[repository](https://github.com/daytona/clients).
