**One `SandboxHandleJSON` in both SDKs** — `to_json()` / `toJSON()` return the
same camelCase `SandboxHandleJSON`, byte for byte, with no URL or credential:
timestamps are the server's strings, verbatim, and integral sizes are
integers. It is a snapshot only: reconnect with `get(json["sandboxId"])`.
