**Process `env` must hold strings** — `process.start()` / `run()`,
`sandbox.exec()`, `create_session()` / `createSession()` and `pty.start()`
refuse an `env` that is not a mapping (Python) or plain object (TypeScript) of
strings with `BadRequestError` before any request (a `Map`, an array or a
non-string value was sent mangled).
