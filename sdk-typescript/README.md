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

The legacy package `@daytonaio/sdk` is published only from the 0.x line:
installing or upgrading it never gives you 2.x. Install `@daytona/sdk`.

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
// create() returns the handle even if its wait ended before the sandbox started
if (sandbox.state !== 'started') await sandbox.wait()
const result = await sandbox.exec('python -V')
console.log(result.exitCode, result.output)
await daytona.v2.delete(sandbox)
```

`images.pull()` waits until ready by default. Set `{ wait: false }` to skip
the readiness wait, or `{ timeoutSeconds: 120 }` for a 120-second budget
(omitted, `0` or a value above about 24.8 days is unbounded). A failed import throws
`ImageBuildFailedError` with the server's error message. `images.build()`
remains non-waiting.

The root client is the only way to build a client: `daytona.v2` and
`daytona.v0` are its namespace clients. `@daytona/sdk/v2` exports v2's resource
types, builders and errors, but not a client (`import { Daytona } from
'@daytona/sdk/v2'` fails); type a stored v2 client as `DaytonaV2Client` from
`@daytona/sdk`.

`daytona.v2` waits (`create`, `start`, `wait`, …) ride one WebSocket
per regional API endpoint, shared by the v2 clients in the process with matching connection settings
(API URL, API key or JWT identity, request timeout and response body cap) and
events settings (`sharedHub` and `events` on the config). Closing is optional: in Node an idle socket stays open
for the next wait but does not keep the process alive, so a script can simply
end; a runtime where the socket cannot be released from the event loop closes
it 10 s after the last wait instead, and `events.idleCloseSeconds` overrides
either (values above about 24 days clamp to that maximum). `await daytona.close()`
releases this client's reference at once; the shared socket closes when the
last client sharing it closes.
New requests on a stored v2 client or any of its handles after closing throw
`ClientClosedError` (`CLIENT_CLOSED`, source `SDK`, no HTTP status).
Accessing `root.v2` after `await root.close()` builds a fresh working client.
Error constructors that take `ErrorOptions` reject unknown option keys and a
`statusCode` that is not an integer number (or `undefined`) with `TypeError`.
A non-Cloud `DAYTONA_API_URL` emits a warning once per process: SDK 2.x ignores
this legacy variable. Use SDK 0.x for a custom API URL.

The root client reads `DAYTONA_API_KEY`, `DAYTONA_JWT_TOKEN`,
`DAYTONA_ORGANIZATION_ID` (scopes a JWT for the v0 namespace),
`DAYTONA_PROJECT_ID` (scopes a JWT for the v2 namespace), and
`DAYTONA_OTEL_ENABLED` from the environment when a field is omitted.
Environment values are trimmed, and a blank one counts as unset; values you
pass in the config are used as given, and an empty one (`apiKey: ''`, …)
throws `BadRequestError` instead of falling back to the environment.
`DAYTONA_V2_API_URL` selects another v2 API endpoint (a regional endpoint, as
`daytona region list` in the CLI shows them, or a test environment);
when it is unset the SDK uses its built-in default API URL. `daytona.v0`
always targets Daytona's v0 API URL. SDK 2.x never routes using `DAYTONA_API_URL`, the
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
arrives, and file transfers have no total limit but throw `ClientTimeoutError`
once no bytes move for that long (with `requestTimeoutSeconds: 0` they are not
bounded at all). `maxResponseBytes` (an integer; `undefined` or `null` keep the
1 GiB default, `0` means no limit, and any other value is a `BadRequestError`) caps every response body the SDK holds in memory whole: a larger
body throws `PayloadTooLargeError` before it is read in full (an error response
keeps its status's error).

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
rejects `apiUrl`, and SDK 2.x (`daytona.v0` included) never routes with
`DAYTONA_API_URL` (a non-Cloud value only gets a one-time warning).

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

Set `DAYTONA_V2_API_URL` only when you need a non-default v2 API endpoint. `region` on each `create*()` chooses an ID listed by `daytona region list`.
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
same sandbox. Its `accessToken` is returned only on the first call. A blank
name is rejected before any request: omit `name` for a generated one.

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

`images.build()` returns the submitted `Image` without waiting for the build.
Follow its output with `images.followBuildLogs(image.id, { onLogs })`, or just
wait for the result with `images.get(image.id, { wait: 'ready', timeoutSeconds })`:

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
checkpointing. Build failure is a result, not a connection error. An event
kind a later API adds is skipped (it does not move the cursor). An HTTP error of
the first request, such as `NotFoundError` for an unknown image, is thrown as
that typed error rather than as `BuildLogInterruptedError`.
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
- **Bucket volumes** — to mount an S3 bucket you own, create a bucket volume
  for it in the dashboard or with `daytona volume create NAME --type bucket
  --bucket BUCKET_NAME --access-key-secret ACCESS_KEY_SECRET
  --secret-key-secret SECRET_KEY_SECRET` (the last two name project secrets
  holding the bucket's keys), then attach it when creating a sandbox through
  the `volumes` option.
- **Sandbox search** — `list({ cursor, limit })` returns one page without
  filters; `iter({ cursor, limit })` traverses those pages. Use `search()` for
  eventually consistent, all-region filtering by `states`, `sandboxClass`,
  `region`, `labels`, resource/timestamp bounds, `sort`, `order`, and other
  search options. `states` accepts a public state or array, for example
  `{ states: ['started', 'paused'] }`; `unknown` is not searchable.
  `region` takes region IDs; a region that contains other regions (a geo
  group, or `earth`) matches every region inside it, and an unknown region is
  a `BadRequestError`. Timestamp filters are half-open:
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
  `NETWORK_POLICY_DOMAINS_REQUIRE_BLOCK_ALL`. A non-empty `allowCidrs` needs
  `blockAll: true` or `allowDomains`: without them the policy is open and
  ignores `allowCidrs`, so the SDK refuses it locally. `blockAll: true` with
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
  `sandbox.toJSON()` returns a `SandboxHandleJSON` (exported from
  `@daytona/sdk/v2`) snapshot without credentials or URLs, exactly what the
  Python SDK's `to_json()` writes for the same sandbox (timestamps kept as the
  server's strings). Reconnect with `await daytona.v2.get(...)` using the
  sandbox id it carries.
  The read-only `sandbox.accessToken` getter is the sandbox-scoped bearer the
  handle currently authenticates with: from create, a caller override, or a
  minted token. It is `undefined` until known and is never serialized.
  Sandbox metadata is read-only: use methods such as `setLabels()` to update it
  and `refreshData()` to reload it. Sandboxes expose `labels`, `cpu`, `cpuMax` (cores; 1 core =
  2 vCPUs; a macOS core is one Mac CPU), `memory`,
  `memoryMax`, `disk`, `diskMax`, `sandboxClass`, `region`,
  `persistent`, `spot`, `spotEvictedAt`,
  `lifecyclePolicy`, `forkedFrom`, `user`, and `error` (network policy is read through `getNetworkPolicy()`).
  A handle from `create*()` or `fork()` whose wait did not read the sandbox
  (`waitSeconds: 0`, or a wait that ended early) holds only what the create
  response carries: fields it does not report are `undefined` (unknown),
  `labels` included, until `refreshData()` reads them; `state` is `'unknown'` before a
  status is known. Absent values are `undefined` throughout the SDK's models,
  never `null`.

Resource models use readonly camelCase fields: for example, images expose
`sandboxClass`, `layerCount` and `availableIn`; snapshots expose `sandboxClass`
and `memoryBytes`; volumes expose `sizeGib` and `usedBytes`. Closed SDK
enums map unrecognized values to `unknown` and preserve the original in a
`raw…` sibling, such as `sandbox.rawSandboxClass`. An image's `source` is an
`ImageSource` (exported from `@daytona/sdk/v2`) told apart by `source.type`; a
source kind a later API adds reads as its `unknown` member, with the reported
kind in `rawType`, never as an error. Timestamps are RFC 3339
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

Use `sandbox.setLabels(labels)` to replace the label set. Pass `{}` to clear
every label but `name`. It resolves to the applied label map. The reserved
`name` label that `findByName()` matches is kept unless the new labels set it:
the sandbox is read first to learn it (one more request, not atomic with the
write), and it cannot be removed. A label key must not be empty:
`setLabels()`, the `create*()` methods and `search({ labels })` refuse one with
`BadRequestError` before any request.

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
if (sandbox.state !== 'started') await sandbox.wait()
const result = await sandbox.volumes.pull(volume.id, {
  requestId: 'refresh-42', // retain this caller-owned ID for retries/checks
  timeoutSeconds: 5, // 0–30 seconds; default 30; fractions are allowed
})
```

`volumes` is also accepted by `createFromBuild` and `createMacos`. Each
`VolumeMount` names a `volumeId`, `mountPath`, optional `subpath` and optional
`readOnly`. Only bucket volumes support `readOnly: true`. macOS accepts
read-write disk volumes only, without `persistent: true`.

`mountPath` is an absolute path with no `..` component. The API normalizes it
(repeated slashes, `.` components and a trailing slash are removed) and
refuses `/`, any path on or beneath `/bin`, `/boot`, `/dev`, `/etc`, `/lib`,
`/lib32`, `/lib64`, `/mnt/scratch`, `/overlay`, `/proc`, `/sbin`, `/sys` or
`/usr`, and one with a control character, with `BadRequestError` (400).

Create a volume with `daytona.v2.volumes.create({ name: 'data' })`; the server
chooses its type unless you pass `type: 'disk'` (sized with `sizeGib`) or
`type: 'bucket'`. Delete one by name with `await daytona.v2.volumes.delete('data')`, or by ID with
`await daytona.v2.volumes.delete(volume.id, { idOnly: true })`. `{ idOnly: true }`
accepts an ID only and turns off the name lookup.

Pulling an attachment is separate from the `daytona.v2.volumes` management
service. IDs contain 1–128 ASCII letters, digits, underscores or hyphens.
The result has `requestId`, `status` (`pulled`, `unchanged` or `pending`) and
an optional `manifestId` (`undefined` when there is none). Unknown statuses map to `unknown` with the original
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

`ImageBuilder.env()` values are literal: the image gets exactly the string you
pass (`$PATH` is not expanded); a blank value, or one with a control character
(such as a newline), is rejected.

`createFromBuild()` always waits for the image build before creating a sandbox.
`buildTimeoutSeconds` bounds only the build (omitted, `0` or a value above about
24.8 days means no deadline);
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
readiness wait in seconds, repeating holds of at most 60 seconds until the
artifact is `ready`, `failed` or `error`, and return it for you to check its
status. A budget that runs out first throws `RequestTimeoutError`; a budget
above the timer maximum (about 24.8 days) waits without a deadline. An early
non-terminal response pauses up to one second before retrying. Omitted or zero
`timeoutSeconds` sends one hold of the server's default length and returns what
the server reports.
`sandbox.captureImage({ name, idleExpirySeconds })` captures an
image from a sandbox, and `sandbox.createSnapshot(name)` a snapshot. Both need
`write:sandboxes` in addition to `write:images` / `write:snapshots`; without it
they throw `ForbiddenError` (403).

### macOS sandboxes

A macOS sandbox runs on a dedicated Mac reserved by your organization. Create
one with `daytona.v2.createMacos(image, options)` (`POST /sandboxes/macos`).
`create()` makes Linux sandboxes. `image` is a macOS image name
such as `base-macos`. Set `resources: { cpu, memory, disk }`: `cpu` is an
integer number of physical cores (at least 4), `memory` is GiB (at least 4),
and `disk` is GiB (at least 80, including macOS). Omitted values use the
model's default sandbox size (`defaultSize`). A create from a captured image
that omits `disk` gets the image's disk size, and a smaller `disk` is refused.
The size is fixed at creation.
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

Find available regions and their regional API endpoints with `daytona region list`.
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
each model's resources, sandbox disk space, `maxSandboxes`, and `defaultSize`,
the size that omitted values use. The disks and disk volumes of the sandboxes
on one Mac at once must fit its `sandboxDiskMib`.
Reserve a Mac, then create a sandbox using the model's default size:

```ts
const reservation = await daytona.v2.reservations.create({ region: '<region>', model: '<model>' })
await daytona.v2.createMacos('base-macos', { region: '<region>', reservation: reservation.id, persistent: true })
```

Stop and pause need a persistent sandbox (`persistent: true`), which keeps its
disk and machine across stop and start, and its memory across pause and
resume. A start can run on any Mac of the organization; after a start on a
different Mac, iCloud asks the user to sign in again. Delete erases the disk.
A reservation end or a Mac failure stops a persistent sandbox that has started
and never deletes it; a Mac failure can lose its most recent writes. An ephemeral sandbox ends
with its reservation, or with its Mac when that Mac fails.

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
all of them, or none if fewer are free or fit under the organization's Mac
reservation limit, unless `minCount` accepts fewer. A purchase at that limit
throws `LimitExceededError` (a `RateLimitError`) with `limit ===
'max_mac_reservations'`. A bulk purchase throws it when fewer than `minCount`
fit under the limit, which is checked first, and `ConflictError` with code
`NO_MAC_AVAILABLE` when fewer than `minCount` Macs are free. Every purchase and
renewal carries an idempotency key; pass your own so a retry after a lost
response neither reserves more Macs nor pays for a second period:

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
or through auto-reserve, finds no fitting Mac in the region). Buying a
reservation when the organization holds its Mac reservation limit throws
`LimitExceededError` with `limit === 'max_mac_reservations'` instead.
`MAC_DISK_FULL` never buys a reservation. Starting a stopped macOS sandbox
(`start()`) uses its disk and size on any Mac of the organization; it throws
`NO_FREE_SLOT` when no Mac has space for its size and `MAC_DISK_FULL` when the
Mac with space is low on free disk space. With no Mac
reservation in the region and auto-reserve on, the start buys one, so it can
fail as a create that buys one does, `LimitExceededError` with
`limit === 'max_mac_reservations'` included. A start, `fork()` or
`captureImage()` throws `ConflictError` with `code === 'DISK_NOT_READY'` while
the sandbox's disk is not ready yet; retry later. Reservations report `model`,
`capacity`, `resourcesUsed`, `origin` (`explicit` or `auto`), `autoRenew`
and `renewalError`; `reservations.update(id, { autoRenew })` changes renewal
on a live reservation.
An organization over its Mac reservation limit, after a plan downgrade for
example, keeps every Mac until the end of the period it paid for, but the
reservations above the limit do not renew: their `autoRenew` turns off and they
end at the end of that period. `reservations.renew()` of such a reservation
throws `LimitExceededError` with `limit === 'max_mac_reservations'`.

`pause()` and `resume()` work on persistent macOS sandboxes. `pause()` saves
the sandbox's memory on its Mac and waits for `paused`; `resume()` puts the
sandbox in `assigned` and waits for `started`. A paused sandbox keeps its
memory on its Mac. With auto-renew on, it keeps its reservation renewing;
otherwise the sandbox is stopped, with its disk, before the reservation ends.
`sandbox.macBootMode` reports how the macOS sandbox resumed; `sandbox.error`
provides additional details when resuming fails.

A macOS `fork()` copies the disk only: omit `memory` or pass `memory: false`
(`memory: true` answers `NOT_SUPPORTED_FOR_CLASS`). A fork of a running macOS
sandbox boots with a fresh machine identity; a fork of a paused or stopped
one remains `stopped`.

### Processes

`sandbox.exec()` is the root alias of `process.run()`. `process.run()` awaits a
command and returns a `ProcessResult`: `stdout`, `stderr`, `output`, the log
frames it read them from (`logs`), and how the process ended (`reason`,
`exitCode`, `signal`). `exitCode` and `signal` are set only when the sandbox
reported them; `reason` is always set (`'unknown'` with the value on
`rawReason` for one this release does not know). `run()` reads the output back
from the process log, so its `keepLogs` takes `'on_exit_ttl'` (default) or
`'until_cleanup'`, not `'none'`. It returns no handle to write to, so it
refuses `stdin: 'open'`: use `start()` for a process that reads stdin.

`process.start()` (and `session.start()`) sends `keepLogs: 'on_exit_ttl'`
unless you pass another value: the sandbox keeps the record and log for a while
after the process exits; `'until_cleanup'` keeps them until the process is
deleted, and `'none'` keeps no log.

`process.start()` returns a detached `ProcessHandle` you can serialize
(`toJSON()`, a `ProcessHandleJSON` the Python SDK reads too) and reconnect to
on any worker (`process.connect(json or id)`); handles come from the SDK,
never from a constructor. Handles offer cursor-replayable `logs()`,
`streamLogs()` with resume, `wait()`, `writeStdin()` / `closeStdin()` for exec
and pty handles, `delete()` (stopping it first if running; a running interpreter
execution, or a session command that does not stop within 5 s, is refused:
delete its context or session), signal-aware `kill()` with escalation,
`resize()` and a websocket `attach()`. `streamLogs()` yields one
`StreamLogEvent` per server event of a supported type (an event of another
type is skipped), each carrying its resume `cursor`, and stops after `eof`
(`StreamLogFrameEvent`, `StreamLogStateEvent`, `StreamLogWarningEvent`,
`StreamLogEofEvent`, told apart by `type`); a stream error throws its typed
error. The sandbox ends the stream with `eof`, so an iteration that ends
normally has the whole log; a stream cut short, or one that ends without
`eof`, throws `ConnectionError`: call `streamLogs({ cursor })` again with the
last event's cursor. Every duration is in seconds: `timeoutSeconds` is the caller wait
budget (omitted or `0` waits unbounded), `executionTimeoutSeconds` the process
deadline for server-managed runs that support one (omitted or `0`: no
deadline), and
`kill({ escalateAfterSeconds })` the escalation delay. Signals are bare names
(`'TERM'`), in `kill()` and in what `wait()` and `get()` report. Reconnected
`kind:'code'` handles remain replay/control handles (`logs()` / `wait()` /
`kill()`), not a way to recover a parsed `InterpreterResult` on another worker.

`pty.start(command?, { terminal: { cols, rows }, cwd, env, name, user, shell,
login, stdin, executionTimeoutSeconds, keepLogs })` starts an interactive
terminal (80×24 when `terminal` is omitted; `cols` and `rows` are 1 to 65535;
`TERM` is `xterm-256color` unless `env` sets it; `stdin` defaults to `'open'`,
`keepLogs` to `'until_cleanup'`); `pty.attach()` takes the same options and also
connects to it. `pty.list()` is the PTY-only view of
`process.list({ kind: 'pty' })`, and `pty.delete(id)` kills then cleans up that
PTY record.

### Sessions

Create one persistent shell when commands need shared state. Commands run one
at a time, in order; `cd`, exports, and functions carry over. Set `cwd`, `env`,
`shell`, and `login` at creation, not on each command.

```ts
const session = await sandbox.process.createSession({ cwd: '/app', env: { CI: '1' } })
await session.run('export GREETING=hello')
const result = await session.run('echo "$GREETING"', { timeoutSeconds: 10, executionTimeoutSeconds: 30 })
const handle = await session.start('npm run dev')
await handle.kill({ signal: 'INT' }) // interrupts this command; the session survives
const info = await session.get() // includes the shell's current cwd
const reconnected = sandbox.process.connectSession(session.toJSON()) // or session.id
await reconnected.delete() // ends the shell and removes its commands
```

`session.run()` returns `ProcessResult`; `session.start()` returns `ProcessHandle`.
`timeoutSeconds` is the caller's wait budget; `executionTimeoutSeconds` stops the
command, not the session (a command that has not stopped 5 seconds after its
timeout ends the session instead of blocking it). `await using session = ...` deletes
the session on scope exit. List sessions with `process.list({ kind: 'session' })`
or commands with `process.list({ sessionId: session.id })`. Plain `process.run()`,
`process.start()`, and `sandbox.exec()` do not accept `sessionId`.
Sessions survive pause/resume but not sandbox stop/start; reconnecting never
creates a replacement for a missing or ended session.
In dash, BusyBox `sh` and mksh, an interrupt leaves only the
innermost shell function or sourced file; bash and zsh leave the whole command
(bash older than 4.4, macOS's `/bin/bash`, only when no DEBUG trap is set).

### Tags

Give related exec processes, PTYs, and shell sessions the same optional `tag`.
Tags are safe identifiers (`A–Z`, `a–z`, `0–9`, `.`, `_`, `-`, 1–128 characters,
not `.` or `..`) and cannot change after creation. Session commands inherit their session's
tag; do not set one on individual session commands. Interpreter executions
do not support tags.

```ts
await sandbox.process.start('python -m api_server', { tag: 'job-123' })
await sandbox.process.start({ argv: ['node', 'worker.js'] }, { tag: 'job-123' })
const shell = await sandbox.process.createSession({ cwd: '/app', tag: 'job-123' })
await shell.run('nohup ./watcher.sh &') // inherits job-123

await sandbox.process.list({ tag: 'job-123' })
const killed = await sandbox.process.killAll({ tag: 'job-123' })
// Everything the kill covered has stopped; a start racing the kill may run
// afterwards. The tag can be reused immediately.
```

`process.run()`, `sandbox.exec()`, and `pty.start()` also accept `{ tag }`.
`killAll()` keeps logs and records and returns the ids of the tagged processes
that were running, or had running descendants, when it began; survivors of
deleted or expired records are killed too but not listed. Its optional
`timeoutSeconds` is in seconds (more than 0 and at most 60, fractions allowed,
default 10). A tag or timeout outside those rules throws `BadRequestError`
before any request. If processes remain, it throws
`ProcessKillIncompleteError`; calling it again is safe.

Linux and Windows support tags, filtering and tag-wide kills. macOS supports
tags and filtering only: `killAll()` throws `UnsupportedOperationError`.

Without process containment, `killAll()` throws `UnsupportedOperationError`
before killing anything. On Linux with containment, `handle.kill()` also
stops detached descendants (`setsid`, `nohup`, double fork), even after the
original process exited:

```ts
const handle = await sandbox.process.start('./start-server.sh') // backgrounds a server, exits
await handle.wait()
await handle.kill() // Linux with process containment: also stops the server
```

### Files

All sandbox filesystem paths are absolute strings starting with `/`; relative
paths are refused locally with `BadRequestError`. Local upload/download paths
may be relative. Path lists must be arrays of strings, not a bare string.
`writeText()` requires a string; `writeBytes()` and `writeMany()` accept
`ArrayBuffer` and any `ArrayBuffer` view, using only the view's byte range.

`readBytes` / `readText`, `writeBytes` / `writeText`, bulk `readMany` / `writeMany`,
`remove()`, `grep()` (file contents) / `glob()` (file names), `watchDir()` for directory watches, and
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
stay one-shot. `findByName()` is the explicit exception: it returns all matches. The filesystem's `list()`, `grep()`, `glob()` and `replace()`
return one `ListResult` page (`ListResult<FileInfo>`, `ListResult<FileMatch>`,
`ListResult<string>`, `ListResult<ReplaceResult>`). `readText()` decodes strictly as UTF-8 (invalid bytes throw)
and keeps a leading byte-order mark. Writes take no `mode`; use `chmod()`.

`watchDir(path, onEvent, { recursive, onOverflow, onExit })` calls `onEvent`
for each change (a `FilesystemEvent`; `timestampMs` is Unix milliseconds;
`type` is `'create'`, `'write'`, `'remove'`, `'rename'`, `'chmod'` or a kind a
later sandbox adds, passed through as its string).
Callbacks may return promises; they run one at a time and are awaited. A
failing `onEvent` or `onOverflow` ends the watch with that error, which
`exitError` / `waitForExit()` report and `onExit` receives; a failing `onExit`
is ignored. A watch whose stream drops ends with `ConnectionError`. `onExit`
runs exactly once for a watch `watchDir()` returned; a failure before the
watch starts (a refused connection, an HTTP error, a stream that ends before
`start`) rejects `watchDir()` and does not call it. `stop()` resolves once a
callback that is already running, and `onExit`, have finished.

To stop a watch from an event, signal the code that holds the handle and call
`stop()` there; this works in every case. As a shorthand, after a direct
`await watchDir(...)` a callback (or `onExit`) can call `stop()` on the
returned handle itself, and await `waitForExit()` without waiting for itself:
callbacks are held until `watchDir()` has returned its handle. When
`watchDir()`'s promise is not awaited directly (inside `Promise.all`, or
awaited later), the first events can reach the callback before the caller
holds the handle. In runtimes without `AsyncLocalStorage` (browsers), a
callback or `onExit` that stops its own watch after an `await` must not await
that `stop()`.

`writeBytes()`, `writeText()` and `writeMany()` accept `atomic: true` to write via a
temporary file + rename when the sandbox supports it. For `writeBytes()` and
`writeText()` a filesystem that cannot do that rejects with
`AtomicUploadUnsupportedError`; `writeMany()` keeps its partial-success
contract instead — the affected entries appear in the returned `errors` array
as failure messages naming the file and the atomic-rename limitation, while the
rest of the batch is written.

`readBytes()`, `readText()`, `readMany()` and the computer-use recording
`download()` hold the whole response in memory: they take
`{ maxResponseBytes }` to replace the client's cap for one call (`undefined`
or `null` keep the client's configured cap, 1 GiB unless set; `0` means no
limit; any other value, a negative, fractional or oversized number, a
boolean or a string included, is a `BadRequestError`). For large files, or content from a sandbox you don't trust, use `downloadFile()`, which streams to disk
with progress and cancellation (for a recording, pass its `filePath`, not its
id). `downloadFile()` is Node.js-only (a browser build throws
`NotSupportedError`); in a browser, the in-memory reads and a
`presignedDownloadUrl()`, which the browser downloads directly, are the
options.

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
for a throwaway run or use `createContext({ language, contextId, cwd, env })` /
`connectContext()` / `listContexts()` for an independent named context. Select a
context with `run(code, { context })`: a handle, its `toJSON()` identity (an
`InterpreterContextJSON`) or a context id together with `language`. A context's `run(code, { cwd, timeoutSeconds })`
takes no `env`: set it when the context is created. If an explicit named context
vanished, `interpreter.run(..., { context })` / `context.run()` surface the typed
`SessionNotFoundError`; recreate the context yourself and decide what to rerun.
`timeoutSeconds` is enforced client-side (omitted or `0` waits unbounded). Results are split into
`results[]`, `logs{stdout,stderr}`, `error`, and `process`. That rich result
shape comes from the interpreter run path itself; reconnecting a serialized
code-process handle later does not recreate it.

### Git

`add()`, `restore()` and `reset({ files })` require an array of string
pathspecs, not a bare string. Repository paths and pathspecs may remain relative;
the filesystem's absolute-path rule does not apply to git.

`clone()`, `pull()` and `push()` use a dedicated **30-minute** remote-operation
deadline by default, and each accepts a per-call `timeoutSeconds` override:
one total deadline for the whole request, not each read (`0` means no limit,
as for every timeout).

`git.clone(url, { path, branch, commitId, depth, insecureSkipTls, username,
password })` adds optional shallow-clone depth and per-call TLS verification
skipping. `git.pull(path, { username, password, branch, remote })` and
`git.push(path, { username, password, branch, remote, setUpstream })` forward
the additional remote/branch controls to the sandbox.

`git.commit(path, message, { author, email, allowEmpty })` forwards
`allowEmpty: true` when you need an empty commit. `status()` returns a
`GitStatus`; `history()`, `listRemotes()` and `listBranches()` return one
`ListResult` page of `GitCommit`, `GitRemote` and branch-name items.

### Ports, LSP, computer use

`ports` owns both listener discovery and preview exposure:
`list()`, `isInUse(port)`, `expose(port, { public })`,
`previewLink(port)`,
`signedPreviewLink(port, { expiresInSeconds })`,
`expireSignedPreviewLink(port, token)`, paginated `listEndpoints(options)`, and
`iterEndpoints(options)`. The sandbox daemon's ports 2280, 2281 and 22220 are
never preview ports: `expose()`, `previewLink()`, `signedPreviewLink()` and
`createAccessToken({ ports })` refuse them with `BadRequestError` before any
request.
To skip the preview warning page when a headless browser opens a preview URL,
send `{ [PREVIEW_WARNING_SKIP_HEADER]: PREVIEW_WARNING_SKIP_HEADER_VALUE }` (both
exported from `@daytona/sdk/v2`) with its requests.
`lsp.start(languageId, pathToProject)` returns an `LspServer` handle with
`openDocument()`, `closeDocument()`, `completions(uri, position, { context })`,
`documentSymbols()`, `workspaceSymbols()` and `stop()`. A completion item's
`documentation` is absent, a string or an `LspMarkupContent`.
`computerUse` stays lifecycle-flat (`status()`, `start()`, `stop()`, `restart()`) and
splits actions into `service`, `mouse`, `keyboard`, `screen`, `display`,
`a11y` and `recording`. `screen.screenshot()` captures the whole screen or a
`region`; any of `format`, `quality` or `scale` returns a compressed image.
`a11y.tree({ scope: 'focused' })` returns at most 256 levels below the root (`maxDepth`, an
integer from 0 to 256, default 256); a deeper UI comes back cut at that depth
with `truncated` set.

### Docker

Docker Engine starts automatically in sandboxes whose image ships it; there
is no create option. `sandbox.docker.status()` returns its state (`ready`,
`starting`, `failed`, `not_present`, `disabled`, `unmanaged`, `deferred` or
`unsupported`), who owns it, a `reason`, and the `processId` whose logs hold
Docker's output. `sandbox.docker.waitUntilReady({ timeoutSeconds })` polls until the
state is `ready` and resolves with the status. It throws `DockerNotReadyError`
(`error.status`) as soon as Docker settles in a state that cannot become
`ready` (`failed`, `not_present`, `disabled`, `unmanaged` or `unsupported`;
it decides on the state alone, so every `deferred` status keeps polling), and
`DockerWaitTimeoutError` when the budget **(seconds, default 60)**
runs out while Docker is still `starting` or `deferred`, or a status request
is still unanswered at the deadline. `timeoutSeconds: 0`, or a value above about
24.8 days, waits without a deadline. A
Docker failure never makes the sandbox itself unusable.

Set `DAYTONA_DOCKER_START` in the sandbox env to choose how Docker starts:
`auto` (default; start the image's `dockerd` when it ships one), `entrypoint`
(the image's own entrypoint starts Docker) or
`off` (Docker is not started; state `disabled`). If Docker status is unavailable,
`status()` and `waitUntilReady()` throw `NotFoundError` (404).

Some environment variable names are reserved for the platform, among them
every `DAYTONA_*` name other than `DAYTONA_API_URL`, `DAYTONA_OTEL_ENDPOINT`
and `DAYTONA_DOCKER_START` (`DAYTONA_SANDBOX_TOKEN` included). A sandbox `env`
that sets a reserved name fails the create with `BadRequestError` (400) naming
the key. `updateEnv()` refuses reserved `DAYTONA_*` names in the keys it sets or
unsets too (`DAYTONA_SANDBOX_ID`, for one), by the sandbox's own list, which
differs from the create's: it accepts `DAYTONA_SANDBOX_TOKEN`, for one.

### Waiting for lifecycle states

Use `sandbox.wait({ until: 'started', timeoutSeconds: 120 })` to wait for a
public lifecycle state. `until` also accepts an array of states. The default
is `started`, with a 60-second budget; `timeoutSeconds: 0`, or a value above
about 24.8 days (the timer maximum), waits without a deadline. Timeout throws `RequestTimeoutError`; a terminal state reached
first throws `SandboxTerminalStateError`, with the observed public state as
`error.state` and the wire status as `error.rawState`. When the server explains
that state, the explanation is `error.reason` and ends the error message.

`start()`, `stop()`, `pause()`, `resume()` and `delete()` accept
`{ timeoutSeconds }` with the same default and unbounded form. They wait for
completion: `stop()` returns at `stopped`, and `delete()` returns at
`destroyed` (a sandbox disappearing during deletion also means completion).
Client methods accept a sandbox or its ID and the same options, for example
`daytona.v2.stop(sandbox.id, { timeoutSeconds: 120 })`. The server decides
which lifecycle actions the sandbox supports.

`fork({ waitSeconds: 60 })` has its own wait budget; fractions are allowed,
`0` skips waiting, and a value above about 24.8 days waits with no deadline. Once their create or fork request succeeds, these calls
return the handle however the readiness wait ends, including a failed wait
read. Caller cancellation still propagates; a cancelled create may already
have created a sandbox. A raised create or fork means the request itself failed, except for
`SandboxCreateRequestTimeoutError` and `SandboxCreateConnectionError` (they
carry the idempotency key because a sandbox may exist), caller cancellation,
and an unexpected non-SDK error during the wait. A running Linux source produces a running fork; otherwise
the fork is paused if the source was paused and memory was retained, or
stopped. macOS forks copy only disk and settle at `started` or `stopped`.

`await sandbox.delete()` and `await daytona.v2.delete(sandbox)` wait until the
sandbox is `destroyed` (a 404 while waiting counts as destroyed). A sandbox
with volumes attached stays `destroying` until the platform has finished with
its volumes: usually seconds, but minutes when a lot of volume data is written
at the end, so give `delete({ timeoutSeconds: 600 })` a budget that covers it.
If that takes too long, the sandbox reports `error` and the wait throws
`SandboxTerminalStateError`; when the server explains why the deletion did not
finish, that is its `reason`. Such a sandbox can still reach `destroyed`
later. To keep waiting for it, catch the error, pause for a few seconds and
call `sandbox.wait({ until: 'destroyed', timeoutSeconds: 600 })`: while the
sandbox still reports `error`, the call throws again at once.

Reaching `destroyed` does not guarantee that deleting an image or snapshot
will succeed: deleting the image or snapshot immediately afterwards can still
throw `ConflictError` (409) while references are being released. A missing
sandbox during `wait()` throws `NotFoundError`; only `delete()` maps a 404 to
`destroyed`.

## Errors

The root `DaytonaError` (from `@daytona/sdk`) catches errors from both
namespaces and types `statusCode`, `code`, and `source`. The v2 classes
(`import { NotFoundError, … } from '@daytona/sdk/v2'`) have the same names as
in the Python SDK: `BadRequestError`, `AuthenticationError` (401),
`ForbiddenError` (403), `NotFoundError`, `ConflictError`, `RateLimitError`,
`RequestTimeoutError` (408), `ClientTimeoutError` (the client's own request
timeout), `ConnectionError`, … They also expose `traceId` (v0 errors carry
none), and the status-level bases have code-specific subclasses so you can
catch as broadly or narrowly as you need. A call into a sandbox that is not
running (a stopped one), through `sandbox.process`, `sandbox.fs`, `sandbox.git`
or the other in-sandbox namespaces, throws `SandboxNotRunningError` (409,
`SANDBOX_NOT_RUNNING`), a `ConflictError`, not `NotFoundError`; its preview URLs
answer 409 too. Start the sandbox and retry. `code` and `source` are open
vocabularies: a code this release does not know is kept as sent and the error
is the class for its HTTP status; the `codes` namespace lists the known codes
per source (`codes.API_ERROR_CODES`, `codes.CODE_STATUS`,
`codes.isKnownErrorCode`, …). The SDK never follows an HTTP redirect: a 3xx
response throws `DaytonaError` with its `statusCode` (source `SDK`). The standard errors' constructors take the
message plus an `ErrorOptions` object (`statusCode`, `code`, `source`,
`traceId`, `cause`, …), so tests can build the errors your code handles. A few
specialised errors build their own message from what identifies them instead:
`SandboxTerminalStateError(sandboxId, state, options?)` (`options`:
`{ rawState?, reason? }`),
`SandboxCreateTimeoutError(sandboxId)`,
`SandboxCreateRequestTimeoutError(idempotencyKey)`,
`SandboxCreateConnectionError(idempotencyKey, detail)`,
`DockerWaitTimeoutError(status?)` and `DockerNotReadyError(status)`.

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

On a sandbox that is deleted or being deleted, `setLabels()`,
`updateLifecyclePolicy()`, `resetIdleTimer()` and the preview-endpoint calls
(`ports.expose()`, `ports.listEndpoints()`, `ports.previewLink()`,
`ports.signedPreviewLink()`) throw `ConflictError` (`CONFLICT`);
`ports.unexpose()` and `ports.expireSignedPreviewLink()` still work.

## Versioning and releases

See `docs/VERSIONING.md` and `CHANGELOG.md` in the
[repository](https://github.com/daytona/clients).
