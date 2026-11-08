**TypeScript toolbox argument checks** — the invalid arguments TypeScript
already refused in the interpreter, git and filesystem calls (an invalid
timeout, conflicting interpreter context selectors, a commit without author or
email, a pre-signed URL on a handle without a signer) now throw
`BadRequestError` (400, `BAD_REQUEST`, source `SDK`) instead of a bare
`DaytonaError`; and both SDKs now refuse, before any request, a `git.clone()`
`insecureSkipTls` that is not a boolean (TypeScript) and an `argv` that is not
a non-empty list of strings (both).
