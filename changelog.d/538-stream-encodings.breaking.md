**Streams in an unsupported encoding are refused** — in Python, a successful
streamed response in a content encoding other than gzip, `x-gzip`, deflate or
`identity` (no encoding) is refused with `BadRequestError` (400, `BAD_REQUEST`, source `SDK`) without
being decoded; an error response in one keeps its status's typed error (a 404
raises `NotFoundError`), with no code read from its body.
