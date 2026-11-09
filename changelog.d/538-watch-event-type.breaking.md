**Open filesystem event kinds** — `FilesystemEvent.type` is an open string in
both SDKs: TypeScript `FilesystemEventType` is no longer an `enum` but the
known string literals (`'create'`, `'write'`, `'remove'`, `'rename'`,
`'chmod'`) widened to any string, and Python `FilesystemEventType` is no
longer a `str` `Enum` but a `Literal` of the known values widened to `str`. A
kind a later sandbox reports is delivered to the callback as its string
instead of ending the watch. Compare with string literals
(`event.type == "create"`) instead of the removed enum members
(`FilesystemEventType.CREATE`).
