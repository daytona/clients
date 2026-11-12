**One `SandboxHandleJSON` in both SDKs** — `to_json()` / `toJSON()` return the
same camelCase `SandboxHandleJSON` shape, with the same fields and values in
both SDKs and no URL or credential: timestamps are the server's strings,
verbatim, and integral sizes are integers. It is a snapshot only: reconnect
with `get(json["sandboxId"])`.
