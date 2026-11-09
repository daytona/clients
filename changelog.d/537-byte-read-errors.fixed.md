**TypeScript byte reads keep the server's error** — a failed `fs.readBytes()`,
`fs.readText()`, recording download or other call that reads its body as bytes
now throws the error class, code and message the server sent (for example
`FileNotFoundError` with `FILE_NOT_FOUND`), as the Python SDK does, instead of
a bare status error. A transfer's error body uses the same idle timeout as its
success body, and the response size cap (`maxResponseBytes`) still applies to
it.
