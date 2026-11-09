**Compressed streams are decoded in bounded steps** — in Python, a streamed
response (process log streams, directory watches, `download_file()`, build
logs) compressed with gzip (also sent as `x-gzip`) or deflate is decoded by
the SDK at most 64 KiB at a time, as capped reads are, so one small compressed
chunk can no longer expand into a large event before the stream's bound is
checked. A successful response in any other content encoding is refused with
`BadRequestError` (400, `BAD_REQUEST`, source `SDK`) undecoded; an error
response in one keeps its status's typed error (a 404 raises `NotFoundError`),
with no code read from the body. Compressed data that stops before its end
ends the stream with `ConnectionError`, as a capped read does. TypeScript
runtimes already decode a stream in small chunks (and pass incomplete
compressed data through, the documented exception).
