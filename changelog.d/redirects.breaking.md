**The SDKs never follow an HTTP redirect** — a 3xx response is a
`DaytonaError` carrying its status (`status_code` / `statusCode`, source
`SDK`) in both SDKs, its body is not read, and its `Location` is never
requested. TypeScript followed redirects (cross-host ones included) and
reported the target's status; Python read a 3xx body as a success and failed
with a misleading malformed-response error, and its sandbox events connection
followed a redirected WebSocket handshake.
