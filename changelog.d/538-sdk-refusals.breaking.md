**Toolbox argument checks** — the invalid arguments TypeScript already refused
in the interpreter, git and filesystem calls (an invalid timeout, conflicting
interpreter context selectors, a commit without author or email, a pre-signed
URL on a handle without a signer) now throw `BadRequestError` (400,
`BAD_REQUEST`, source `SDK`) instead of a bare `DaytonaError`. Both SDKs now
also refuse, with that error and before any request: an `argv` that is not a
non-empty sequence of strings (TypeScript: an array), a git config `scope`
other than `'global'` / `'local'`, and a `delete_context()` /
`deleteContext()` id that is not a non-empty string; TypeScript a
`git.clone()` `insecureSkipTls` that is not a boolean and a `pty.start()` /
`attach()` command that is not a string (omitted still starts the default
shell); Python a `pty.start()` `terminal` that is not a `TerminalSize`.
