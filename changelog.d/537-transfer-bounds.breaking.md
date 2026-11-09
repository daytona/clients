**File transfers are idle-bounded and buffered responses are capped** — file
transfers (`read_bytes` / `readBytes`, `read_text` / `readText`, `read_many` /
`readMany`, `write_bytes` / `writeBytes`, `write_text` / `writeText`,
`write_many` / `writeMany`, `upload_file` / `uploadFile`, `download_file` /
`downloadFile` and the computer-use recording `download`) used to be bounded
differently in each SDK: Python cut them after 30 s without bytes moving (its
per-phase I/O timeout), and TypeScript gave the in-memory ones a 24-hour total
deadline and `uploadFile` / `downloadFile` a deadline only until the response
headers. They now have no total deadline in either SDK, and fail with `ClientTimeoutError` (408,
`REQUEST_TIMEOUT`, source `SDK`) once no bytes move for the client's request
timeout (default 60 s; `0` = no idle bound): while connecting, while waiting
for the response, and between body chunks. A transfer that keeps moving
bytes is never cut off. In TypeScript, where the runtime reports no upload
progress (a browser or worker sending a request body over 64 KiB) the bound
starts when the response arrives, so a server that never answers holds the
upload until the caller aborts it. Every response body the SDKs hold in memory
whole (API responses, the in-memory file reads, bulk reads, error bodies) was
read without a size limit; it is now capped by the new client setting
`max_response_bytes` / `maxResponseBytes`: an integer from `0` (no limit) to
2^53 − 1; left unset (Python `None`, TypeScript `undefined` or `null`) it is
1 GiB, and any other value (negative, fractional, boolean, string, or above
2^53 − 1) is a `BadRequestError`. A declared
`Content-Length` over the cap is refused before the body is read, and a body
that passes it stops being read; the connection is closed. Such a response
raises `PayloadTooLargeError` (413, `PAYLOAD_TOO_LARGE`, source `SDK`), and
an error response raises its status's typed error saying its body was too
large to read. `read_bytes` / `readBytes`, `read_text` / `readText`,
`read_many` / `readMany` (for the whole multipart body) and the recording
`download` take the same option per call (`max_response_bytes=` /
`{ maxResponseBytes }`) to raise, lower or lift the cap. Streamed bodies
(`download_file` / `downloadFile`, log, event and build-log streams, watches)
are not capped: use `download_file` / `downloadFile` for large files or
content from a sandbox you don't trust.
