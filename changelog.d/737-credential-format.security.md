**Malformed credentials are refused without echoing them** — the v2 client
(Python, TypeScript and Go) trims surrounding spaces, tabs and line breaks
from an API key or JWT, as it already did for one read from the environment,
so a key read from a file with its trailing newline keeps working. A
credential that is then blank (explicitly set) or not printable ASCII (a
non-ASCII or control character inside it) is refused before any request with
the SDK's bad-request error (Python and TypeScript `BadRequestError`, Go
`ErrBadRequest`), whose message names the option or environment variable and
never contains the value. Python no longer puts a header value the HTTP
client refuses (such as `Bearer <credential>`) into a `ConnectionError`, the
errors it chains or telemetry spans.
