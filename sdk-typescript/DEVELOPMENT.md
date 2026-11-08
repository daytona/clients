# @daytona/sdk — development

Contributor notes. Not shipped in the package (`package.json` `files` lists
`dist` and `README.md` only). The user-facing README must stay free of
internal details — see `../AGENTS.md`.

## Design references

- Python ↔ TypeScript parity contract: [`../AGENTS.md`](../AGENTS.md).

## Implementation notes

### Delete completion and image or snapshot conflicts

Keep `Sandbox.delete()` acceptance-only. The v2 client's `delete()` delegates
to it. Reusing
`waitForState(['destroyed'])` would wait for an observed lifecycle state,
not establish that deleting its image or snapshot can no longer conflict:

An accepted delete (HTTP 202), or a subsequent `destroyed` state, does not
guarantee that deleting the sandbox's image or snapshot will succeed without
a conflict. Callers must still handle `ConflictError` from those operations.

The existing waiter treats `destroying` as progress when targeting
`destroyed`. Its refresh still propagates a typed 404; the 404-to-`destroyed`
mapping is local to `Sandbox.delete()`'s single follow-up refresh, not
`refreshData()` or the waiter. Neither a new default wait nor an extra
refresh changes this API guarantee.

### Volumes

`daytona.v2.volume` manages named volumes through the platform API.
`Volume.ts` maps contract fields to the public camel-case `Volume` type,
preserving explicit nulls and normalizing unknown states and types to
`unknown` with the original value in `rawStatus` / `rawType`. `list()` reads
one cursor page; `iter()` traverses pages with a cursor-cycle guard.

`create`, `createFromBuild` and `createMacos` accept `VolumeMount` entries
through `volumes`. Request mapping uses `volume`, `mount_path`, `subpath`
and `read_only`; false or omitted `readOnly` leaves `read_only` absent.
macOS accepts read-write disk volumes only, without `persistent: true`.

`sandbox.volumes` lazily caches a `SandboxVolumesService` and clears it
when the sandbox connection is refreshed. `pull()` preserves the caller's
request identity, accepts a nullable `manifestId`, and makes one request
without automatic retries. Its HTTP deadline includes read slack beyond the
requested wait. `tests/volume-pull.test.ts` exercises these behaviors over HTTP;
`tests/unit.test.ts` covers management, create mappings and route telemetry.

`npm run gen:contract` restores volume aliases in the API and toolbox alias
layers. Keep those aliases in `scripts/gen-contract.mjs`; the pull response
must preserve `manifestId: null` rather than optionalizing it.

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
  `DAYTONA_V2_API_URL`, then `DAYTONA_API_URL`, or falls back to
  `DEFAULT_V2_API_URL`. Set environment overrides before constructing a client.
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
  `...V2` aliases: `DaytonaAuthenticationError`,
  `DaytonaAuthorizationError`, `DaytonaConflictError`,
  `DaytonaConnectionError`, `DaytonaError`, `DaytonaNotFoundError`,
  `DaytonaRateLimitError`, `DaytonaTimeoutError`, `DaytonaValidationError`,
  `Image`, `LspServer`, and `Sandbox`.

- `process`: cursor-replayable `logs()`, SSE `streamLogs()` with
  `Last-Event-ID` resume, `wait()` long-poll; PTY `attach()` relies on Node's
  built-in global `WebSocket` (Node 22.4+); the package's floor is Node 22.15,
  which the bundled v0's OpenTelemetry 3.x dependencies declare. Public
  `executionTimeout` is translated to the API's `timeoutMs` only on the
  process routes that support a server-managed kill deadline; code execution
  stays client-timed and interrupts on expiry instead.
- `interpreter`: one-shot `run()` (`kind:"code"`) and stateful contexts.
  Public `timeout` is client-side only. Context-free runs share per-language
  state whose `env` is fixed on first use; `isolated: true` creates a throwaway
  context. Explicit contexts surface `SessionNotFoundError` when lost, rather
  than silently recreating state.
- Pre-signed file URLs never expose the signing key.
  `expiresInSeconds` is validated to the range 1 to 604800
  (`SIGNED_FILE_MAX_TTL_SECONDS`) before the request. Invalid response payloads
  such as JSON `null`, arrays or malformed objects produce the same typed 500
  error in both SDKs. `sandbox.rotateSigningKey()` resolves `void`; outstanding
  links stop working within approximately 30 seconds.
- Errors: status-level bases → code-specific subclasses; `src/v2/codes.ts`
  is drift-tested against the committed error catalogs under `../contract/`.
- Signals: bare names on the surface (`TERM`, `INT`, …), `SIG*` on the wire.
- Reserved sandbox `env` names: platform-reserved prefixes plus every
  `DAYTONA_*` name other than
  `DAYTONA_SANDBOX_TOKEN`, `DAYTONA_API_URL`, `DAYTONA_OTEL_ENDPOINT` and
  `DAYTONA_DOCKER_START`. The API rejects the create with `400` naming the
  key.

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

### Public-surface snapshot

`api-surface/index.api.txt` (`@daytona/sdk`) and `api-surface/v2.api.txt`
(`@daytona/sdk/v2`) list the declaration surface of the entry points, one
declaration per line: every export with its kind and full type signature, the
members of exported classes, interfaces and object types, enum members, and
long unions one member per line. A declaration of this package that a listed
signature uses but no entry point exports (the v2 client behind the root's
`.v2`, an options type that is not exported) is listed as `~Name`
(`~Name@path` when the name is taken), in the file of the first entry point
that reaches it, with its members. Union members are printed in sorted
order, so the output does not depend on the order the compiler checks files.
An object type callers pass in (named in a parameter of a listed signature, or
in a property of such a type, and never returned by the SDK) gets a
`Name: passed-in` line: a new required member of it is a breaking change. So
do the base interfaces of such a type, whose members are listed under the base
(`passed-in also-returned` when the SDK also returns the base). Directions
follow who implements a function: the parameters of a function the SDK
implements are passed in and its return type is returned; for a callback the
caller implements and passes in (a function-typed parameter or property, or a
method of an interface or object type the caller passes in) it is the other
way round: its parameters are returned and its return type is passed in. Any
other type that is both passed in and returned gets
`Name: returned also-passed-in`. A member of a type that is only passed in may
become optional; one of a type that goes both ways may not. `scripts/api-surface.mjs` reads it with the
TypeScript compiler API (`@internal` exports, members and overloads excluded,
as `stripInternal` does for the published declarations). Regenerate after any change to the public surface and commit
the result; CI runs the check:

```bash
npm run api-surface          # regenerate
npm run api-surface:check    # exit 1 when stale
```

The `Breaking changes` workflow compares the snapshot with the last stable
release (see `docs/VERSIONING.md`, "Breaking-change check").

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
