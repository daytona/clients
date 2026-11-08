TypeScript refusals the SDK raises itself in the interpreter, git and
filesystem calls are `BadRequestError` (400, `BAD_REQUEST`, source `SDK`)
instead of a bare `DaytonaError`; `git.clone({ insecureSkipTls })` refuses a
non-boolean value.
