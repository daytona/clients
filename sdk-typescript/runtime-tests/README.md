# Runtime compatibility tests

These tests install the packed `@daytona/sdk` (ESM and CommonJS builds,
`package.json` `exports` / `imports`, the bundled v0 namespace) into one
throwaway project per JavaScript runtime and run the same checks in each one.
No deployment and no credentials are involved: `run-all.sh` starts
[`mock-api.mjs`](./mock-api.mjs), a local stand-in for the API and one sandbox
toolbox shaped after `contract/api-openapi.json` and
`contract/toolbox-openapi.json`.

## What each runtime checks

Every runtime imports the **root** package (`import * as sdk from
'@daytona/sdk'`, or `require` for CommonJS), the way applications do. It passes
that module to [`runtime-checks.mjs`](./runtime-checks.mjs), which builds
`new Daytona({ apiKey }).v2` and checks:

| Key | Call | Path |
| --- | --- | --- |
| `listOk` | `list()` returns one sandbox in state `started` | `GET /sandboxes` (axios transport) |
| `notFoundOk` | `get('does-not-exist')` throws `DaytonaNotFoundError` (`statusCode` 404, `code` `NOT_FOUND`) | `GET /sandboxes/{id}` returning the error envelope |
| `toolboxListOk` | `sandbox.fs.list('/')` returns `hello.txt` | sandbox access token mint, then toolbox `GET /files` (axios) |
| `toolboxWatchOk` | `sandbox.fs.watchDir('/')` delivers one event, then `stop()` exits cleanly (no `exitError`) | toolbox `GET /files/watch` SSE (native `fetch`, `ReadableStream` reader, `AbortController`) |
| `v0` | `daytona.v0` builds the v0 client (`bundled`), or throws `NOT_IMPLEMENTED` for an unbundled source build (`unbundled`) | no request |

[`assert-result.mjs`](./assert-result.mjs) requires every `*Ok` key to be
`true` and `v0` to match the package under test.

The mock checks that requests look the way the contract says: a bearer API key
on API routes and the minted sandbox token on toolbox routes (401 otherwise),
query parameters, and CORS for the browser runtime.

**Provenance.** `run-all.sh` restamps the tarball with a version no registry
has (`<version>-runtime-test.<time>.<pid>`, in `package.json` and in the built
`SDK_VERSION` that both transports send as `X-Daytona-SDK-Version`). The mock
rejects every request that carries another version, so a runtime that resolved
some other `@daytona/sdk` (a registry release, a cached or stale tarball) fails
every check instead of passing on the wrong code.

## Running locally

```bash
cd sdk-typescript
npm run build
npm run test:runtime                           # every runtime
ONLY=node-esm,workerd npm run test:runtime
```

That tests the unbundled source build (`v0` is `unbundled`). To test the
published shape, with the v0 namespace bundled (what CI runs):

```bash
bash ../scripts/bundle-v0.sh --lang typescript
npm install && npm run build
RUNTIME_TEST_REQUIRE_V0_BUNDLE=1 npm run test:runtime
bash ../scripts/bundle-v0.sh --clean && git checkout -- package.json package-lock.json
```

`RUNTIME_TEST_TARBALL=<file.tgz>` tests an existing `npm pack` output instead
of packing `dist/`.

Runtimes run in parallel (`RUNTIME_TEST_PARALLEL`, default `nproc`; `1` runs
them one at a time), each in a temporary copy of its directory with a
per-runtime timeout (`RUNTIME_TEST_TIMEOUT_SECONDS`, default 600). Output is
buffered per runtime and printed in order at the end. A runtime whose binary is
missing prints `SKIP:` and counts as skipped; with `CI=true` a skip fails the
run, because in CI it means a toolchain install step broke.

## Runtimes

| Runtime | What runs | Loads |
| --- | --- | --- |
| `node-cjs` | `node test.cjs` with `require('@daytona/sdk')` | CommonJS build |
| `node-esm` | `node test.mjs` with `import` | ES module build |
| `bun` | `bun run test.ts` | Bun |
| `deno` | `deno run test.ts`, resolved from `node_modules` | Deno 2 |
| `esbuild-cjs` | `esbuild --bundle --platform=node --format=cjs`, run from a directory without `node_modules` | ES module build re-bundled to CommonJS |
| `vite-ssr` | `vite build --ssr`, then Node imports the bundle | Vite SSR bundle |
| `vite-browser` | `vite build`, then Playwright loads the page in headless Chromium | browser bundle (`browser` conditions, v0's ESM build) |
| `nextjs` | `next build` (Turbopack) + `next start`, App Router route | bundled into the server route |
| `nextjs-webpack` | `next build --webpack` + `next start` | bundled by webpack (`webpack` condition) |
| `nextjs-external` | `serverExternalPackages: ['@daytona/sdk']` | Node loads the package from `node_modules` at run time |
| `nuxt` | `nuxt build` + the Nitro server | Nitro bundle |
| `remix` | `remix vite:build` + `remix-serve` | runs on Remix's fetch polyfill (`@remix-run/web-fetch`) |
| `workerd` | esbuild worker bundle served by the `workerd` runtime (npm `workerd`, `nodejs_compat`), with the axios release npm resolves and with axios 1.20 | edge-runtime worker bundle (`workerd` / `worker` conditions) |
| `aws-lambda` | esbuild bundle run by [`lambda-local`](https://github.com/ashiina/lambda-local) | Lambda handler bundle |
| `azure-functions` | `tsc` to CommonJS + `func start` (HTTP trigger, no extension bundle) | CommonJS build in the Functions host |

The configuration reaches each runtime the way an application's would: the
API URL through `DAYTONA_V2_API_URL` (the v2 client takes no URL option) and the
API key as `apiKey`. The `workerd` worker gets both as text bindings (with
`nodejs_compat` and a compatibility date from 2025-04-01 on, `process.env`
carries them). The browser page has no environment, so `test-browser.mjs`
injects a `process.env` shim with the URL ahead of the bundle.

These runtimes run real application builds; sdk-ci's tarball smoke separately
checks the bundle shapes themselves (esbuild, webpack and rollup browser
bundles, CommonJS bundles without `node_modules`).

Defects this suite caught, and why the checks look the way they do:

- axios 1.20 sends `cache: 'default'` on every fetch, which workerd rejects
  ("Unsupported cache mode: default"), so every request failed there. The SDK
  now sets `fetchOptions: { cache: 'no-store' }` only where `Request` refuses
  `default` but accepts `no-store`. `workerd` runs once with axios 1.20
  pinned.
- Remix's fetch polyfill rejects an aborted fetch with its own `Error` named
  `AbortError`, not a `DOMException`, so `watchDir().stop()` reported an
  `exitError` instead of exiting cleanly. Every abort check in the SDK now
  matches on the name (`isAbortError` in `src/v2/http.ts`).

## External tools

Everything except these comes from each runtime's own `package.json`:

| Tool | Runtime | Local install | CI |
| --- | --- | --- | --- |
| `bun` | `bun` | <https://bun.sh> | `oven-sh/setup-bun` |
| `deno` | `deno` | <https://deno.com> | `denoland/setup-deno` |
| `func` (Azure Functions Core Tools 4) | `azure-functions` | `npm i -g azure-functions-core-tools@4` | the same, pinned |
| Chromium for Playwright | `vite-browser` | downloaded by `run.sh` on first use | `playwright install --with-deps chromium` with the version pinned in `vite-browser/package.json` |

Inside a Nix dev shell, Playwright's host check may report system libraries as
missing even though Chromium starts; set
`PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=1` in that case.

## CI

`.github/workflows/sdk-typescript-runtimes.yaml` bundles the pinned v0 release,
builds and packs once, then runs one job per runtime (`run-all.sh --list`
supplies the matrix, so a new runtime directory gets a job automatically) with
`RUNTIME_TEST_REQUIRE_V0_BUNDLE=1` and `CI=true`. Each job installs only the
toolchain its runtime needs.

## Adding a runtime

1. Create `runtime-tests/<name>/` with a minimal `package.json`. Do not list
   `@daytona/sdk`: `install_sdk` installs the tarball.
2. Import the root package and pass it to `runChecks` from
   `./runtime-checks.mjs` (copied next to `run.sh` at run time; import it with
   the relative path from your source file). Print the result as JSON, or
   return it from the server route.
3. Add an executable `run.sh`:
   - start with `# shellcheck source-path=SCRIPTDIR/.. source=lib.sh` and
     `source "$RUNTIME_LIB"`, then call `require_binary` for any non-npm tool;
   - call `install_sdk`, build if needed, capture the JSON in an assignment
     (`result="$(…)"`, so a failing command fails the runtime) and pass it to
     `assert_response "$result"`;
   - for a server: `install_traps`, then
     `start_server_on_free_port <log> <url with {PORT}> <command with {PORT}>`
     (picks a free port, waits for the URL, retries a lost bind race) and
     `curl` the route. Never use fixed ports or `pkill`: runtimes run in
     parallel.
4. If the runtime needs a binary that is not on the GitHub runner, add a
   conditional install step to the workflow.
5. Extend `mock-api.mjs` only when a check needs another route, and keep it
   matching the contract.
