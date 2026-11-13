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
    # create() returns the handle even if its wait ended before the sandbox started
    if sandbox.state != "started":
        sandbox.wait()
    result = sandbox.exec("python -V")
    print(result.exit_code, result.output)
    d.v2.delete(sandbox)
```

`images.pull()` waits until ready by default. Set `wait=False` to skip the
readiness wait, or `timeout=120` for a 120-second budget (`None`, `0` or a
value above about 24.8 days is unbounded). A failed import raises `ImageBuildFailedError` with the server's
error message. `images.build()` remains non-waiting. Both take the same image
options (`name`, `region`, `cpu_arch`, `idle_expiry_seconds`), and their
submission is an ordinary request, bounded by `request_timeout`.

Image names may not start with a resource id prefix (`img_`, `snap_`, `sbx_`,
`vol_`, `fn_`): such a name raises `ImageNameReservedError`. A named
`images.pull()` whose name already exists for different content or import
options (source, region, CPU architecture) raises `ImageNameTakenError` (409
`IMAGE_NAME_TAKEN`) and changes nothing; an identical one returns the existing
image (a failed one is imported again). Capturing or promoting to a name that
exists raises `ImageNameTakenError` too.

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
After closing, new requests on a stored v2 client or any of its handles raise
`ClientClosedError` (`CLIENT_CLOSED`, source `SDK`, no HTTP status).
Accessing `root.v2` after `root.close()` builds a fresh working client.
Configuration dataclasses, including `DaytonaConfig` and `SandboxEventsConfig`,
accept keyword arguments only. Error `status_code` must be an integer (not a
boolean) or `None`; invalid types raise `TypeError`.
A non-Cloud `DAYTONA_API_URL` emits a warning once per process: SDK 2.x ignores
this legacy variable. Use SDK 0.x for a custom API URL.
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
        sandboxes = await asyncio.gather(*(d.v2.create_from_snapshot("worker-snapshot") for _ in range(100)))
        # Each handle comes back even if its wait ended before the sandbox started:
        # wait for the rest, and keep going when some end in a terminal state.
        await asyncio.gather(
            *(sb.wait() for sb in sandboxes if sb.state != "started"), return_exceptions=True
        )
        for sb in sandboxes:
            if sb.state != "started":
                print(f"{sb.id} did not start: {sb.state} {sb.error or ''}")
        ready = [sb for sb in sandboxes if sb.state == "started"]
        results = await asyncio.gather(*(sb.exec("python train_step.py") for sb in ready))


asyncio.run(main())
```

`async with` releases the client's connection pools when the block ends.

`DaytonaConfig` reads `DAYTONA_API_KEY` and `DAYTONA_JWT_TOKEN` from the
environment when the fields are omitted. Both credentials serve `d.v0` and
`d.v2`. Environment values are trimmed, and a blank one counts as unset;
values you pass in `DaytonaConfig` are used as given, and an empty one
(`api_key=""`, …) raises `BadRequestError` instead of falling back to the
environment. `Daytona(config)` copies the config when it is built: changing
the object afterwards has no effect on the client. Additional env vars:

- `DAYTONA_ORGANIZATION_ID` — scopes a JWT for the v0 namespace.
- `DAYTONA_PROJECT_ID` — scopes a JWT for the v2 namespace.
- `DAYTONA_V2_API_URL` — selects another v2 API endpoint (a regional endpoint,
  as `daytona region list` in the CLI shows them, or a test environment). When unset, the SDK connects to the Daytona API. `d.v0` always
  uses its own production URL. SDK 2.x never routes using `DAYTONA_API_URL`, the 0.x
  SDK's variable.
- Credentials: `d.v2` reads `DAYTONA_*` variables from the process
  environment only. `d.v0` keeps the 0.x SDK's behaviour of also reading
  `.env.local` / `.env`, so a key kept only in a `.env` file reaches `d.v0` but
  not `d.v2`; export it, or pass `api_key=` in `DaytonaConfig`.
- `DAYTONA_OTEL_ENABLED` — enable client spans when `daytona[otel]` is
  installed.

Set `request_timeout=` **(seconds; default 60; `0`, or a value above 2,147,483.647 (about
24.8 days), means no limit)** to change
the total time one ordinary request may take before it raises
`ClientTimeoutError`. Waits and long polls use their own budget, streams are
bounded only until their response arrives, and file transfers have no total
limit but fail with `ClientTimeoutError` once no bytes move for that long
(with `request_timeout=0` they are not bounded at all).
Set `max_response_bytes=` **(an integer; `None` keeps the 1 GiB default, `0`
means no limit, and any other value is a `BadRequestError`)** to cap every
response body the SDK holds in memory whole: a larger body raises
`PayloadTooLargeError` before it is read in full (an error response keeps its
status's error).
Set `connection_pool_maxsize=` to cap both live and keep-alive HTTP connections,
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
| `DaytonaConfig(api_url=...)` / `DAYTONA_API_URL` for a custom API URL | stay on SDK 0.x: SDK 2.x targets Daytona Cloud and never routes using `DAYTONA_API_URL` |

### Creating without an image

`d.v2.create()` with no image boots the platform default image, a Linux image
Daytona publishes for every project. `d.v2.images.list(platform="true")`
lists the platform images; the default one has `default=True`. A platform image
can also be passed by name, for example `d.v2.create("daytona-base")`. When no
default is configured, the create raises `DEFAULT_IMAGE_NOT_CONFIGURED` (400).

### Regions and safe create retries

`d.v2.create()` accepts an optional `region=` argument that creates the sandbox
in a region listed by `daytona region list` (with `-o json`, each entry
includes `id`, `name`, `available`, and optionally `api_base_url`).
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
  before any request, and so is a blank name: omit `name` for a generated one);
- derives a stable idempotency key from the name unless you pass
  `idempotency_key`. The derivation is identical in the TypeScript SDK, so a
  retry can use either SDK without creating a second sandbox.

A repeated `create("py312", name="example-user")` is therefore a **get-or-create**: the
same sandbox comes back (its `access_token` only on the first call), not a
second sandbox and not a 409 — v2 has no "409 on duplicate name". This is
permanent behavior of `name=`. The exact semantics, all server-side:

- **Region-scoped.** Reusing a key with a different regional API endpoint can
  create a second sandbox; use the same `region` and API endpoint for retries.
- **Exact.** `create("py312", name="example-user", resources=Resources(cpu=2))` followed by
  `create("py312", name="example-user", resources=Resources(cpu=8))` is a 409, not a silent replay of the
  2-CPU sandbox. Change the arguments and you need a new name or an explicit
  key.
- **Released on delete.** Delete the sandbox and `create(..., name="example-user")`
  makes a new one.

`find_by_name(name, region=…)` returns **all** sandboxes carrying that exact `name`
label, newest first, as a tuple (names are not unique); `destroyed`/`destroying`
ones are left out. It is the one list method that is not a single page. It reads sandbox search with an exact `name` label match,
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
# use the same name and arguments, so the second is a replay of the first.
```

On `AsyncDaytona` the same pattern uses `await`:

```python
existing = await d.v2.find_by_name("example-user")
sandbox = existing[0] if existing else await d.v2.create("py312", name="example-user")
```

## What you get

### Follow build logs

`images.build()` returns the submitted `Image` without waiting for the build.
Follow its output with `images.follow_build_logs(image.id, on_logs=...)`, or
just wait for the result with `images.get(image.id, wait="ready", timeout=...)`:

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
until completion or a deadline. An event kind a later API adds is skipped
(it does not move the cursor). An HTTP error of the first request, such as
`NotFoundError` for an unknown image, is raised as that typed error rather
than as `BuildLogInterruptedError`.

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
- **Sandbox search** — `d.v2.list(cursor=, limit=)` returns one page
  without filters, and `d.v2.iter(cursor=, limit=)` walks those pages. Where
  the deployment serves search, lists (sandboxes, images and snapshots) read
  it and are eventually consistent: an item just created may be
  missing, and `get()` reads it at once. They fall back to the plain list routes only
  where search is not served (`501 SEARCH_NOT_CONFIGURED`, or `404` from an
  older API); a `503 SEARCH_UNAVAILABLE` is raised, never a fallback. `d.v2.search(...)` queries
  the eventually consistent, all-region search API (`GET /sandboxes/search`)
  and carries every filter: `states`, `sandbox_class`, `region`, `labels`,
  `name_prefix` / `id_prefix`, resource/timestamp bounds, `sort`, `order`, ...
  `states` takes the SDK's sandbox states (`states=["started", "paused"]`);
  `"unknown"` is not searchable. `region` takes region IDs; a region that
  contains other regions (a geo group, or `earth`) matches every region inside
  it, and an unknown region is a `BadRequestError`. Timestamp filters are `created_start_at` /
  `created_end_at`, `updated_start_at` / `updated_end_at`, and
  `last_activity_start_at` / `last_activity_end_at`; starts are inclusive and
  ends are exclusive.
- **`Sandbox`** (sync) / **`AsyncSandbox`** (async) — a handle with
  `fork()`, `wait()` and the `process`, `fs`, `git`, `interpreter`,
  `pty`, `ports`, `volumes`, `docker` and `computer_use` namespaces.
  Sandbox handles borrow the client's shared transport; they do not need
  closing.
- **Network policy** (Linux and macOS sandboxes; the API rejects unsupported
  sandbox configurations) — create-time `network_policy` on
  `create` and `create_macos`, plus
  `sandbox.get_network_policy()`,
  `sandbox.update_network_policy(network_policy=..., if_match=...)`, and
  `sandbox.reset_network_policy(if_match=...)` over `GET|PUT /sandboxes/{id}/network-policy`.
  Responses expose desired vs applied state, the latest quoted ETag, pending or
  failed apply state, and the `applied_start_id` of the sandbox start that last
  applied the policy. Writes are full
  replacement only; reset is an explicit JSON `null`; the SDK does not auto-GET
  or auto-retry around stale `If-Match`. Domain allow-lists imply default-deny:
  omitting `block_all` while setting `allow_domains` lets the API normalize it
  to `true`, while an explicit `block_all=False` is rejected locally with
  `NETWORK_POLICY_DOMAINS_REQUIRE_BLOCK_ALL`. A non-empty `allow_cidrs` needs
  `block_all=True` or `allow_domains`: without them the policy is open and
  ignores `allow_cidrs`, so the SDK refuses it locally. `block_all=True` with
  `allow_cidrs` and no `allow_domains` is the CIDR-only mode, which is **not a
  DNS exfiltration boundary**: DNS A lookups for any name are still sent to
  DNS resolvers and only answers inside `allow_cidrs` come back, so a sandbox can
  carry data out in the names it looks up. When lookups must be contained, set
  `allow_domains` — `allow_domains=[]` resolves no tenant name (a domain list,
  even an empty one, also denies TCP 80/443 to `allow_cidrs` unless the name is
  listed); `block_all=True` with no allow list refuses every lookup except
  platform-managed names.
- **Runtime-managed sandbox secrets** (supported Linux sandbox configurations)
  — `sandbox.get_secrets()` and `sandbox.replace_secrets(secrets=[...], if_match=...)`
  over `GET|PUT /sandboxes/{id}/secrets`.
  Responses expose desired secret names, desired/applied revisions, the quoted
  ETag, the apply `status` (`pending`/`applied`/`failed`) with its `error`, and
  capture metadata. Writes are full replacement only,
  use caller-supplied `If-Match`, and never send or return secret values.
- **Platform namespaces** — `d.v2.images`, `d.v2.snapshots`, `d.v2.volumes`,
  `d.v2.secrets`, `d.v2.reservations`, `d.v2.mac_models`.

### Sandbox metadata and helpers

Every fetched sandbox exposes its metadata, read-only:
`labels`, `cpu` / `cpu_max` (cores; 1 core = 2 vCPUs; a macOS core is one Mac CPU), `memory` / `memory_max`,
`disk` / `disk_max` (GiB, exactly the API's MiB / 1024), `sandbox_class` / `raw_sandbox_class`, `region`,
`persistent`, `spot`, `spot_evicted_at`,
`lifecycle_policy`, `forked_from`, `user`, and `error` (network policy is read through `get_network_policy()`).
Update it with methods such as `set_labels()` and reload it with
`refresh_data()`. A handle from `create*()` or `fork()` whose wait did not read
the sandbox (`wait_seconds=0`, or a wait that ended early) holds only what the
create response carries: fields it does not report are `None` (unknown), with
`labels is None` rather than `{}`, until `refresh_data()` reads them; `state` is
`"unknown"` and `raw_state` is `None` before a status is known. A class this SDK does not know reads as
`sandbox_class == "unknown"` with the API's value in `raw_sandbox_class`.
`sandbox.access_token` is the sandbox-scoped token the handle authenticates
with (from the create response, a token you pass as
`d.v2.get(id, access_token=...)`, which the handle never replaces, or one it
minted); it is `None` until one is known and never serialized.

`sandbox.to_json()` returns a `SandboxHandleJSON` (exported from `daytona.v2`):
the handle's metadata as camelCase JSON, exactly what the TypeScript SDK's
`toJSON()` writes for the same sandbox, timestamps kept as the server's
strings, without credentials or URLs. It's a snapshot, not a way back:
reconnect with `d.v2.get(...)` using the sandbox id it carries.

Resource models are frozen, keyword-only dataclasses: for example, images
expose `sandbox_class`, `layer_count` and `available_in`; snapshots expose
`sandbox_class` / `raw_sandbox_class` and `memory_bytes`; volumes expose
`size_gib`, `used_bytes` and read-only `options`. An image's `source` is a
frozen `RegistryImageSource`, `DockerfileImageSource`, `NamedImageSource` or
`ManifestImageSource`, told apart by `source.type`; a source kind a later API
adds reads as `UnknownImageSource` (`type == "unknown"`, the wire type in
`raw_type`), never as an error. Closed SDK enums map
unrecognized values to `"unknown"` and keep the original in a `raw_…`
sibling. Timestamps are timezone-aware `datetime` values; a budget period
start is a `date`. Collection fields on result dataclasses are tuples,
including `items` on list, search and log pages; mapping fields remain plain dicts.

Use `sandbox.set_labels({...})` to replace the label set (`{}` clears every
label but `name`). A label key must not be empty: `set_labels()`, the
`create*()` methods and `search(labels=...)` refuse one with `BadRequestError`
before any request. The reserved `name` label that `find_by_name()` matches is
kept unless the new labels set it: the sandbox is read first to learn it (one
more request, not atomic with the write), and it cannot be removed. Use `sandbox.entrypoint_logs()` to read the sandbox's
entrypoint-session log as plain text. On `AsyncDaytona` prefix these with
`await`.

Use `sandbox.update_env({...}, unset=(...))` to update the environment
inherited by processes, PTYs and sessions started afterwards. Running processes
keep their current environment, and `unset` removes only values previously set
through `update_env()`, not image-level environment entries.

### Volumes

Manage volumes with `d.v2.volumes.list()`, `iter()`, `get()`, `create()` and
`delete()`. `create(name=...)` and `get(name, create=True)` let the server
choose the volume's type. Only `create()` takes `type` and `size_gib`: pass
`type="disk"` (sized with `size_gib`) or `type="bucket"` to choose it.
`id_only=True` on `get()` or `delete(name_or_id)`
disables name fallback. Deletion returns when the API accepts the request,
not when deletion finishes.

```python
from daytona.v2 import VolumeMount

volume = d.v2.volumes.get("data", create=True)
sandbox = d.v2.create(
    "py312",
    volumes=[VolumeMount(volume_id=volume.id, mount_path="/mnt/data", subpath="project")],
)
```

`mount_path` is an absolute path with no `..` component. The API normalizes
it (repeated slashes, `.` components and a trailing slash are removed) and
refuses `/`, any path on or beneath `/bin`, `/boot`, `/dev`, `/etc`, `/lib`,
`/lib32`, `/lib64`, `/mnt/scratch`, `/overlay`, `/proc`, `/sbin`, `/sys` or
`/usr`, and one with a control character, with `BadRequestError` (400).

`create_from_build(..., volumes=[...])` accepts the same mounts. To mount an
S3 bucket you own, create a bucket volume for it in the dashboard or with
`daytona volume create NAME --type bucket --bucket BUCKET_NAME
--access-key-secret ACCESS_KEY_SECRET --secret-key-secret SECRET_KEY_SECRET`
(the last two name project secrets holding the bucket's keys), then attach it
through `volumes`. `VolumeMount(..., read_only=True)` is
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

This is separate from the platform `d.v2.volumes` management service.
The volume ID is the attached volume's `vol_` ID or its UUID. IDs contain 1–128
ASCII letters, digits, underscores or hyphens. `PullVolumeResult` has
`request_id`, `status` (`pulled`, `unchanged`, `pending`, or `unknown`) and
`manifest_id` (`None` when there is none); `raw_status` preserves the server's
status string.
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
first, always waits for that image to become ready, then creates the sandbox
from the image id. The build submission is an ordinary request, bounded by
`request_timeout`; `build_timeout` then bounds only the build wait (seconds;
`None`, `0` or a value above about 24.8 days means no deadline). A failed build (status `failed` or `error`)
raises `ImageBuildFailedError` and a
build that outlasts `build_timeout` raises `RequestTimeoutError`; in both
cases no sandbox exists. The create then gets the full, independent
`wait_seconds` budget and takes the same keywords as `create()`.

```python
from daytona.v2 import ImageBuilder, Resources

builder = ImageBuilder.from_registry("python:3.12").run_commands("pip install numpy")
sandbox = d.v2.create_from_build(
    builder, resources=Resources(cpu=2, memory=4), build_timeout=600, wait_seconds=60
)
```

A builder can copy local files into the image. `add_local_file`,
`add_local_dir` and `pip_install_from_requirements` check the path when they
are called and read it when the image is built: the SDK packs the rendered
Dockerfile and the files into a build context and uploads it (up to 1 GiB;
an unchanged builder reuses its earlier upload). A directory's own
`.dockerignore` excludes entries as `docker build` would.

```python
builder = (
    ImageBuilder.debian_slim("3.12")
    .pip_install_from_requirements("requirements.txt")
    .add_local_dir("src", "/app")
    .workdir("/app")
    .cmd("python", "main.py")
)
sandbox = d.v2.create_from_build(builder, build_timeout=600)
```

To build an existing Dockerfile with its directory as the context, pass the
directory to `images.build_from_context()`; it uses `<dockerfile>.dockerignore`
next to the selected Dockerfile when present, otherwise `.dockerignore` at
the context root, the way `docker build` does:

```python
image = d.v2.images.build_from_context("./service", dockerfile="Dockerfile", name="service")
image = d.v2.images.get(image.id, wait="ready", timeout=600)
```

`ImageBuilder.env()` values are literal: the image gets exactly the string you
pass (`$PATH` is not expanded); a blank value, or one with a control character
(such as a newline), is rejected.

`wait_seconds` is the create wait budget in whole seconds (default 60). The
server holds create for up to its own short cap, then the SDK waits for the
remainder. `wait_seconds=0` returns as soon as the create is accepted. Once
the create request has succeeded, every `create*()` returns the handle however
its wait ends — the budget runs out, the sandbox reaches a terminal state, or
a read during the wait fails: check `sandbox.state` and `sandbox.error` before
doing work. A raised create or fork means the request itself failed, except for
`SandboxCreateRequestTimeoutError` and `SandboxCreateConnectionError` (they
carry the idempotency key because a sandbox may exist), caller cancellation,
and an unexpected non-SDK error during the wait. Retry
those two with their `idempotency_key`; a cancelled create may already have
created a sandbox. `create_timeout_seconds` is the server-side
queue/boot budget in seconds (an integer, at least `-1`); `-1` asks the server
to apply its maximum.

Linux sizing uses one `resources=Resources(cpu=, cpu_max=, memory=, memory_max=,
disk=, disk_max=)` entry: CPU in cores, memory and disk in GiB that must
convert to a whole number of MiB (`value * 1024`), else `BadRequestError`.
Sizes read back exactly. `create_from_snapshot()` inherits the snapshot's
configuration and takes no resources, CPU selectors, environment, volumes,
secrets or network policy.

`images.get(id, wait="ready", timeout=120)` and
`snapshots.get(id, wait="ready", timeout=120)` wait for the artifact to
settle (`ready`, `failed` or `error`): they repeat the server's long poll,
each hold at most 60 s (the wait budget governs it, not `request_timeout`),
until it settles, and return it for you to check its status. A budget that
runs out first raises `RequestTimeoutError`; a budget above the timer maximum
(about 24.8 days) waits without a deadline. Without a timeout (omitted or
`0`), one long poll of the server's default length returns what the server
reports. `sandbox.capture_image(name=..., idle_expiry_seconds=...)` captures an
image from a sandbox, and `sandbox.create_snapshot(name)` a snapshot. Both need
`write:sandboxes` in addition to `write:images` / `write:snapshots`; without it
they raise `ForbiddenError` (403).

### macOS sandboxes

A macOS sandbox runs on a dedicated Mac reserved by your organization. Create
one with `d.v2.create_macos(image, ...)` (`POST /sandboxes/macos`);
`create()` makes Linux sandboxes.
`image` is a macOS image name
such as `base-macos`. `resources=MacosResources(cpu=, memory=, disk=)` sizes
the sandbox: `cpu` is an integer number of physical cores (at least 4),
`memory` is GiB (at least 4) and `disk` is GiB (at least 80: the whole sandbox
disk, macOS included); each omitted value uses the Mac model's default size
(`default_size`). A create from a captured image that omits `disk` gets the
image's disk size, and a smaller `disk` is refused. The size is fixed at creation and reported once a Mac takes the sandbox
(`sandbox.memory` and `sandbox.disk` in exact GiB, `sandbox.reservation_id`
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

Find available regions and their regional API endpoints with `daytona region list`.
Use `d.v2.mac_models.catalog(region="<region>")` or
`daytona mac-model list --region '<region>'` to discover available Mac models
and current prices. Replace `<region>` and `<model>` in the examples with
values from these catalogs. When using a regional API endpoint, set
`DAYTONA_V2_API_URL` before creating the client. Against an API that does not
serve the region, `create_macos` raises the API's `ConflictError`; the SDK
does not send the create anywhere else.

A sandbox without a size uses the model's default sandbox size:

```python
from daytona.v2 import MacosResources

d.v2.create_macos("base-macos")
# Or choose resources explicitly:
d.v2.create_macos("base-macos", resources=MacosResources(cpu=4, memory=8, disk=100))
```

A reservation reserves one physical Mac of a named model. Set CPU, memory,
and disk sizes when creating each sandbox. `d.v2.mac_models.list()` lists each
model's resources, sandbox disk space, `max_sandboxes`, and `default_size`,
the size that omitted values use. The disks and disk volumes of the sandboxes
on one Mac at once must fit its `sandbox_disk_mib`.
Reserve a Mac, then create a sandbox using the model's default size:

```python
reservation = d.v2.reservations.create(region="<region>", model="<model>")
d.v2.create_macos("base-macos", region="<region>", reservation=reservation.id, persistent=True)
```

Stop and pause need a persistent sandbox (`persistent=True`), which keeps its
disk and machine across stop and start, and its memory across pause and
resume. A start can run on any Mac of the organization; after a start on a
different Mac, iCloud asks the user to sign in again. Delete erases the disk.
A reservation end or a Mac failure stops a persistent sandbox that has started
and never deletes it; a Mac failure can lose its most recent writes. An ephemeral sandbox ends
with its reservation, or with its Mac when that Mac fails.

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
request: all of them, or none if fewer are free or fit under the organization's
Mac reservation limit, unless `min_count` accepts fewer. A purchase at that
limit raises `LimitExceededError` (a `RateLimitError`) with `limit ==
"max_mac_reservations"`. A bulk purchase raises it when fewer than `min_count`
fit under the limit, which is checked first, and `ConflictError` with code
`NO_MAC_AVAILABLE` when fewer than `min_count` Macs are free. Every purchase
and renewal carries an idempotency key; pass your own so a retry after a lost
response neither reserves more Macs nor pays for a second period:

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
or through auto-reserve, finds no fitting Mac in the region). Buying a
reservation when the organization holds its Mac reservation limit raises
`LimitExceededError` with `limit == "max_mac_reservations"` instead.
`MAC_DISK_FULL` never buys a reservation. Starting a stopped macOS sandbox
(`start()`) uses its disk and size on any Mac of the organization; it answers
`NO_FREE_SLOT` when no Mac has space for its size and `MAC_DISK_FULL` when the
Mac with space is low on free disk space. With no Mac
reservation in the region and auto-reserve on, the start buys one, so it can
fail as a create that buys one does, `LimitExceededError` with
`limit == "max_mac_reservations"` included. A start, `fork()` or
`capture_image()` answers `ConflictError` with `code == "DISK_NOT_READY"` while
the sandbox's disk is not ready yet; retry later. Reservations report `model`,
`capacity`, `resources_used`, `origin` (`explicit` or `auto`), `auto_renew`
and `renewal_error`; `reservations.update(id, auto_renew=...)` changes renewal
on a live reservation.
An organization over its Mac reservation limit, after a plan downgrade for
example, keeps every Mac until the end of the period it paid for, but the
reservations above the limit do not renew: their `auto_renew` turns off and they
end at the end of that period. `reservations.renew()` of such a reservation
raises `LimitExceededError` with `limit == "max_mac_reservations"`.

`pause()` and `resume()` work on persistent macOS sandboxes. `pause()` saves
the sandbox's memory on its Mac and waits for `paused`; `resume()` puts the
sandbox in `assigned` and waits for `started`. A paused sandbox keeps its
memory on its Mac. With auto-renew on, it keeps its reservation renewing;
otherwise the sandbox is stopped, with its disk, before the reservation ends.
After a resume,
`sandbox.mac_boot_mode` reports how the sandbox resumed; `sandbox.error`
provides error details when available.

A macOS `fork()` copies the disk only: omit `memory` or pass `memory=False`
(`memory=True` answers `NOT_SUPPORTED_FOR_CLASS`). A fork of a running macOS
sandbox boots with a fresh machine identity; a fork of a paused or stopped
one rests `stopped`. `fork(wait_seconds=…)` (default 60; `0` returns once
accepted) waits for `started` for a running source, and `stopped` for a paused
or stopped source. If the budget expires first, it returns the handle even
though the fork has not reached that state; check `fork.state`. For a running
source, `fork.wait()` keeps waiting for `started`; for a paused or stopped
source, use `fork.wait(until="stopped")`.

### Processes

`sandbox.exec()` is the root alias of `process.run()`. `process.run()` executes
a command and returns a `ProcessResult`: `stdout`, `stderr`, `output`, the log
frames it read them from (`logs`), and how the process ended (`reason`,
`exit_code`, `signal`). `exit_code` and `signal` are set only when the sandbox
reported them; `reason` is always set (`"unknown"` with the value on
`raw_reason` for one this release does not know). `run()` reads the output
back from the process log, so its `keep_logs=` takes `"on_exit_ttl"` (default)
or `"until_cleanup"`, not `"none"`. It returns no handle to write to, so it
refuses `stdin="open"`: use `start()` for a process that reads stdin.

`process.start()` (and `session.start()`) sends `keep_logs="on_exit_ttl"`
unless you pass another value: the sandbox keeps the record and log for a while
after the process exits; `"until_cleanup"` keeps them until the process is
deleted, and `"none"` keeps no log. `process.start()` returns a detached
`ProcessHandle` with `logs()`,
`stream_logs()`, `wait()`, `write_stdin()`, `close_stdin()`, signal-aware `kill()`,
`delete()` and `resize()`. `write_stdin()` / `close_stdin()` are for exec and
PTY handles only. `stream_logs()` yields one `StreamLogEvent` per server
event of a supported type (an event of another type is skipped), each
carrying its resume `cursor`, and stops after the `eof` event: `StreamLogFrameEvent` (a
`LogFrame` on `.frame`), `StreamLogStateEvent` (the process ended),
`StreamLogWarningEvent` (older frames were evicted) and `StreamLogEofEvent`;
a stream error raises its typed error. The sandbox ends the stream with
`eof`, so an iteration that ends normally has the whole log; a stream cut
short, or one that ends without `eof`, raises `ConnectionError`: call
`stream_logs(cursor=...)` again with the last event's cursor. `timeout=` is the
caller wait budget **(seconds)**, and `timeout=0` means wait unbounded.
With `encoding="base64"`, `logs()` and `stream_logs()` return each frame's
`data` as standard padded base64, which `base64.b64decode()` decodes.
`execution_timeout=` is the process deadline **(seconds)** for server-managed
runs that support one (omitted or `0`: no deadline); `kill(escalate_after=)` is in seconds too. Signals are
bare names (`"TERM"`), in `kill()` and in what `wait()` and `get()` report.

Handles come from the SDK, never from a constructor: `handle.to_json()` returns
a `ProcessHandleJSON` (the sandbox and process ids, plus the process `kind`
when it is known; the same JSON the TypeScript SDK writes), and `sandbox.process.connect(value)` reconnects it, or a
bare process id, on any worker without a request. Reconnected `kind='code'`
handles remain replay/control handles (`logs()` / `wait()` / `kill()`), not a
way to recover a parsed `InterpreterResult` on another worker.

`pty.start(command=None, *, terminal=TerminalSize(cols=..., rows=...), ...)`
starts an interactive terminal (80×24 when `terminal` is omitted; `cols` and
`rows` are 1 to 65535; a Windows sandbox refuses more than 32767) with `TERM=xterm-256color` unless `env` sets `TERM`; it also
takes `cwd`, `env`, `name`, `user`, `shell`, `login`, `stdin` (default
`"open"`), `execution_timeout` and `keep_logs` (default `"until_cleanup"`).
`pty.list()` is the PTY-only view of `process.list(kind="pty")`, and
`pty.delete(id)` kills then cleans up that PTY record.

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
Use `session.to_json()` (a `SessionJSON`) and
`sandbox.process.connect_session(identity)` to reconnect without a request. `process.list(kind="session")` lists sessions;
`process.list(session_id=session.id)` lists their commands. Sessions survive
pause/resume but not a sandbox stop/start. In dash, BusyBox `sh` and mksh, an interrupt leaves only the
innermost shell function or sourced file; bash and zsh leave the whole command
(bash older than 4.4, macOS's `/bin/bash`, only when no DEBUG trap is set).

### Tags

Group independently started processes with an optional `tag`, a safe identifier
of 1–128 characters using ASCII letters, digits, `.`, `_` and `-` (not `.` or
`..`):

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
await handle.kill()  # Linux with process containment: also stops the server.
```

These examples use `AsyncDaytona`; omit `await` with `Daytona`. Tags also work
with `process.run()`, `sandbox.exec()` and `pty.start()`. A tag is immutable;
session commands inherit it, and interpreter code executions cannot be tagged.
`ProcessInfo.tag` exposes it. `kill_all()` keeps records and logs and returns
the ids of the tagged processes that were running, or had running descendants,
when it began; survivors of deleted or expired records are killed too but not
listed. Its `timeout` is in seconds (more than 0 and at most 60, fractions
allowed, default 10). A tag or timeout outside those rules raises
`BadRequestError` before any request. `ProcessKillIncompleteError` means the
timeout expired before all work stopped, and retrying is safe. A start racing the kill either precedes it
and is killed, or starts afterwards and keeps running.

Linux and Windows support tags, filtering and tag-wide kills. macOS supports
tags and filtering only: `kill_all()` raises `UnsupportedOperationError`, as it
does on sandboxes without process containment. On Linux with containment,
`kill()` also stops detached descendants (`setsid`, `nohup`, double fork) and
survivors of an exited process; macOS `kill()` is unchanged. Tags can be reused
immediately after `kill_all()`.

### Files

All sandbox filesystem paths are absolute strings starting with `/`; relative
paths are refused locally with `BadRequestError`. Local upload/download paths
may be relative. Path lists must be sequences of strings, not a bare string.
`write_text()` requires a string; `write_bytes()` and `write_many()` accept
bytes-like buffers, including `bytes`, `bytearray` and `memoryview`.

`read_text` / `write_text`, `read_bytes` / `write_bytes`, `upload_file()` /
`download_file()`, bulk `read_many` / `write_many`, `remove()`, `grep()` (file
contents) / `glob()` (file names), `watch_dir()` for directory watches, and
`presigned_download_url` / `presigned_upload_url` for direct transfers. The
platform mints the link; `expires_in_seconds` (default 3600) must be between 1
and `SIGNED_FILE_MAX_TTL_SECONDS` (7 days) — links always expire.
`sandbox.rotate_signing_key()` retires every pre-signed URL minted so far;
new URLs use the new key, which is never
returned to the client. `list()`, `grep()`, `glob()` and `replace()` return one
`ListResult` page (of `FileInfo`, `FileMatch`, path strings and
`ReplaceResult`; read them from `.items`); these methods have no
page-draining iterator and take no cursor argument. `grep()` and `glob()` run
as the sandbox user and return a `FileSearchResult`, a `ListResult` whose
`skipped` tuple lists the absolute paths below the root that user cannot read
(not searched; at most 1000); only an unreadable root raises
`FileAccessDeniedError`. `read_text()`
decodes strictly as UTF-8 and keeps a leading byte-order mark.

`watch_dir(path, on_event, *, recursive=False, on_overflow=None, on_exit=None)`
calls `on_event` for each change (a `FilesystemEvent`; `timestamp_ms` is Unix
milliseconds; `type` is `"create"`, `"write"`, `"remove"`, `"rename"`,
`"chmod"` or a kind a later sandbox adds, passed through as its string). Callbacks may be plain or coroutine functions; they run one at
a time and are awaited. A failing `on_event` or `on_overflow` ends the watch
with that error, which `exit_error` / `wait_for_exit()` report and `on_exit`
receives; a failing `on_exit` is ignored. A watch whose stream drops ends with
`ConnectionError`; one whose directory is deleted ends without an error, after a
last `remove` event with an empty `name`, where the sandbox reports the removal.
`on_exit` runs exactly once for a watch `watch_dir()`
returned; a failure before the watch starts (a refused connection, an HTTP
error, a stream that ends before `start`) is raised from `watch_dir()` and
does not call it. `stop()` returns once a callback that is already running,
and `on_exit`, have finished.

To stop a watch from an event, signal the code that holds the handle (an
`asyncio.Event`, or a `threading.Event` with the synchronous client) and call
`stop()` there; this works in every case. As a shorthand, after a direct
`await watch_dir(...)` a callback (or `on_exit`, or a task it started) can call
`stop()` on the returned handle itself: callbacks are held until `watch_dir()`
has returned its handle. When `watch_dir()` runs inside another task
(`asyncio.gather`, `asyncio.create_task`, or `asyncio.wait_for` on Python
3.10–3.11), or with the synchronous client, the first events can reach the
callback before the caller holds the handle.

With the synchronous `Daytona` client the callbacks run on the SDK's
event-loop thread. There a callback may call its own watch's `stop()` (which
returns without waiting), `wait_for_exit()` and `exit_error`. Any other
synchronous SDK call, including another watch's methods, raises a
`DaytonaError` (source `SDK`, no code), which ends the watch.

`read_bytes()`, `read_text()`, `read_many()` and the computer-use recording
`download()` hold the whole response in memory: they take
`max_response_bytes=` to replace the client's cap for one call (`None`, the
default, keeps the client's configured cap, 1 GiB unless set; `0` means no
limit; any other value, a negative, fractional or oversized number, a
boolean or a string included, is a `BadRequestError`). For large files, or content from a sandbox you don't trust, use `download_file()`, which streams to disk
with progress and cancellation (for a recording, pass its `file_path`, not its
id).

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
for a throwaway run or use `create_context(language=..., context_id=None,
cwd=None, env=None)` / `connect_context()` / `list_contexts()` for an
independent named context. Select a context with `run(..., context=...)`: a
handle, its `to_json()` identity (an `InterpreterContextJSON`) or a context
id with `language=`.
A context's `run(code, *, cwd=None, timeout=None)` takes no `env`: set it when
the context is created. If an explicit named
context vanished, `interpreter.run(..., context=...)` / `context.run()` surface
the typed `SessionNotFoundError`; recreate the context yourself and decide what
to rerun. `timeout=` is enforced client-side **(seconds)**: on expiry the kernel
is interrupted. Results are split into `results[]`, `logs{stdout,stderr}`,
`error`, and `process`. That rich result shape comes from the interpreter run
path itself; reconnecting a serialized code-process handle later does not
recreate it.

### Git

`add()`, `restore()` and `reset(files=...)` require a sequence of string
pathspecs, not a bare string. Repository paths and pathspecs may remain relative;
the filesystem's absolute-path rule does not apply to git.

`clone()`, `pull()` and `push()` use a dedicated **30-minute** remote-operation
deadline by default, and each accepts a per-call `timeout=` override
**(seconds)**: one total deadline for the whole request (`0` means no limit,
as for every timeout).
`clone()` also accepts `depth=` for shallow clones and
`insecure_skip_tls=True` for one clone operation. `pull()` and `push()` accept
`branch=` and `remote=`; `push()` also accepts `set_upstream=True`.
`commit(path, message, *, author=..., email=...)` keeps the identity fields
keyword-only. Pass `allow_empty=True` to create an empty commit when Git would
otherwise refuse it. `status()` returns a `GitStatus`; `history()`,
`list_remotes()` and `list_branches()` return one `ListResult` page of
`GitCommit`, `GitRemote` and branch-name items. A clone, pull or push
`timeout=` bounds the whole request. `delete_branch(path, name)` is a safe
deletion: an unmerged branch raises `ConflictError`; pass `force=True` to
delete it anyway. Expected git failures raise typed errors:
`GitAuthFailedError` (401), `GitRepoNotFoundError` and
`GitBranchNotFoundError` (404), `GitBranchExistsError` (409), and
`ConflictError` for an unmerged branch, a checked-out branch or a checkout
that would overwrite local changes.

### Ports, computer use

`ports` owns both listener discovery and preview exposure:
`list()`, `is_in_use(port)`, `expose(port, public=...)` (also changes the
visibility of an exposed port), `preview_link(port)`,
`signed_preview_link(port, expires_in_seconds=...)`,
`expire_signed_preview_links()` (expires every signed preview link of the
sandbox, on every port), paginated `list_endpoints(...)`, and
`iter_endpoints(...)`. The sandbox daemon's ports 2280, 2281 and 22220 are
never preview ports: `expose()`, `preview_link()`, `signed_preview_link()` and
`create_access_token(ports=...)` refuse them with `BadRequestError` before any
request.
A visibility change takes effect immediately on event delivery; at most 30 s
if an event is missed.
To skip the preview warning page when a headless browser opens a preview URL,
send `{PREVIEW_WARNING_SKIP_HEADER: PREVIEW_WARNING_SKIP_HEADER_VALUE}` (both
exported from `daytona.v2`) with its requests.
`computer_use` stays lifecycle-flat (`status()`, `start()`, `stop()`, `restart()`) and
splits actions into `service`, `mouse`, `keyboard`, `screen`, `display`,
`a11y` and `recording`. `screen.screenshot()` captures the whole screen or a
`region=ComputerUseRegion(...)`; any of `format=`, `quality=` or `scale=`
returns a compressed image. `a11y.tree(scope="focused")` returns at most 256 levels below
the root (`max_depth`, an integer from 0 to 256, default 256); a deeper UI
comes back cut at that depth with `truncated` set.

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
the deadline. `timeout=0`, or a value above about 24.8 days, waits without a
deadline. A Docker failure never
makes the sandbox itself unusable.

Set `DAYTONA_DOCKER_START` in the sandbox env to choose how Docker starts:
`auto` (default; start the image's `dockerd` when it ships one), `entrypoint`
(the image's own entrypoint starts Docker) or
`off` (Docker is not started; state `disabled`). If Docker status is unavailable,
`status()` and `wait_until_ready()` raise `NotFoundError` (404).

Some environment variable names are reserved for the platform, among them
every `DAYTONA_*` name other than `DAYTONA_API_URL`, `DAYTONA_OTEL_ENDPOINT`
and `DAYTONA_DOCKER_START` (`DAYTONA_SANDBOX_TOKEN` included). A sandbox `env`
that sets a reserved name fails the create with `BadRequestError` (400) naming
the key. `update_env()` refuses reserved `DAYTONA_*` names in the keys it sets or
unsets too (`DAYTONA_SANDBOX_ID`, for one), by the sandbox's own list, which
differs from the create's: it accepts `DAYTONA_SANDBOX_TOKEN`, for one.

### Waiting for lifecycle states

Use `sandbox.wait(until="started", timeout=120)` to wait for a public
lifecycle state. `until` also accepts a list of states. The default is
`started`, with a 60-second budget; `timeout=0`, or a value above about
24.8 days (the timer maximum), waits without a deadline. Running out raises `RequestTimeoutError`; a terminal state (`destroying`,
`destroyed`, `error`) reached first raises `SandboxTerminalStateError`, with
the observed public state as `error.state` and the wire status as
`error.raw_state`. When the server explains that state, the explanation is
`error.reason` and ends the error message.

`start()`, `stop()`, `pause()`, `resume()` and `delete()` take `timeout=`
(seconds) with the same default and unbounded form. They wait for
completion: `stop()` returns at `stopped`, and `delete()` returns at
`destroyed` (a sandbox disappearing during deletion also means completion).
Client methods accept a sandbox or its id and the same options, for example
`d.v2.stop(sandbox.id, timeout=120)`. Once the target state has been
observed, a failed metadata refresh afterwards does not turn that success into
an error. The server decides which lifecycle actions the sandbox supports.

`fork(wait_seconds=60)` has its own wait budget; fractions are allowed,
`0` skips waiting, and a value above about 24.8 days waits with no deadline;
the fork's detail read counts against a finite budget. Like create,
fork returns its handle once the fork request succeeded, however the wait
ends. A running Linux source produces a running fork; otherwise
the fork is paused if the source was paused and memory was retained, or
stopped. macOS forks copy only disk: a running source waits for `started`;
a paused or stopped source waits for `stopped`, within the same wait budget.

`sandbox.delete()` and `d.v2.delete(sandbox)` wait until the sandbox is
`destroyed` (a 404 while waiting counts as destroyed). A sandbox with volumes
attached stays `destroying` until the platform has finished with its volumes:
usually seconds, but minutes when a lot of volume data is written at the end,
so give `delete(timeout=600)` a budget that covers it. If that takes too
long, the sandbox reports `error` and the wait raises
`SandboxTerminalStateError`; when the server explains why the deletion did not
finish, that is its `reason`. Such a sandbox can still reach `destroyed`
later. To keep waiting for it, catch the error, pause for a few seconds and
call `sandbox.wait(until="destroyed", timeout=600)`: while the sandbox still
reports `error`, the call raises again at once.

Deleting the image or snapshot immediately afterwards can still raise
`ConflictError` (409) while references are being released. A missing sandbox
during `wait()` raises `NotFoundError`; only `delete()` maps a 404 to
`destroyed`.

## Errors

The package root exports the error hierarchy both namespaces share:
`DaytonaError` and one class per category — `DaytonaBadRequestError`,
`DaytonaAuthenticationError`, `DaytonaForbiddenError`,
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

The v2 classes (`from daytona.v2 import NotFoundError, …`) have the same
names as in the TypeScript SDK: `BadRequestError`, `AuthenticationError`
(401), `ForbiddenError` (403), `NotFoundError`, `ConflictError`,
`RateLimitError`, `RequestTimeoutError` (408), `ClientTimeoutError` (the
client's own request timeout), `ConnectionError`, … They expose
`status_code`, `code`, `source` and `trace_id`, and have code-specific
subclasses (`ProcessNotFoundError`, `SandboxTerminalStateError`, …) so you can
catch as narrowly as you need. A call into a sandbox that is not running (a
stopped one), through `sandbox.process`, `sandbox.fs`, `sandbox.git` or the
other in-sandbox namespaces, raises `SandboxNotRunningError` (409,
`SANDBOX_NOT_RUNNING`), a `ConflictError`, not `NotFoundError`; its preview
URLs answer 409 too. Start the sandbox and retry. `code` and `source` are open vocabularies: a
code this release does not know is kept as sent and the error is the class for
its HTTP status; `daytona.v2.codes` lists the known codes per source
(`codes.API_ERROR_CODES`, `codes.CODE_STATUS`, `codes.is_known_error_code`, …).
The SDK never follows an HTTP redirect: a 3xx response raises `DaytonaError`
with its `status_code` (source `SDK`).
Import the v2 errors by name rather than with `from daytona.v2 import *`:
`ConnectionError` and `FileNotFoundError` are the SDK's categories and would
shadow Python's built-ins. Version-specific errors are imported from
`daytona.v2` (or `daytona.v0`).

The standard errors' constructors take the message plus keyword-only fields
(`status_code`, `code`, `source`, `trace_id`, …), so tests can build the
errors your code handles. A few specialised errors build their own message
from positional identity arguments instead:
`SandboxTerminalStateError(sandbox_id, state, *, raw_state=None, reason=None)`,
`SandboxCreateTimeoutError(sandbox_id)`,
`SandboxCreateRequestTimeoutError(idempotency_key)`,
`SandboxCreateConnectionError(idempotency_key, detail)`,
`DockerWaitTimeoutError(status)` (`status` may be `None`) and
`DockerNotReadyError(status)`.

A request refused because your organization has hit one of its limits (for
example the maximum number of concurrent sandboxes) raises
`LimitExceededError` — a `RateLimitError` that also carries the limit's name
and counts as `limit`, `current` and `max`. Other rate-limited requests keep
raising plain `RateLimitError`.

On a sandbox that is deleted or being deleted, `set_labels()`,
`update_lifecycle_policy()`, `reset_idle_timer()` and the preview-endpoint calls
(`ports.expose()`, `ports.list_endpoints()`, `ports.preview_link()`,
`ports.signed_preview_link()`) raise `ConflictError` (`CONFLICT`);
`ports.unexpose()` and `ports.expire_signed_preview_links()` still work.

## Versioning and releases

See `docs/VERSIONING.md` and `CHANGELOG.md` in the
[repository](https://github.com/daytona/clients).
