**Interpreter contexts** — a context's id is `contextId` / `context_id` in
both SDKs: TypeScript `createContext({ id })` and `context.id` become
`contextId`. `run()` selects a context with one `context` option (a handle,
its `toJSON()` / `to_json()` identity, or an id together with `language`);
TypeScript removes `sessionId`, Python removes `get_context()` (use
`connect_context()`), and `sandbox.runCode()` / `run_code()` follow. A
context's own `run()` takes no `env` (it was always refused); set it on
`createContext()` / `create_context()`. TypeScript runs with
`timeoutSeconds: 0` leave their setup requests unbounded, like Python. Python
removes the unused `InterpreterDisplay`.
