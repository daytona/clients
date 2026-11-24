**Malformed credentials are refused without echoing them** — the v2 client
(Python, TypeScript and Go) refuses an API key or JWT that is not printable
ASCII without leading or trailing whitespace (a trailing newline, a
whitespace-only or non-ASCII value) with a `BadRequestError` naming the field
or environment variable, never the value, before any request. Environment
values are still trimmed first. Python no longer puts a header value the HTTP
client refuses (such as `Bearer <credential>`) into a `ConnectionError`, the
errors it chains or telemetry spans.
