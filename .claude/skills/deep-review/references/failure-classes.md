# SDK and CLI failure classes

Use this catalog as a set of hypotheses, not a historical comment log.
Every applicable class gets a concrete probe and a recorded outcome.

## 1. Deadlines, retries, and guards

- **Deadline-only exit:** zero, absent, infinite, or sub-millisecond budgets
  cause a busy retry loop or a different result across SDKs. **Probe:** use
  instant, exact-budget, and never-responding peers; record request count
  and total elapsed time in both SDKs, converting seconds to milliseconds.
- **Budget double-spend:** create, wait, and fallback each receive the full
  original timeout. **Probe:** consume most of the budget in the first
  request and assert the total is bounded by one budget plus stated overhead.
- **Guard rejects correct behavior:** a retry limiter counts its own
  reissues or rejects a peer that waits for the documented interval.
  **Probe:** after a hostile-peer test, run conforming short/long waits and
  terminal responses exactly at the boundary; guards must not misfire.
- **Timeout implies cancellation that never happened:** a local deadline
  ends waiting but leaves remote work active, or cleanup abandons background
  tasks. **Probe:** time out through the public method, inspect cancel or
  interrupt requests and task/socket cleanup, and compare the documented
  semantics; do not assume every local timeout promises remote termination.
- **Unsafe retries:** a response lost after a mutating request causes a
  second mutation. **Probe:** accept a request then drop its response; count
  writes and check the documented idempotency/retry rules.

## 2. Streams, loss, and ordering

- **Replay-to-live gap:** a replay cursor is discarded or reused incorrectly,
  losing or duplicating data. **Probe:** insert a frame during attachment
  and reconnect with a cursor; assert the expected sequence and deduplication.
- **Read failure becomes success:** truncated output or an early disconnect
  is treated as EOF despite a required terminal frame. **Probe:** close at
  each frame boundary and mid-frame; assert a typed failure, not completion.
- **Framing differs across SDKs:** newline forms, chunk boundaries, split
  UTF-8, or multi-line events change the parsed result. **Probe:** feed the
  same LF/CRLF/CR-only, comment, empty, split-codepoint, and terminal fixtures
  through both real parsers; enforce the documented accepted grammar.
- **Cancellation poisons shared work:** one subscriber's cancellation closes
  a shared stream or cached operation for everyone. **Probe:** attach two
  callers, cancel the first, and verify the second continues; close all and
  check the shared transport is released.
- **Close races with lazy initialization:** work created after close leaks
  or a failed initialization is cached forever. **Probe:** synchronize two
  first uses with close, then retry a failed first use; assert the documented
  reuse/closure behavior and resource counts.
- **Async surface blocks the caller's loop:** synchronous I/O or the sync
  bridge runs inline. **Probe:** stall the dependency while an independent
  coroutine advances; test cancellation and loop/thread affinity as well.

## 3. Wire contracts and routing

- **Request does not match the committed contract:** wrong verb, route,
  body/query placement, nullability, or field vocabulary. **Probe:** capture
  the complete outgoing request, compare with `contract/`, and run each SDK's
  route drift tests. Do not fabricate a new contract to match the code.
- **Wrong resource or URL provenance:** a derived handle retains its source
  transport, repeats an identifier, or ignores a returned URL. **Probe:** use
  distinct source/destination identifiers and endpoints; assert the exact
  URL and credential scope of every request.
- **Config precedence or units drift:** blank values override defaults,
  namespace-specific settings leak, CLI flags lose to profiles/environment,
  or seconds become milliseconds twice. **Probe:** give every candidate a
  different value, test blank/unset separately, and inspect the request in
  both SDKs and the CLI where their behavior is shared.
- **Open wire enum becomes a closed cast:** unknown strings fail parsing or
  disappear. **Probe:** return a future enum value plus unknown fields;
  verify the documented public `unknown` value and raw-value sibling survive.
- **Pagination hides work or never terminates:** a page method fetches all
  pages, an iterator drops options, or a cursor cycle repeats forever.
  **Probe:** count page requests; return empty, repeated, and cycling cursors
  with filters, and assert results and bounded termination.
- **Unnecessary round trips or lossy result adapters:** a helper discards
  terminal reason/signal information or adds redundant reads. **Probe:**
  count requests per operation and return non-success terminal outcomes;
  compare base behavior and both SDKs' result models.

## 4. Errors and validation

- **Status-only or code-only dispatch:** identical codes from different
  sources map to the wrong class, and generic errors lose extras or cause.
  **Probe:** run `(source, code, status)` vectors from `contract/`, including
  unknown codes, generic statuses, malformed bodies, and per-code extras;
  assert category, concrete class, message, and structured data in both SDKs.
- **String matching replaces structured failure:** localized text, unrelated
  words, or an unknown enum become misleading errors. **Probe:** vary error
  wording while holding structured fields fixed, then vary the fields with
  identical text; classification must follow the documented boundary.
- **Validation checks one value but sends another:** trimming, encoding, or
  case normalization is discarded. **Probe:** use padded, mixed-case,
  Unicode, reserved-character, and empty inputs; assert the actual sent value.
- **Validation accepts unsupported options or breaks valid inputs:** a guard
  accepts undocumented options in one SDK or rejects a supported shape.
  **Probe:** test unsupported inputs and valid neighbors in both SDKs;
  confirm exports and CLI help match their respective documented surfaces.

## 5. Parity, generation, and exports

- **One-sided feature or behavioral fix:** names match but defaults, errors,
  cancellation, or optional values diverge. **Probe:** drive both SDKs with
  the same wire fixture, record outputs and requests, and check deliberate
  language-specific exceptions before filing one combined parity finding.
- **Generated sync surface is stale:** async parameters or exports change
  without a matching generated twin, or the bridge changes semantics.
  **Probe:** run `test_sync_surface.py`, regenerate in a disposable copy,
  compare output, and call the sync and async public methods on the same
  response/error fixtures. Never repair drift by editing generated files.
- **Generated wire types drift from contract:** output is patched by hand,
  nullability aliases diverge, or an enum annotation is ignored. **Probe:**
  run `scripts/gen-contract-types.sh --check`, then typecheck/build all
  affected consumers and parse a future-value fixture.
- **Export exists only in source:** missing runtime binding, declaration,
  package export, or accidental exposure of a hidden client. **Probe:**
  enumerate intentional exports from an installed Python wheel and ESM/CJS
  tarball consumers, compile minimal type consumers, and test documented
  forbidden imports separately from accidental missing names.
- **Cross-copy error identity breaks:** ESM/CJS or namespace loads produce
  incompatible catch behavior. **Probe:** load both entry points in one
  process, construct errors from each, and assert documented root and
  version-specific matching, including user subclasses.

## 6. Credentials, local files, and command arguments

- **Secrets escape through output or forwarding:** logs, exceptions, argv,
  config permissions, or cross-origin redirects expose credentials.
  **Probe:** use synthetic sentinel tokens, capture stdout/stderr and
  requests, redirect between two local origins, and inspect created config
  modes; verify only the intended recipient receives the credential.
- **Path or argument injection:** user input changes the destination path
  or becomes a command option. **Probe:** use `..`, absolute paths, symlink
  parents, leading dashes, spaces, and shell metacharacters at CLI download,
  extraction, and subprocess boundaries; assert confinement and literal argv.
- **Partial local writes corrupt state:** cancellation truncates a profile,
  credential file, or download. **Probe:** fail before/during replacement
  and verify the previous state remains usable with proper permissions.

## 7. CLI automation behavior

- **Human output contaminates machine output:** progress or diagnostics
  corrupt JSON/stdout, or a failure returns status zero. **Probe:** invoke
  the real command without a TTY against an `httptest` peer; parse stdout,
  inspect stderr, and assert exit status on success and failure.
- **Prompting or cancellation hangs automation:** stdin is required despite
  noninteractive flags, a child ignores cancellation, or cleanup runs only
  on success. **Probe:** run with closed stdin and a bounded test context,
  cancel during a stalled request, and check child/file/socket cleanup.

## 8. Test shape and evidence

- **Test passes for the wrong reason:** validation fails before the intended
  error, mocks bypass the parser, or normalized goldens erase units/values.
  **Probe:** prove the intended request was reached, assert exact meaningful
  scalars, and temporarily inject the named regression in a disposable copy;
  the test must fail for it.
- **Timing tests certify scheduler luck:** wall-clock thresholds fluctuate
  on shared machines. **Probe:** use a controlled clock/handshake for branch
  coverage; keep real elapsed-time measurements as separate bounded drivers.
- **Self-skip hides missing functionality:** a required feature is absent
  but the test exits green. **Probe:** enumerate skip conditions and observed
  counts; distinguish missing optional credentials from a broken feature.

## 9. Docs, packaging, and release checks

- **Description or fragment overstates the diff:** stale counts, nonexistent
  paths, unsupported options, or a guarantee the implementation cannot meet.
  **Probe:** verify each claim at the head, recount results, execute README
  examples, and match every user-facing change to `changelog.d/`.
- **Package tests accidentally import the checkout:** missing package data,
  loaders, typing markers, or runtime dependencies go unnoticed. **Probe:**
  install wheel and tarball into clean consumers outside the source tree;
  test root/versioned imports, ESM/CJS, and relevant browser entry points
  without source paths or dev dependencies leaking into resolution.
- **Bundled namespace differs from the shipped artifact:** a source-only
  stub is mistaken for a complete release, or the version pin and contents
  disagree. **Probe:** inspect the public bundling/release scripts and pin,
  test the real bundle when available, and label stub-only coverage honestly.
- **CI or release boundaries are incomplete:** a new package escapes checks,
  or untrusted contributions reach secrets/publishing permissions. **Probe:**
  enumerate package paths against workflow filters and matrices; trace
  checkout refs, permissions, and artifact inputs without executing publish
  steps. Check license headers, DCO, and CLA status without modifying them.
