**Toolbox handles reconnect from one JSON shape** — `ProcessHandleJSON`
(`{ sandboxId, processId, kind? }`), `SessionJSON` (`{ sandboxId, sessionId }`)
and `InterpreterContextJSON` (`{ sandboxId, contextId, language }`) are the
same camelCase JSON in both SDKs, so a handle stored by one reconnects in the
other through `process.connect()`, `process.connect_session()` /
`connectSession()` and `interpreter.connect_context()` / `connectContext()`
(Python no longer reads the snake_case context identity earlier pre-releases
wrote: both SDKs refuse it with `BadRequestError`).
Python removes `ProcessHandleRef`, `ProcessHandle.from_json()` / `ref()` /
`bind()`, `Session.from_json()`, the handle's duplicate `process_id`
attribute and `InterpreterContext.bind()`; contexts gain `to_json()`.
Construction of the process, session, PTY, interpreter, filesystem, watch,
git, LSP and computer-use services and handles is internal: Python
constructor parameters are private, TypeScript constructors refuse a call
from outside the SDK, and TypeScript no longer exports `ComputerUseTransport`.
`process.connect()` / `connect_session()` / `connectSession()` refuse an
identity without a non-empty id with `BadRequestError` (Python
`connect_session(5)` raised `AssertionError`).
