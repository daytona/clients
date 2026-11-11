**Blocking runs refuse `stdin="open"`** — `process.run()`, `sandbox.exec()`
and `session.run()` return no handle to write to, so a process started with
open stdin could never be fed or closed: the call hung until its timeout and
then left the process running (a session command also blocked the session).
They now raise `BadRequestError` before any request; `process.start()` and
`session.start()` keep `stdin="open"` / `stdin: 'open'`. In TypeScript
`RunOptions.stdin` and `SessionRunOptions.stdin` are typed `'closed'`.
