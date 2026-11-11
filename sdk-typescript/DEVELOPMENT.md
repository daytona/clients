# @daytona/sdk — development

Contributor notes. Not shipped in the package (`package.json` `files` lists
`dist` and `README.md` only). The user-facing README must stay free of
internal details — see `../AGENTS.md`.

## Design references

- Python ↔ TypeScript parity contract: [`../AGENTS.md`](../AGENTS.md).

## Implementation notes

### Delete completion and image or snapshot conflicts

`Sandbox.delete({ timeoutSeconds })` requests deletion, then waits for
`destroyed`. A typed 404 on any read during that wait also means completion;
the handle is updated to `destroyed`. Ordinary `refreshData()` and other
waits still propagate 404. The private waiter treats `destroying` as progress
when targeting `destroyed`. `stop()` similarly waits for `stopped`.

All lifecycle methods use `LifecycleWaitOptions` (default 60 seconds, `0`
unbounded). The v2 client's methods delegate to the handle, including when
given an ID. Timeout throws `RequestTimeoutError`; a terminal state throws
`SandboxTerminalStateError`. Server errors decide class and persistence
support, without client-side lifecycle prechecks.

An observed `destroyed` state does not guarantee that deleting the sandbox's
image or snapshot will succeed without a conflict. Callers must still handle
`ConflictError` from those operations.

### Create, fork and public metadata

After a create or fork POST succeeds, the call returns its handle however the
readiness wait ends, including any `DaytonaError` from a wait read. Non-SDK
errors and caller cancellation propagate; a cancelled create may already have
created a sandbox. A raised create or fork means the request itself failed, except for
`SandboxCreateRequestTimeoutError` and `SandboxCreateConnectionError` (they
carry the idempotency key because a sandbox may exist), caller cancellation,
and an unexpected non-SDK error during the wait.
Create uses whole-second
`waitSeconds`; fork permits fractions and uses `0` to skip waiting. Linux
forks refresh the source before submission when waiting: running sources wait
for `started`, paused sources retaining memory wait for `paused`, and other
sources wait for `stopped`. macOS forks wait for `started` or `stopped`.

`createFromBuild()` always waits for the image build with its independent
`buildTimeoutSeconds` budget (omitted or `0` is unbounded), then creates with
the caller's full `waitSeconds`. Build failure throws `ImageBuildFailedError`
with `imageId` and `buildError`; build timeout throws `RequestTimeoutError`.
Build submission and its initial lookup are ordinary requests; the build wait
budget begins afterwards. Long polls (sandbox waits, image pulls, artifact
gets) are governed by the wait budget, not the request timeout: each hold is
the smaller of its slice and the remaining budget, in whole milliseconds, and
the read allows the hold plus a few seconds of slack. Positive artifact-get
budgets repeat holds capped at 60 seconds and pause up to one second after an early
non-terminal response; omitted or zero budgets send one default hold.

All create sizing lives in `resources`. GiB values must convert to whole MiB
without rounding, and sandbox readback divides MiB by 1024 exactly. Public
models use readonly camelCase fields and SDK-owned types. Construction of
`Sandbox`, `Image`, `Snapshot`, `ImagesService`, `SnapshotsService`,
`VolumesService`, `SecretsService`, `ReservationsService`, `MacModelsService`,
`PortsService`, `DockerService` and `SandboxVolumesService` requires the
package-internal construction token. This does not describe toolbox classes
such as `Session` or `ProcessService`. `SandboxHandleJSON` includes known metadata but no URLs or
credentials; callers reconnect with `get(info.sandboxId)`. `autoDelete` defaults
to false on every acquisition route, including `get()` and `fork()`.
The read-only `accessToken` getter exposes the sandbox-scoped bearer currently
used by the handle (create response, caller override, or minted token), is
`undefined` until known, and is never serialized. The toolbox URL is internal.

`list()` / `iter()` accept only cursor and limit. `search()` carries filters,
including public `states`, which map to the API's status vocabulary at the
boundary. Named creates permanently support get-or-create; name-key helpers
remain internal. `findByName()` returns all matches and is the explicit
exception to the one-page list rule.

### Volumes

`daytona.v2.volumes` manages named volumes through the platform API.
`Volume.ts` maps contract fields to the public camel-case `Volume` type,
mapping absent optional fields to undefined and normalizing unknown states and types to
`unknown` with the original value in `rawStatus` / `rawType`. `list()` reads
one cursor page; `iter()` traverses pages with a cursor-cycle guard. `get()`
takes `{ create?, idOnly? }`, `create()` takes `{ name, sizeGib?, type? }` and
sends `type` only when the caller passes one (the server chooses otherwise), and `delete()` takes a name or ID plus `{ idOnly? }`.

`create`, `createFromBuild` and `createMacos` accept `VolumeMount` entries
through `volumes`. Request mapping uses `volume`, `mount_path`, `subpath`
and `read_only`; false or omitted `readOnly` leaves `read_only` absent.
macOS accepts read-write disk volumes only, without `persistent: true`.

`sandbox.volumes` lazily caches a `SandboxVolumesService` and clears it
when the sandbox connection is refreshed. `pull()` preserves the caller's
request identity, maps the generated wire type's nullable `manifestId` to
the public `PullVolumeResult`'s optional `manifestId` (`undefined` for
`null`), and makes one request
without automatic retries. Its HTTP deadline includes read slack beyond the
requested wait. `timeoutSeconds` accepts 0–30 seconds, including fractions;
the wire `waitMs` is floored, with every positive input producing at least 1 ms.
`tests/volume-pull.test.ts` exercises these behaviors over HTTP;
`tests/unit.test.ts` covers management, create mappings and route telemetry.

`npm run gen:contract` restores volume aliases in the API and toolbox alias
layers. Keep those aliases in `scripts/gen-contract.mjs`; the generated pull
response type must keep `manifestId: null` rather than optionalizing it (the
wire carries `null`; only the public result reads it as `undefined`).

### Layout and runtime

- Layout:

  ```text
  src/
  ├── Daytona.ts        root client (`new Daytona().v0` / `.v2`)
  ├── index.ts          root exports (client, config, shared v0/v2 errors, constants)
  ├── errors.ts         shared root error hierarchy + registration helpers
  ├── types.ts          root config type
  ├── v0/              publish-time bundle only (gitignored, never committed)
  ├── v0-unbundled.ts   source-checkout fallback for `@daytona/sdk/v0`
  ├── v2/              current handwritten SDK
  └── version.ts        stamped from package.json; stays at this path
  ```

- The published root package exports `.` for the namespaced root client, `./v2`
  for the current SDK, and `./v0` for the publish-time v0 bundle.
- `src/errors.ts` is the shared root hierarchy: root classes are native Error
  subclasses with `Symbol.hasInstance`; v2 registers its classes at module load,
  and the bundled v0 entry registers its classes by export name when loaded.
- A bundled publish runs `npm install`, not `npm ci`: the merged v0
  dependencies keep the 0.x release's semver ranges, so the
  build's v0 dependency graph is range-resolved at publish time. The tarball
  declares the same ranges; consumers resolve them on install.
- TypeScript resolves `@daytona/sdk/v0` through `tsconfig.json` paths in this
  order: `dist/cjs/v0/index.d.ts` when the bundle exists, then the committed
  `src/v0-unbundled.ts` fallback in a source checkout. The fallback is only for
  local typecheck / tests; the published package resolves through `exports`.
- The root client config does not accept `apiUrl`. Its v2 namespace reads
  `DAYTONA_V2_API_URL`, or falls back to `DEFAULT_V2_API_URL`;
  `DAYTONA_API_URL` belongs to the 0.x SDK and nothing in 2.x reads it. Set
  environment overrides before constructing a client.
- `scripts/build-v0.mjs` emits the unbundled stub when `src/v0/` is absent;
  publish builds require the bundle. When present it builds
  `dist/cjs/v0` (`tsconfig.v0.json`) and the
  browser ESM build `dist/esm/v0` (`tsconfig.v0.esm.json`, `require(` shimmed).
- Local bundled build: `bash ../scripts/bundle-v0.sh --lang typescript`, then
  `npm install` (the bundle merged v0's dependencies into `package.json`; until
  they are installed `npm run build` fails on `src/v0`), then `npm run build`.
  Before committing, run `bash ../scripts/bundle-v0.sh --clean` (removes
  `src/v0`) and restore the manifest with
  `git checkout -- package.json package-lock.json`; sdk-ci rejects a committed
  bundled manifest.
- The v0 namespace vendors the 0.x SDK at the tag pinned in
  `scripts/v0-sdk-version` (`scripts/bundle-v0.sh` reads it; bumping
  the pin, tag plus its commit, is a two-line PR plus a 2.x release; `DAYTONA_V0_TAG` overrides it
  for testing only).
- `scripts/bundle-v0.mjs <v0-sdk-typescript-src-dir> <v0-version> <tag> <v0-root-package.json>`
  repopulates `src/v0/`, writes `src/v0/_bundle.ts`, appends the `...V0`
  aliases for names also exported by v2, and merges the v0 runtime dependency
  set into `package.json`. Re-running it replaces the bundle in place.
- Names also exported by v0 at the pinned tag (`v0.230.0`) on the v2 surface are committed as
  `...V2` aliases: `DaytonaError`, `Image`, `LspServer`, and `Sandbox`.
  `scripts/bundle-v0.mjs` fails when this set and the pinned release's shared
  value exports disagree.

- `process`: cursor-replayable `logs()`, SSE `streamLogs()` with
  `Last-Event-ID` resume, `wait()` long-poll; PTY `attach()` relies on Node's
  built-in global `WebSocket` (Node 22.4+); the package's floor is Node 22.15,
  which the bundled v0's OpenTelemetry 3.x dependencies declare. Public
  `executionTimeoutSeconds` is translated to the API's `timeoutMs` (whole
  milliseconds, rounded up, by `exactMs` in `ProcessWire.ts`; a value above
  the timer maximum, checked first by `aboveTimerMaximum`, omits it: no
  deadline) only on the
  process routes that support a server-managed kill deadline; code execution
  stays client-timed and interrupts on expiry instead.
- `interpreter`: one-shot `run()` (`kind:"code"`) and stateful contexts.
  Public `timeoutSeconds` is client-side only; an explicit `0` also leaves the
  setup requests (context creation, the code POST) unbounded. Context-free runs share per-language
  state whose `env` is fixed on first use; `isolated: true` creates a throwaway
  context. Explicit contexts surface `SessionNotFoundError` when lost, rather
  than silently recreating state.
- Pre-signed file URLs never expose the signing key.
  `expiresInSeconds` is validated to the range 1 to 604800
  (`SIGNED_FILE_MAX_TTL_SECONDS`) before the request. A response payload that
  breaks the contract (JSON `null`, an array, a malformed object) is a
  `BadRequestError` (400, `BAD_REQUEST`, source `SDK`) naming the field, with
  the same message in both SDKs; so is a malformed sandbox access-token mint
  response. `sandbox.rotateSigningKey()` resolves `void`; outstanding
  links stop working within approximately 30 seconds.
- Errors: status-level bases → code-specific subclasses; `src/v2/codes.ts`
  is drift-tested against the committed error catalogs under `../contract/`
  and exported as the frozen `codes` namespace. The standard errors take
  `(message, options?: ErrorOptions)`; a subclass with extra fields extends
  `ErrorOptions` (`ImageBuildFailedErrorOptions`,
  `BuildLogInterruptedErrorOptions`), and the specialised errors that build
  their own message take their identity positionally plus options of their
  own (`SandboxTerminalStateError(sandboxId, state,
  SandboxTerminalStateErrorOptions)`, `SandboxCreateRequestTimeoutError(idempotencyKey)`, …). SDK-raised errors pass
  `{ statusCode, code, source: 'SDK' }`; `createDaytonaError` dispatches a
  `(source, code)` pair and `errorOptionsFromEnvelope` maps a wire envelope.

### Options objects

Public options objects reject unknown keys at runtime, because a JavaScript
caller's typo or an option renamed in 2.x would otherwise be ignored silently.
Today the client configuration does (`DaytonaConfig`, `SandboxEventsConfig`),
and so does every client, sandbox-lifecycle and platform-service options
object, nested entries (`resources`, ports, volume mounts) and
`IfMatchOptions` included; the toolbox namespaces' per-call options adopt it
area by area. Every one of those options parameters also refuses a value that
is not an options object (`assertOptionsObject`: `null`, a primitive, an
array, a function, a built-in) before any request; only an omitted argument
takes the defaults. Create option values (`env`, `labels`, `secrets`, port and
volume entries, `spot`, `user`, `cpuArch`, ...) are checked by
`assertCreateNestedOptions`, before the build in `createFromBuild()`. `src/v2/options.ts` provides the
pieces; applying them to a new options type takes two lines:

```ts
import { assertKnownOptions, optionKeys } from './options.js'

/** The known keys of {@link CreateSandboxOptions}. */
export const CREATE_SANDBOX_OPTION_KEYS = optionKeys<CreateSandboxOptions>()({
  name: true,
  labels: true,
  waitSeconds: true,
  // ...every key of CreateSandboxOptions, nothing else
})

public async create(image: string, options: CreateSandboxOptions = {}): Promise<Sandbox> {
  assertKnownOptions('CreateSandboxOptions', options, CREATE_SANDBOX_OPTION_KEYS)
  // ...
}
```

- `optionKeys<T>()` is curried so `T` is explicit while the literal's keys are
  inferred: a key of `T` missing from the list, or a listed key `T` does not
  have, is a compile error, so the list cannot drift from the type.
- `assertKnownOptions` throws `BadRequestError` (400, `BAD_REQUEST`, source
  `SDK`): "`CreateSandboxOptions has no option \`foo\``". When the unknown key
  is in `RENAMED_OPTIONS` and its replacement is a known key of the type, the
  message names it: "`CreateSandboxOptions: \`timeout\` was renamed to
  \`timeoutSeconds\` (seconds)`". The table covers `timeout`,
  `executionTimeout`, `escalateAfter`, `requestTimeout`, the 0.x client
  configuration's `requestTimeoutMs`, `reconcileIntervalMs` and
  `idleCloseMs`, and the `keyboard.type()` option `delay`; add a row when
  another option is renamed. The optional fourth argument supplies per-type
  mappings: fork maps `timeout` to `waitSeconds`, and `createFromBuild` maps
  it to `buildTimeoutSeconds`. Removed `envVars` is an unknown key without a
  rename hint.
- Check the options object itself, and nested SDK-defined option objects
  (`resources`, `ports`, `volumes`, `networkPolicy`, `lifecyclePolicy`, the
  root's `events` as `SandboxEventsConfig`, `pty.start()`'s `terminal` as
  `TerminalSize`); never a pass-through map (`env`, `labels`, request
  headers).
- A duration option in seconds that the SDK converts itself (the request
  timeout, the events durations) is validated with `optionalSeconds(name,
  value)` (finite, non-negative) and converted to milliseconds where it is
  used. A request-timeout delay goes through `boundedTimerDelay` (above
  `MAX_TIMER_DELAY_MS` means no bound); the events hub instead clamps its
  timer delays to `MAX_TIMER_DELAY_MS` (the reconcile interval to that less
  its 10 % jitter), and Python clamps the same way. The toolbox converts
  process and interpreter caller budgets with `secondsToMs` (`ProcessWire.ts`)
  on the request timeout's rule (above the maximum means no limit); the kill
  escalation and the keyboard typing delay go through `exactMs`, which sends a
  positive sub-millisecond value as 1 ms and refuses one above 2^53 − 1 ms
  (an execution timeout above the timer maximum is no limit, else `exactMs`). Per-call options of other
  areas keep their own validation until their area moves to seconds.
- A nested options object the SDK keeps (the root's `events`) is copied with
  `snapshotOptions`, so later changes to the caller's object do not reach the
  client.
- Test both the rejection and the rename hint for every type you cover (see
  `tests/client-config.test.ts`).

### Request timeout

`requestTimeoutSeconds` (default 60, `0` = none) bounds the total time of one
ordinary request: `createConfiguredHttpClient` arms an abort-signal timer per
axios request (`armRequestDeadline`), because axios' own `timeout` only bounds
the wait for headers and idle reads in Node. A request with its own budget
passes `timeout` explicitly (`0` = unbounded): waits and long polls derive it
from the caller's deadline. File transfers pass `transferRequest(...)`
(axios) or `transfer: true` (`HttpTransport.fetch`): no total deadline, an
idle watchdog (`IdleWatchdog` in `bodyLimit.ts`) of the request timeout
instead; other `fetch` calls are streams and are bounded only until their
response headers arrive. The Python transport implements the same rule
(`_request_deadline`, `RequestSpec.transfer`).

Every axios request goes through `boundedAdapter` (`boundedAdapter.ts`): Node's http
adapter where the runtime has it, else the SDK's `fetchAdapter`. Every buffered
response (any `responseType` but `'stream'`, which the caller reads itself and
which is not capped; a file transfer cannot use it) is opened as a stream and
read by `readCappedBody` under `maxContentLength`
(the client's `maxResponseBytes`, or the per-call `maxContentLengthFor(...)`),
the declared `Content-Length` checked first. Never route a request around it
(a test that replaces `defaults.adapter` with a mock is the exception).
Bodies read off `fetch` whole (error bodies, the token exchange) go through
`readFetchText`. `tests/transfer-bounds.test.ts` runs the bounds against both
adapters (`forceFetchAdapterForTests`), and the runtime suite's `capOk` check
runs them in every runtime.

The deadline is absolute (`config.daytona.deadlineAt`), set once when the
call starts: waiting for the project token is abortable by the request's
signal (the shared exchange itself runs on under its own timeout for the
other requests waiting on it), a 401 refresh gets only the remaining budget,
and the retry re-arms the same deadline. `withAuth`
(sandbox tokens) sets `deadlineAt` before minting, and `fetch` bounds the
bearer and the refresh with the same timeout signal.

- Signals: bare names on the surface (`TERM`, `INT`, …), `SIG*` on the wire.
- Reserved sandbox `env` names: platform-reserved prefixes plus every
  `DAYTONA_*` name other than
  `DAYTONA_SANDBOX_TOKEN`, `DAYTONA_API_URL`, `DAYTONA_OTEL_ENDPOINT` and
  `DAYTONA_DOCKER_START`. The API rejects the create with `400` naming the
  key.

### Construction of toolbox handles and services

`ProcessService`, `ProcessHandle`, `Session`, `PtyService`,
`InterpreterService`, `InterpreterContext`, `FileSystemService`, `GitService`,
`LspService`, `LspServer` and the computer-use services take `INTERNAL`
(`src/v2/internal.ts`) as their first constructor argument and throw a
`TypeError` without it. The module is not reachable through the package
`exports`, so callers get these objects from a sandbox; tests import the token
directly (`new ProcessService(INTERNAL, transport, 'sbx')`). The real
signature is an `/** @internal */` overload (removed by `stripInternal`); the
published one is `constructor(token: Internal, ...init: never[])`, so no
declaration names `HttpTransport`, `ComputerUseTransport` or another internal
input. `tests/public-declarations.test.ts` emits the declarations and fails
when a public class member names one of a selected set of leaked types (the
transports and their options, axios types, `ProcessBinding`,
`ContextExecutionQueue`, generated `api.` / `toolbox.` types); classes of
other areas that still do are on its `ALLOWED` list, which only shrinks.

## Verification

Verify that `buildLogs()` returns a one-shot string and `followBuildLogs()`
delivers output with resumable cursors. State events carry status, optional
error and deadline; availability is a separate `unavailable` event. Validate
destinations before attaching credentials and refuse redirects. Both modes
preserve bounded reads and deadlines. Test equivalent accepted sandbox ID
spellings so resource checks do not reject valid responses.

```bash
npm install && npm run typecheck && npm test && npm run build
```

The live toolbox test requires `DAYTONA_SDKTS_TOOLBOX_URL` and skips when
it is unset or the configured endpoint is unavailable.

### Runtime compatibility

`runtime-tests/` installs the packed package into Node (CommonJS and ESM),
Bun, Deno, an esbuild CommonJS re-bundle, Vite (SSR and a browser bundle in
headless Chromium), Next.js (Turbopack, webpack and `serverExternalPackages`),
Nuxt, Remix, workerd (edge runtime), AWS Lambda and Azure Functions, and runs the
same API, toolbox and fs-watch checks in each against a local mock API:

```bash
npm run build && npm run test:runtime
ONLY=node-esm,bun npm run test:runtime   # a subset
```

Changes to the transports in `src/v2/http.ts` (fetch options, abort and
timeout handling, `process` / `window` guards), the ESM / CommonJS build, or
`package.json` `exports` / `imports` must keep it green. CI runs it on the
v0-bundled package, one job per runtime
(`.github/workflows/sdk-typescript-runtimes.yaml`). See
[`runtime-tests/README.md`](./runtime-tests/README.md) for the external tools
(bun, deno, `func`, Chromium) and how to add a runtime.
