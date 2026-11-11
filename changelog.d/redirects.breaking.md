**The SDKs never follow an HTTP redirect** — a 3xx response is a
`DaytonaError` carrying its status (`status_code` / `statusCode`, source
`SDK`) in both SDKs, its body is not read, and its `Location` is never
requested, a build-context part upload to storage included. In a browser,
fetch hides a redirect's status, so the same error carries none. TypeScript's
API requests followed redirects (cross-host ones included) and reported the
target's status, and its storage part uploads failed a redirect as a
connection error after retrying; Python read a 3xx body as a success and
failed with a misleading malformed-response error (a storage part upload with
`BadRequestError`), and its sandbox events connection followed a redirected
WebSocket handshake.
