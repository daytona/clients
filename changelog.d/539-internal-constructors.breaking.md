**Handles and services are built by the SDK** — the handle and service classes
are no longer constructed by callers: TypeScript's constructors refuse callers
(get them from the client or the sandbox; their published declarations carry
no wire, axios or transport types), and Python's take `_`-prefixed parameters.
Python's model dataclasses stay constructible, keyword-only (`ImageBuilder`,
`ImageRef` and `SnapshotRef` included).
