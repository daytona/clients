# @daytona/sdk

The official TypeScript SDK for Daytona sandboxes. Ships ESM + CJS with type
declarations. Node >= 22.15 (or a browser environment with `WebSocket`).

> **Alpha.** This SDK is published as alpha prereleases under the `alpha`
> dist-tag. Install with `npm install @daytona/sdk@alpha` (or pin an exact
> `X.Y.Z-alpha.N` version). The API may still change between alphas.

## Install

```bash
npm install @daytona/sdk@alpha
```

The package includes the v0 namespace, so installing it also installs the
0.x SDK's runtime dependencies (AWS S3 client, OpenTelemetry, socket.io, …),
even if you only use `daytona.v2`. In Node builds they load only on first
`daytona.v0` access (a browser bundle of the root includes them; see Browser apps).

Tracing is off unless you set `otelEnabled`. The published package already
includes `@opentelemetry/api`; install it yourself only for an unbundled build
from source.

Type-checking the root `@daytona/sdk` declarations with `skipLibCheck: false`
needs TypeScript 5.7 or later (and `esModuleInterop` under node10 / CommonJS
resolution, as for the 0.x SDK): the root's `daytona.v0` type comes from the
vendored v0 SDK, whose declarations use generic `Uint8Array<…>` and a default
import of `isomorphic-ws`.

## Quickstart

```ts
import { Daytona } from '@daytona/sdk'

const daytona = new Daytona({
  apiKey: 'dtn_…', // a project API key, minted in the dashboard
  requestTimeoutSeconds: 30, // total time per ordinary request (default 60)
  // jwtToken: 'eyJ…',   // optional alternative to apiKey
  // projectId: '00000000-0000-0000-0000-000000000000',
  // organizationId: 'org-id', // v0 only
  // target: 'eu', // deprecated, v0 only: pass `target` per d.v0 create instead
  // otelEnabled: true,
  // connectionPoolMaxsize: 32,
})

await daytona.v2.images.pull('python:3.12', { name: 'py312' }) // waits until ready
const sandbox = await daytona.v2.create('py312', { waitSeconds: 60 })
const result = await sandbox.exec('python -V')
console.log(result.exitCode, result.output)
await daytona.v2.delete(sandbox)
```

`images.pull()` waits until ready by default. Set `{ wait: false }` to skip
the readiness wait, or `{ timeoutSeconds: 120 }` for a 120-second budget
(omitted or `0` is unbounded). A failed import throws
`ImageBuildFailedError` with the server's error message. `images.build()`
remains non-waiting.

The root client is the only way to build a client: `daytona.v2` and
`daytona.v0` are its namespace clients. `@daytona/sdk/v2` exports v2's resource
types, builders and errors, but not a client (`import { Daytona } from
'@daytona/sdk/v2'` fails); type a stored v2 client as `DaytonaV2Client` from
`@daytona/sdk`.

`daytona.v2` waits (`create`, `start`, `wait`, …) ride one WebSocket
per regional API endpoint, shared by the v2 clients in the process that have the same API URL,
API key or JWT identity, and effective events settings (`sharedHub` and
`events` on the config). Closing is optional: in Node an idle socket stays open
for the next wait but does not keep the process alive, so a script can simply
end; a runtime where the socket cannot be released from the event loop closes
it 10 s after the last wait instead, and `events.idleCloseSeconds` overrides
either (values above about 24 days clamp to that maximum). `await daytona.close()`
releases this client's reference at once; the shared socket closes when the
last client sharing it closes.

The root client reads `DAYTONA_API_KEY`, `DAYTONA_JWT_TOKEN`,
`DAYTONA_ORGANIZATION_ID` (scopes a JWT for the v0 namespace),
`DAYTONA_PROJECT_ID` (scopes a JWT for the v2 namespace), and
`DAYTONA_OTEL_ENABLED` from the environment when a field is omitted.
Environment values are trimmed, and a blank one counts as unset; values you
pass in the config are used as given, and an empty one (`apiKey: ''`, …)
throws `BadRequestError` instead of falling back to the environment.
`DAYTONA_V2_API_URL` selects another v2 API endpoint (a regional endpoint from
`GET /regions`, or a test environment);
when it is unset the SDK uses its built-in default API URL. `daytona.v0`
always targets Daytona's v0 API URL. SDK 2.x never reads `DAYTONA_API_URL`, the
0.x SDK's variable.
`daytona.v2` reads `DAYTONA_*` variables from the process environment only;
`daytona.v0` keeps the 0.x SDK's behaviour of also reading `.env.local` /
`.env` when `dotenv` is installed. Export the key, or pass `apiKey`, so both
namespaces see it.

`new Daytona(config)` copies the config: changing the object afterwards has no
effect on the client. A key the config does not define throws
`BadRequestError`, and a renamed key names its replacement (`requestTimeout`
→ `requestTimeoutSeconds`; `events.reconcileIntervalMs` /
`events.idleCloseMs` → `reconcileIntervalSeconds` / `idleCloseSeconds`).
`requestTimeoutSeconds` (default 60; `0`, or a value above 2,147,483.647 (about 24.8 days),
means no limit) is the total time one
ordinary request may take before it throws `ClientTimeoutError`; waits and long
polls use their own budget, streams are bounded only until their response
arrives, and file transfers are not bounded.

The root package exports a shared v0/v2 error hierarchy (`DaytonaError`,
`DaytonaNotFoundError`, `DaytonaTimeoutError`, …). Catch those from
`@daytona/sdk` when code may see either namespace, including v0 error classes
imported from `@daytona/sdk/v0`; v2-only errors stay on `@daytona/sdk/v2`.

`@daytona/sdk/v0` is the 0.x Daytona SDK, vendored into this package at a
pinned release. It exports the 0.x resource types and errors, but not the
client: the root client is the only way to build one (`new Daytona().v0`).
`import { Daytona } from '@daytona/sdk/v0'` fails at build or import time, and
`new (require('@daytona/sdk/v0').Daytona)()` throws an error naming the root
form. Type
a stored v0 client as `DaytonaV0Client` from `@daytona/sdk`.

Need the 0.x SDK side-by-side while you migrate? Keep your existing type
imports on `@daytona/sdk/v0`, move new code to `@daytona/sdk/v2`, and use the
root client for shared credentials:

```ts
import { Daytona } from '@daytona/sdk'

const daytona = new Daytona({ apiKey: 'dtn_…' })
for await (const sandbox of daytona.v0.list()) console.log(sandbox.id)
await daytona.v2.images.pull('python:3.12', { name: 'py312' })
await daytona.v2.create('py312')
```

`daytona.v2.create()` with no image (or `create(options)`) boots the platform
default image, a Linux image Daytona publishes for every project.
`daytona.v2.images.list({ platform: 'true' })` lists the platform images; the
default one has `default: true`. When no default is configured, the create
throws `DEFAULT_IMAGE_NOT_CONFIGURED` (400).

SDK 2.x targets Daytona Cloud. For a custom API URL, stay on SDK 0.x: the root
rejects `apiUrl`, and SDK 2.x (`daytona.v0` included) never reads
`DAYTONA_API_URL`.

`daytona.v0` is the v0 SDK's own `Daytona` client, the same instance on every
access, so code written against the v0 SDK keeps its types, `instanceof`
checks and service references. In Node builds the first access loads the v0 SDK
synchronously (about 100 ms, once per process; an app that cares can touch
`daytona.v0` once at startup), and code that only uses `daytona.v2` never loads it.

**Browser apps:** `daytona.v0` works in browsers too. A browser bundle of the
root client includes the v0 SDK (the bundler resolves it at build time, so
`daytona.v0` stays synchronous), which makes the root heavier for browser
apps: about 850 kB minified with webpack, against about 64 kB for v2 alone.
Browser apps pay it even when they only use v2, because the root is the only
way to build a client. Node builds are unaffected:
they load the v0 SDK only on the first `daytona.v0` access. v0 operations that
use Node APIs (file transfers, `Image.addLocalDir`) need the same bundler
setup the 0.x SDK needed in a browser (Node polyfills such as
`vite-plugin-node-polyfills` for `Buffer`, `process` and `global`).

Exactly one of `apiKey` or `jwtToken` must be configured. `jwtToken` requires
`projectId`; the SDK exchanges that OIDC JWT lazily for a short-lived,
project-scoped platform token and reuses it for every request it makes.
Per-sandbox `accessToken` overrides are unchanged.

`connectionPoolMaxsize` is Node-only and sets keep-alive `http.Agent` /
`https.Agent` socket pool sizes for axios-backed SDK requests.

`otelEnabled` is opt-in. When enabled, each SDK HTTP request runs inside a
client span and injects W3C trace headers. The SDK does not install a tracer
provider or exporter; your application owns that setup. In Node the SDK loads
the optional `@opentelemetry/api` peer itself; in a bundled browser app pass
your own module so the bundler can resolve it:

```ts
import * as otel from '@opentelemetry/api'
const daytona = new Daytona({ otelEnabled: true, otelApi: otel })
```

### Regions and safe create retries

Set `DAYTONA_V2_API_URL` only when you need a non-default v2 API endpoint. `region` on each `create*()` chooses an ID returned by `GET /regions`.
The response identifies the default region and lists region names,
availability, and published API URLs. Omit `region` to use the default.
Commands and files target the selected sandbox. When you select a regional
API endpoint, use resources available through that endpoint.

A region that is not currently available for creates throws
`REGION_UNAVAILABLE` (409). Image creates require a specific region; a
region covering too broad an area is rejected with 400.

Every `create*()` sends an idempotency key: the one you pass as
`idempotencyKey`, else one derived from `name` (next section), else a random
one per invocation. If the create request times out or loses its connection,
`SandboxCreateRequestTimeoutError` / `SandboxCreateConnectionError` exposes
`idempotencyKey`. Retry with that key and **the identical create options**; do
not start a fresh-key create to recover a lost response. A changed request
under the same key is a 409, never a silent replay of the original options.
Keys are scoped to the project and the API endpoint that served the
create, not checked across regions; changing regions requires a
new operation and key. Deleting a sandbox releases its key when deletion is
requested, so the same key (or the same name) creates a fresh sandbox afterwards.

### Names, get-or-create and `findByName()`

Sandbox names are **not unique** display metadata. Passing `name` to any
`create*()` also gives it permanent **get-or-create** behavior: repeating
`create('py312', { name: 'example-user' })` with the same options returns the
same sandbox. Its `accessToken` is returned only on the first call.

The SDK supplies a stable idempotency key derived from the name unless you
pass `idempotencyKey`. Python and TypeScript use the same derivation. The
name is also available as `labels.name`; a conflicting explicit `labels.name`
is rejected. The get-or-create rules are:

- **Region-scoped.** Creates through different regional API endpoints can
  return different sandboxes; use the same region and endpoint for a given
  end user's retries.
- **Exact.** `create('py312', { name: 'example-user', resources: { cpu: 2 } })` followed by
  `create('py312', { name: 'example-user', resources: { cpu: 8 } })` is a 409, not a silent replay of
  the 2-CPU sandbox. Change the options and you need a new name or an explicit
  key.
- **Released on delete.** Delete the sandbox and `create(…, { name: 'example-user' })`
  makes a new one.

`findByName(name, { region })` returns **all** sandboxes carrying that exact
`name` label, newest first, as an array (names are not unique);
`destroyed`/`destroying` ones are left out. This is the explicit exception to
the SDK's one-page pagination rule. It searches in the same region as a create
with that `region`. A new sandbox becomes searchable shortly after its create, so a lookup right after a
create can miss it — the get-or-create below stays safe, because the repeat
create derives the same key and returns the same sandbox. A deployment
without search raises `SearchNotConfiguredError` (501). Together:

```ts
const [existing] = await daytona.v2.findByName('example-user')
const sandbox = existing ?? (await daytona.v2.create('py312', { name: 'example-user' }))
// Two callers racing on an empty result still get ONE sandbox: both creates
// use the same name and options.
```

## What you get

### Follow build logs

Image submission returns immediately; its `onLogs` callback reports submission
progress, not build output. Follow that build explicitly:

These two snippets target **Node.js** (`process.stdout`). In a browser, replace
`onLogs` with a callback that appends each chunk to your UI. For a CLI that handles
slow pipes and early reader closure, use the `follow-build-logs` SDK example.

```ts
import { BuildLogInterruptedError, ImageBuilder } from '@daytona/sdk/v2'

const image = await daytona.v2.images.build(
  ImageBuilder.fromRegistry('python:3.12').runCommands('python -V'),
  { name: 'my-worker' },
)
const controller = new AbortController()
try {
  const result = await daytona.v2.images.followBuildLogs(image.id, {
    onLogs: chunk => { process.stdout.write(chunk) },
    timeoutSeconds: 600, // zero/omitted adds no observation deadline
    signal: controller.signal,
  })
  console.log(result.status, result.reason, result.outputComplete)
} catch (error) {
  if (!(error instanceof BuildLogInterruptedError)) throw error
  console.error('Observation interrupted; last checkpoint:', error.cursor)
}

// For an existing sandbox's build:
await (await daytona.v2.get('sandbox-id')).followBuildLogs({
  onLogs: chunk => { process.stdout.write(chunk) },
})
```

`onLogs` is required. Optional `onEvent` receives a discriminated `BuildLogEvent`
union (`event.type` selects the variant), including states, gaps and source
unavailability. Without it, these diagnostics appear as `[build logs: …]`
messages in `onLogs`. Both callbacks may return promises and are awaited before
checkpointing. Build failure is a result, not a connection error.
`outputComplete` describes the requested cursor-to-end interval; gaps keep it
false even after reconnection. `sandbox.buildLogs()` remains a one-shot string
of currently retained output.
One-shot reads reject responses exceeding 2 MiB of received body bytes with
`BUILD_LOG_PROTOCOL_ERROR`, rather than silently truncating them.

A deadline in a state event is server execution metadata, not a client abort:
the follower still drains final output and waits for `done` with reason
`deadline`. Your `timeoutSeconds` and `signal` remain independent observation limits.
`requestTimeoutSeconds` bounds discovery, one-shot reads, and error-response
bodies, including after headers arrive; it does not limit a healthy live log
stream.

Pass `cursor` to resume a saved checkpoint. Automatic reconnects preserve
partial UTF-8 characters; a fresh follower starting mid-character can emit a
replacement character. Persist consumed output and its event cursor together
if you need application-level restart safety. Callback exceptions stop without
retry, since their side effects may already have happened. A timeout or abort
closes the observation, not the build. Source changes never restart from zero
automatically. Unavailable output is reported as an `unavailable` event;
queued builds continue to be observed until completion or a deadline.
Cancellation stops awaiting a pending callback but cannot undo its side effects;
make asynchronous callback work cancellation-aware when needed.

### Surface overview

- **`daytona.v2` client** — sandbox lifecycle (`create`, `get`, `list`, `iter`,
  `start`, `stop`, `pause`, `resume`, `delete`) plus platform
  services: `images`, `snapshots`, `volumes`, `secrets`, `reservations`,
  `macModels`.
- **Bucket volumes** — to mount an S3 bucket, create a bucket volume in the
  dashboard or with `POST /volumes`, then attach it when creating a sandbox
  through the `volumes` option.
- **Sandbox search** — `list({ cursor, limit })` returns one page without
  filters; `iter({ cursor, limit })` traverses those pages. Use `search()` for
  eventually consistent, all-region filtering by `states`, `sandboxClass`,
  `region`, `labels`, resource/timestamp bounds, `sort`, `order`, and other
  search options. `states` accepts a public state or array, for example
  `{ states: ['started', 'paused'] }`; `unknown` is not searchable.
  Timestamp filters are half-open:
  `createdStartAt` / `createdEndAt`, `updatedStartAt` / `updatedEndAt`, and
  `lastActivityStartAt` / `lastActivityEndAt`; each start is inclusive and
  each end is exclusive.
- **Network policy** (Linux and macOS sandboxes; the API rejects unsupported
  sandbox configurations) — create-time `networkPolicy` on
  `create`, `createFromBuild` and `createMacos` (a full-snapshot restore and `sandbox.fork()` inherit the
  source sandbox's policy), plus
  `sandbox.getNetworkPolicy()`, `sandbox.updateNetworkPolicy(networkPolicy, { ifMatch })`, and
  `sandbox.resetNetworkPolicy({ ifMatch })` over `GET|PUT /sandboxes/{id}/network-policy`.
  Responses expose desired vs applied policy, the latest quoted ETag, pending
  or failed apply state, and the `appliedStartId` of the sandbox start that
  received the active policy. Writes are full
  replacement only; reset is an explicit JSON `null`; the SDK does not auto-GET
  or auto-retry around stale `If-Match`. Domain allow-lists imply default-deny:
  omitting `blockAll` while setting `allowDomains` lets the API normalize it to
  `true`, while an explicit `blockAll: false` is rejected locally with
  `NETWORK_POLICY_DOMAINS_REQUIRE_BLOCK_ALL`. `blockAll: true` with
  `allowCidrs` and no `allowDomains` is the CIDR-only mode, which is **not a
  DNS exfiltration boundary**: DNS A lookups can include any name, but only
  answers inside `allowCidrs` come back, so a sandbox can
  carry data out in the names it looks up. When lookups must be contained, set
  `allowDomains` — `allowDomains: []` resolves no tenant name (a domain list,
  even an empty one, also denies TCP 80/443 to `allowCidrs` unless the name is
  listed); `blockAll: true` with no allow list refuses every lookup except
  platform-reserved names. Unsupported sandbox configurations return
  `UNSUPPORTED_OPERATION`.
- **Runtime-managed sandbox secrets** (supported Linux sandbox configurations)
  — `sandbox.getSecrets()` and `sandbox.replaceSecrets([...], { ifMatch })` over
  `GET|PUT /sandboxes/{id}/secrets`.
  Responses expose desired secret names, desired/applied revisions, the quoted
  ETag, the apply `status` (`pending`/`applied`/`failed`) with its `error`, and
  capture metadata. Writes are full replacement only,
  use caller-supplied `If-Match`, and never send or return secret values.
- **`Sandbox`** — a serializable handle with `fork()`, `wait()` and
  the `process`, `fs`, `git`, `interpreter`, `pty`, `ports`, `volumes`, `lsp`,
  `docker` and `computerUse` namespaces. Sandbox handles do not need explicit
  disposal. `autoDelete` defaults to `false`; pass `autoDelete: true` to a
  create, `get()` or `fork()` call to delete the sandbox on `await using` scope exit
  (explicit resource management: Node 24+, or TypeScript 5.2+ downlevelling;
  on Node 22 call `[Symbol.asyncDispose]()` yourself).
  `sandbox.toJSON()` returns a `SandboxHandleJSON` snapshot without credentials
  or URLs, exactly what the Python SDK's `to_json()` writes for the same
  sandbox (timestamps kept as the server's strings). Reconnect using
  `await daytona.v2.get(info.sandboxId)`.
  The read-only `sandbox.accessToken` getter is the sandbox-scoped bearer the
  handle currently authenticates with: from create, a caller override, or a
  minted token. It is `undefined` until known and is never serialized.
  Sandbox metadata is read-only: use methods such as `setLabels()` to update it
  and `refreshData()` to reload it. Sandboxes expose `labels`, `cpu`, `cpuMax` (cores; 1 core =
  2 vCPUs; a macOS core is one Mac CPU), `memory`,
  `memoryMax`, `disk`, `diskMax`, `sandboxClass`, `region`,
  `persistent`, `spot`, `spotEvictedAt`,
  `lifecyclePolicy`, `forkedFrom`, `user`, and `error` (network policy is read through `getNetworkPolicy()`).

Resource models use readonly camelCase fields: for example, images expose
`sandboxClass`, `layerCount` and `availableIn`; snapshots expose `sandboxClass`
and `memoryBytes`; volumes expose `sizeGib` and `usedBytes`. Closed SDK
enums map unrecognized values to `unknown` and preserve the original in a
`raw…` sibling, such as `sandbox.rawSandboxClass`. Timestamps are RFC 3339
strings; budget period dates are `YYYY-MM-DD` strings.

For an automatically deleted temporary sandbox:

```ts
{
  await using sandbox = await daytona.v2.create('py312', { autoDelete: true })
  await sandbox.wait({ timeoutSeconds: 120 })
  console.log((await sandbox.exec('python -V')).output)
} // waits for sandbox deletion
```

### Sandbox labels and entrypoint logs

Use `sandbox.setLabels(labels)` to replace the full label set. Pass `{}` to
clear all labels. It resolves to the applied label map.

```ts
const labels = await sandbox.setLabels({ team: 'sdk', env: 'ci' })
console.log(labels)
```

`sandbox.entrypointLogs()` returns the sandbox entrypoint log as a plain string.

Use `sandbox.updateEnv(env, { unset })` to update the sandbox
environment inherited by processes, PTYs and sessions started afterwards.
Running processes are unchanged; `unset` removes only values previously set
through `updateEnv()`, not image-defined environment variables.

### Attach and pull volumes

```ts
const volume = await daytona.v2.volumes.get('data', { create: true })
const sandbox = await daytona.v2.create('py312', {
  volumes: [{ volumeId: volume.id, mountPath: '/data' }],
})
const result = await sandbox.volumes.pull(volume.id, {
  requestId: 'refresh-42', // retain this caller-owned ID for retries/checks
  timeoutSeconds: 5, // 0–30 seconds; default 30; fractions are allowed
})
```

`volumes` is also accepted by `createFromBuild` and `createMacos`. Each
`VolumeMount` names a `volumeId`, `mountPath`, optional `subpath` and optional
`readOnly`. Only bucket volumes support `readOnly: true`. macOS accepts
read-write disk volumes only, without `persistent: true`.

Create a disk volume with `daytona.v2.volumes.create({ name: 'data', sizeGib: 10 })`;
delete one by name or ID with `daytona.v2.volumes.delete(volume.id, { idOnly: true })`.

Pulling an attachment is separate from the `daytona.v2.volumes` management
service. IDs contain 1–128 ASCII letters, digits, underscores or hyphens.
The result has `requestId`, `status` (`pulled`, `unchanged` or `pending`) and
nullable `manifestId`. Unknown statuses map to `unknown` with the original
value in `rawStatus`.

Zero timeout submits a new request, or returns a known request's last
outcome without retrying it; expiry does not cancel accepted work. Retry a
`pending` result, or a caught `ServiceUnavailableError` / `RateLimitError`,
with the **same requestId** and a timeout above zero. The SDK never retries
automatically or generates an identity. Rate-limit errors expose `requestId`
and `retryAfterMs` when supplied by the API.

Availability depends on API support and may return
`ServiceUnavailableError`. Replay is scoped to the current attachment
session. `unchanged` does not assert that the local attachment has no edits.

### Create sources

Fast path: create from a registered image name or `img_…` id.

```ts
await daytona.v2.images.pull('python:3.12', { name: 'py312' })
const linux = await daytona.v2.create('py312', { waitSeconds: 60 })
const snapshot = await daytona.v2.createFromSnapshot('worker-after-setup', { waitSeconds: 60 })
```

Slow path: build first, then create.

```ts
const builder = ImageBuilder.fromRegistry('python:3.12').pipInstall('numpy').workdir('/workspace')
const sandbox = await daytona.v2.createFromBuild(builder, {
  resources: { cpu: 2, memory: 4 },
  buildTimeoutSeconds: 600,
  waitSeconds: 60,
})
```

`createFromBuild()` always waits for the image build before creating a sandbox.
`buildTimeoutSeconds` bounds only the build (omitted or `0` means no deadline);
a failed build throws `ImageBuildFailedError`, and an expired build budget
throws `RequestTimeoutError`. The sandbox then gets the full, independent
`waitSeconds` budget.

`waitSeconds` is the create wait budget in whole seconds (default 60).
`waitSeconds: 0` returns as soon as the create is accepted. Every `create*()`
returns the handle when its wait expires or a terminal state is reached;
check `sandbox.state` and `sandbox.error` before doing work. Request failures
still throw. `createTimeoutSeconds` is the server-side queue/boot budget in
seconds; `-1` asks the server to apply its maximum.

Linux sizing uses only `resources: { cpu, cpuMax, memory, memoryMax, disk, diskMax }`.
CPU is in cores; memory and disk are in GiB and must convert exactly to whole
MiB. Readback preserves exact GiB values. `createFromSnapshot()` inherits the
snapshot's configuration and accepts no resources, CPU selectors, environment,
volumes, secrets or network policy options.

`images.get(id, { wait: 'ready', timeoutSeconds: 60 })` and
`snapshots.get(id, { wait: 'ready', timeoutSeconds: 60 })` perform a bounded
readiness wait in seconds, repeating holds of at most 60 seconds until ready,
failed, or out of budget, then returning the last observed artifact. An early
non-terminal response pauses up to one second before retrying. Omitted or zero
`timeoutSeconds` sends one hold using the server's default.
`sandbox.captureImage({ name, idleExpirySeconds })` captures an
image from a sandbox.

### macOS sandboxes

A macOS sandbox runs on a dedicated Mac reserved by your organization. Create
one with `daytona.v2.createMacos(image, options)` (`POST /sandboxes/macos`).
`create()` makes Linux sandboxes. `image` is a macOS image name
such as `base-macos`. Set `resources: { cpu, memory, disk }`: `cpu` is an
integer number of physical cores (at least 4), `memory` is GiB (at least 4),
and `disk` is GiB (at least 80, including macOS). Omitted values use the
model's default sandbox size.
The resulting sandbox reports its assigned resources (`sandbox.memory` and
`sandbox.disk` in exact GiB, `sandbox.reservationId` for the reservation).
`name`, `idempotencyKey`, `waitSeconds` and
`createTimeoutSeconds` work as on `create()`: a named create derives its
idempotency key from the name and stamps `labels.name`.

Creating a macOS sandbox, starting a stopped one, reserving a Mac or renewing a
reservation, and turning a reservation's auto-renew on require macOS access
to be enabled for your organization: otherwise the API answers `403`
`FEATURE_NOT_ENABLED` (thrown as `FeatureNotEnabledError`). Reading sandboxes
and reservations, turning auto-renew off, `pause()` and `resume()` are not gated.

Find available regions and their regional API endpoints with `GET /regions`.
Use `daytona.v2.macModels.catalog({ region: '<region>' })` or
`daytona mac-model list --region '<region>'` to discover available Mac models
and current prices. Replace `<region>` and `<model>` in the examples with
values from these catalogs. When using a regional API endpoint, set
`DAYTONA_V2_API_URL` before creating the client. Against an API that does not
serve the region, `createMacos` throws the API's `ConflictError`; the SDK
does not send the create anywhere else.

A sandbox without a size uses the model's default sandbox size:

```ts
await daytona.v2.createMacos('base-macos')
// Or choose resources explicitly:
await daytona.v2.createMacos('base-macos', { resources: { cpu: 4, memory: 8, disk: 100 } })
```

A reservation reserves one physical Mac of a named model. Set CPU, memory,
and disk sizes when creating each sandbox. `daytona.v2.macModels.list()` lists
each model's resources, sandbox disk room, `maxSandboxes`, and `defaultSize`.
Omitted sizes use half of each resource on a model that runs two sandboxes,
or all of it on a model that runs one. Disk defaults round down to a whole GiB.
Reserve a Mac, then create a sandbox using the model's default size:

```ts
const reservation = await daytona.v2.reservations.create({ region: '<region>', model: '<model>' })
await daytona.v2.createMacos('base-macos', { region: '<region>', reservation: reservation.id, persistent: true })
```

Stop and pause need a persistent sandbox (`persistent: true`). Stop keeps the disk on the same Mac, and start and resume use that Mac. Delete erases the disk. At reservation end, a persistent sandbox's disk is saved as an anonymous image before the Mac is erased, and an ephemeral sandbox's disk is erased unsaved; a lost Mac saves persistent disks if it comes back within 7 days. If a Mac restarts before it is declared lost, a running persistent sandbox comes back stopped with its disk, a running ephemeral sandbox is lost, and stopped and paused sandboxes are unchanged. Create a sandbox from an image to continue on another Mac.

`reservation` picks the Mac reservation. Omitted, the sandbox goes on any reservation of your
organization in the region with room for its size, or, when none has room and
your organization's auto-reserve setting is on, the server buys a reservation of the
cheapest model that fits. `reservation: 'new'` always buys such a reservation for the
sandbox (`autoRenew` is accepted only here). A reservation ID targets that
reservation. Acceptance does not guarantee capacity; the sandbox can wait for room
or time out. Reservations are billed per 24-hour period; without `model`,
`reservations.create()` reserves the region's default model and never upgrades
an unavailable model to another one.

`daytona.v2.macModels.catalog({ region })` (`GET /mac-catalog`) shows, per
model, how many Macs a purchase can reserve now (`available`, advisory) and the
`price` of one reservation (absent when pricing is unavailable in the region).
`reservations.createBulk()` reserves several Macs of one model in one request:
all of them, or none if fewer are free, unless `minCount` accepts fewer. Every
purchase and renewal carries an idempotency key; pass your own so a retry after
a lost response neither reserves more Macs nor pays for a second period:

```ts
const catalog = await daytona.v2.macModels.catalog({ region: '<region>' })
const batch = await daytona.v2.reservations.createBulk({ region: '<region>', model: '<model>', count: 3, idempotencyKey: 'nightly-macs' })
console.log(`reserved ${batch.reserved} of ${batch.requested} Macs`)
await daytona.v2.reservations.renew(reservation.id, { idempotencyKey: `renew-${reservation.id}-2` })
```

The dashboard's desktop view opens the Mac's screen through your authenticated
dashboard session. Click once in the desktop before typing, because macOS
drops keys for the first few seconds of each connection. Customers cannot end
a Mac reservation before its 24-hour period.

Without a reservation to use, a macOS create throws `ConflictError` with
`code === 'NO_ACTIVE_RESERVATION'` (or `NO_FREE_SLOT` when no reservation has room
for the size; `MAC_DISK_FULL` when the reservations with room have no free disk
space; `AUTO_RESERVE_LIMIT_REACHED` when auto-reserve holds its maximum number
of reservations; or `NO_MAC_AVAILABLE` when buying a reservation, with `reservation: 'new'`
or through auto-reserve, finds no fitting Mac in the region). `MAC_DISK_FULL`
never buys a reservation. Starting a stopped macOS sandbox uses its own disk on the
same Mac and reservation; it never buys a reservation. Reservations report `model`,
`capacity`, `resourcesUsed`, `origin` (`explicit` or `auto`), `autoRenew`
and `renewalError`; `reservations.update(id, { autoRenew })` changes renewal
on a live reservation.

`pause()` and `resume()` work on persistent macOS sandboxes. `pause()` saves
the sandbox's memory on its Mac and waits for `paused`; `resume()` puts the
sandbox in `assigned` and waits for `started`. A paused sandbox keeps its Mac
slot, but pausing stops its reservation from auto-renewing: a reservation that
holds only paused sandboxes lapses at the end of its period.
`sandbox.macBootMode` reports how the macOS sandbox resumed; `sandbox.error`
provides additional details when resuming fails.

A macOS `fork()` copies the disk only: omit `memory` or pass `memory: false`
(`memory: true` answers `NOT_SUPPORTED_FOR_CLASS`). A fork of a running macOS
sandbox boots with a fresh machine identity; a fork of a paused or stopped
one remains `stopped`. A fork needs sufficient reservation capacity and disk
space; insufficient resources can produce `NO_FREE_SLOT` or `MAC_DISK_FULL`.

### Processes

`sandbox.exec()` is the root alias of `process.run()`. `process.run()` awaits a
command; `process.start()` returns a detached
`ProcessHandle` you can serialize (`toJSON()`) and reconnect to
(`process.connect()`). Handles offer cursor-replayable `logs()`, `streamLogs()`
with resume, `wait()`, `writeStdin()` / `closeStdin()` for exec and pty handles,
`delete()` (stopping it first if running; a running interpreter execution, or a session
command that does not stop within 5 s, is refused: delete its context or
session), signal-aware `kill()` with
 escalation, `resize()` and a websocket `attach()`. `timeout=0` waits
unbounded. `timeout` is the caller
wait budget **(milliseconds)**; `executionTimeout` is the process deadline
**(milliseconds)** for
server-managed runs that support one. Rehydrated `kind:'code'` handles remain replay/control
handles (`logs()` / `wait()` / `kill()`), not a way to recover a parsed
`InterpreterResult` on another worker. `pty.list()` is the PTY-only view of
`process.list({ kind: 'pty' })`, and `pty.delete(id)` kills then cleans up that
PTY record.

### Sessions

Create one persistent shell when commands need shared state. Commands run one
at a time, in order; `cd`, exports, and functions carry over. Set `cwd`, `env`,
`shell`, and `login` at creation, not on each command.

```ts
const session = await sandbox.process.createSession({ cwd: '/app', env: { CI: '1' } })
await session.run('export GREETING=hello')
const result = await session.run('echo "$GREETING"', { timeout: 10_000, executionTimeout: 30_000 })
const handle = await session.start('npm run dev')
await handle.kill({ signal: 'INT' }) // interrupts this command; the session survives
const info = await session.get() // includes the shell's current cwd
const reconnected = sandbox.process.connectSession(session.toJSON()) // or session.id
await reconnected.delete() // ends the shell and removes its commands
```

`session.run()` returns `RunResult`; `session.start()` returns `ProcessHandle`.
`timeout` is the caller's wait budget; `executionTimeout` stops the command,
not the session (a command that has not stopped 5 seconds after its timeout
ends the session instead of blocking it). Both are milliseconds. `await using session = ...` deletes
the session on scope exit. List sessions with `process.list({ kind: 'session' })`
or commands with `process.list({ sessionId: session.id })`. Plain `process.run()`,
`process.start()`, and `sandbox.exec()` do not accept `sessionId`.
Sessions survive pause/resume but not sandbox stop/start; reconnecting never
creates a replacement for a missing or ended session.
In dash, BusyBox `sh` and mksh, an interrupt leaves only the
innermost shell function or sourced file; bash and zsh leave the whole command
(bash older than 4.4, macOS's `/bin/bash`, only when no DEBUG trap is set).

### Files

`readBytes` / `readText`, `writeBytes` / `writeText`, bulk `readMany` / `writeMany`,
`remove()`, `grep()` (file contents) / `glob()` (file names), `watchDir()` for directory watches (sequential callback delivery, optional
overflow hook, exactly-once `onExit`, `waitForExit()` / `exitError`), and
`presignedDownloadUrl` / `presignedUploadUrl` for direct transfers. The
platform mints the link; `expiresInSeconds` (default 3600) must be between 1
and `SIGNED_FILE_MAX_TTL_SECONDS` (7 days) — links always expire.
`sandbox.rotateSigningKey()` retires every pre-signed URL minted so far (they
stop working within ~30 s); new URLs use the new key, which is never returned
to the client. Paginated
services (`daytona.v2`, `images`, `snapshots`, `volumes`, `secrets`, `reservations`)
return one page
from `list()` and can lazily drain all pages with `iter()`; `ports.listEndpoints()`
uses `iterEndpoints()`, while non-pageable `ports.list()` and `listContexts()`
stay one-shot. `findByName()` is the explicit exception: it returns all matches.

`writeBytes()`, `writeText()` and `writeMany()` accept `atomic: true` to write via a
temporary file + rename when the sandbox supports it. For `writeBytes()` and
`writeText()` a filesystem that cannot do that rejects with
`AtomicUploadUnsupportedError`; `writeMany()` keeps its partial-success
contract instead — the affected entries appear in the returned `errors` array
as failure messages naming the file and the atomic-rename limitation, while the
rest of the batch is written.

`uploadFile(localPath, remotePath, { onProgress, signal, atomic })` and
`downloadFile(remotePath, localPath, { onProgress, signal })` stream directly
between disk and the sandbox instead of buffering the whole file in memory.
They are Node.js-only and throw `NotSupportedError` in browser builds.
Downloads write to an exclusive random temp file in the destination directory
and rename it over the destination on success; cancellation or failure removes
that exact temp file. `signal` aborts the transfer with
`TransferCancelledError`.

### Interpreter

`sandbox.runCode()` is the root alias of `interpreter.run()`. Runs without an
explicit context share one per-language interpreter state inside the sandbox,
and that state's `env` is fixed when it is first created. Pass `isolated: true`
for a throwaway run or use `createContext()` / `connectContext()` /
`listContexts()` for an independent named context. If an explicit named context vanished,
`interpreter.run(..., { sessionId })` / `context.run()` surface the typed
`SessionNotFoundError`; recreate the context yourself and decide what to rerun.
`timeout` is enforced client-side **(milliseconds)**. Results are split into
`results[]`, `logs{stdout,stderr}`, `error`, and `process`. That rich result
shape comes from the interpreter run path itself; reconnecting a serialized
code-process handle later does not recreate it.

### Git

`clone()`, `pull()` and `push()` use a dedicated **30-minute** remote-operation
deadline by default, and each accepts a positive per-call `timeout`
override **(milliseconds)**.

`git.clone(url, { path, branch, commitId, depth, insecureSkipTls, username,
password })` adds optional shallow-clone depth and per-call TLS verification
skipping. `git.pull(path, { username, password, branch, remote })` and
`git.push(path, { username, password, branch, remote, setUpstream })` forward
the additional remote/branch controls to the sandbox.

`git.commit(path, message, { author, email, allowEmpty })` forwards
`allowEmpty: true` when you need an empty commit.

### Ports, LSP, computer use

`ports` owns both listener discovery and preview exposure:
`list()`, `isInUse(port)`, `expose(port, { public })`,
`previewLink(port)`,
`signedPreviewLink(port, { expiresInSeconds })`,
`expireSignedPreviewLink(port, token)`, paginated `listEndpoints(options)`, and
`iterEndpoints(options)`.
To skip the preview warning page when a headless browser opens a preview URL,
send `{ [PREVIEW_WARNING_SKIP_HEADER]: PREVIEW_WARNING_SKIP_HEADER_VALUE }` (both
exported from `@daytona/sdk/v2`) with its requests.
`lsp.start(languageId, pathToProject)` returns an `LspServer` handle with
`openDocument()`, `closeDocument()`, `completions()`, `documentSymbols()`,
`workspaceSymbols()` and `stop()`.
`computerUse` stays lifecycle-flat (`status()`, `start()`, `stop()`) and
splits actions into `service`, `mouse`, `keyboard`, `screen`, `display`,
`a11y` and `recording`. `screen.screenshot()` captures the whole screen or a
`region`; any of `format`, `quality` or `scale` returns a compressed image.

### Docker

Docker Engine starts automatically in sandboxes whose image ships it; there
is no create option. `sandbox.docker.status()` returns its state (`ready`,
`starting`, `failed`, `not_present`, `disabled`, `unmanaged`, `deferred` or
`unsupported`), who owns it, a `reason`, and the `processId` whose logs hold
Docker's output. `sandbox.docker.waitUntilReady({ timeoutSeconds })` polls until the
state is `ready` and resolves with the status. It throws `DockerNotReadyError`
(`error.status`) as soon as Docker settles in a state that cannot become
`ready` (`failed`, `not_present`, `disabled`, `unmanaged`, `unsupported`, or
a non-recoverable `deferred` reason), and
`DockerWaitTimeoutError` when the budget **(seconds, default 60)**
runs out while Docker is still `starting` or `deferred`, or a status request
is still unanswered at the deadline. `timeoutSeconds: 0` waits without a deadline. A
Docker failure never makes the sandbox itself unusable.

Set `DAYTONA_DOCKER_START` in the sandbox env to choose how Docker starts:
`auto` (default; start the image's `dockerd` when it ships one), `entrypoint`
(the image's own entrypoint starts Docker) or
`off` (Docker is not started; state `disabled`). If Docker status is unavailable,
`status()` and `waitUntilReady()` throw `NotFoundError` (404).

Some environment variable names are reserved for the platform, among them
every `DAYTONA_*` name other than `DAYTONA_SANDBOX_TOKEN`,
`DAYTONA_API_URL`, `DAYTONA_OTEL_ENDPOINT` and `DAYTONA_DOCKER_START`. A
sandbox `env` that sets a reserved name fails the create with `BadRequestError`
(400) naming the key; the same rule applies to the keys an
`updateEnv()` call sets or unsets.

### Waiting for lifecycle states

Use `sandbox.wait({ until: 'started', timeoutSeconds: 120 })` to wait for a
public lifecycle state. `until` also accepts an array of states. The default
is `started`, with a 60-second budget; `timeoutSeconds: 0` waits without a
deadline. Timeout throws `RequestTimeoutError`; a terminal state reached
first throws `SandboxTerminalStateError`.

`start()`, `stop()`, `pause()`, `resume()` and `delete()` accept
`{ timeoutSeconds }` with the same default and unbounded form. They wait for
completion: `stop()` returns at `stopped`, and `delete()` returns at
`destroyed` (a sandbox disappearing during deletion also means completion).
Client methods accept a sandbox or its ID and the same options, for example
`daytona.v2.stop(sandbox.id, { timeoutSeconds: 120 })`. The server decides
which lifecycle actions the sandbox supports.

`fork({ waitSeconds: 60 })` has its own wait budget; fractions are allowed
and `0` skips waiting. Once their create or fork request succeeds, these calls
return the handle however the readiness wait ends, including a failed wait
read. Caller cancellation still propagates; a cancelled create may already
have created a sandbox. A raised create or fork means the request itself failed, except for
`SandboxCreateRequestTimeoutError` and `SandboxCreateConnectionError` (they
carry the idempotency key because a sandbox may exist), caller cancellation,
and an unexpected non-SDK error during the wait. A running Linux source produces a running fork; otherwise
the fork is paused if the source was paused and memory was retained, or
stopped. macOS forks copy only disk and settle at `started` or `stopped`.

After sandbox deletion completes, deleting its image or snapshot can still
throw `ConflictError` while references are being released. Handle that
conflict when cleaning up those resources.

## Errors

The root `DaytonaError` (from `@daytona/sdk`) catches errors from both
namespaces and types `statusCode`, `code`, and `source`. The v2 classes
(`import { NotFoundError, … } from '@daytona/sdk/v2'`) have the same names as
in the Python SDK: `BadRequestError`, `AuthenticationError` (401),
`ForbiddenError` (403), `NotFoundError`, `ConflictError`, `RateLimitError`,
`RequestTimeoutError` (408), `ClientTimeoutError` (the client's own request
timeout), `ConnectionError`, … They also expose `traceId` (v0 errors carry
none), and the status-level bases have code-specific subclasses so you can
catch as broadly or narrowly as you need. `code` and `source` are open
vocabularies: a code this release does not know is kept as sent and the error
is the class for its HTTP status; the `codes` namespace lists the known codes
per source (`codes.API_ERROR_CODES`, `codes.CODE_STATUS`,
`codes.isKnownErrorCode`, …). Constructors take the message plus an
`ErrorOptions` object (`statusCode`, `code`, `source`, `traceId`, `cause`, …),
so tests can build the errors your code handles.

```ts
import { DaytonaError } from '@daytona/sdk'

try {
  await daytona.v2.get('missing')
} catch (e) {
  if (e instanceof DaytonaError) console.log(e.statusCode, e.code)
}
```

A request refused because your organization has hit one of its limits (for
example the maximum number of concurrent sandboxes) throws
`LimitExceededError` — a `RateLimitError` that also carries the limit's name
and counts as `limit`, `current` and `max`. Other rate-limited requests keep
throwing plain `RateLimitError`.

## Versioning and releases

See `docs/VERSIONING.md` and `CHANGELOG.md` in the
[repository](https://github.com/daytona/clients).
