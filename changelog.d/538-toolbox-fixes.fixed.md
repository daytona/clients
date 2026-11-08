**Toolbox fixes (TypeScript)** — a PTY `attach()` whose socket closes before
it opens now rejects instead of hanging; computer-use results tolerate `null`
optional fields from the API (an accessibility node with `children: null` no
longer throws); interpreter code runs ask for `on_exit_ttl` log retention
like Python; `listContexts()` skips the legacy shell kernel like Python; and
SDK-side refusals in the interpreter, git and filesystem calls throw
`BadRequestError` with source `SDK` instead of a bare `DaytonaError`;
`git.clone({ insecureSkipTls })` refuses a non-boolean value;
`ComputerUseMouseDragRequest.button` is optional (`'left'` by default); and
both SDKs accept a computer-use status with an explicit `cause: null` outside
the failed state, as the API allows. Python validates an LSP completion
context's `trigger_kind` and `trigger_character` like TypeScript.
