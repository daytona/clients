**Truncated compressed streams end with `ConnectionError`** — in Python, gzip
or deflate data that stops before its end, inside a complete HTTP message,
ends the stream with `ConnectionError` (source `SDK`), as a capped read does.
TypeScript passes such data through: its runtimes decode it and accept it (a
documented difference between the SDKs).
