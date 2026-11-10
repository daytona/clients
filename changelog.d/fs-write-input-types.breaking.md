**Filesystem writes check their content type** — text writes
(`write_text()` / `writeText()`) refuse anything but a string, and binary writes
(`write_bytes()` / `writeBytes()`, `write_many()` / `writeMany()` values) take
any bytes-like value and send exactly its bytes: Python `bytes`, `bytearray`
and `memoryview` (`bytearray` and `memoryview` raised `AttributeError`, and a
`str` was written as text), TypeScript `Uint8Array`, `ArrayBuffer` and any
other view (an `ArrayBuffer` was written as NUL bytes, a number or an object as
its string form). Anything else is a `BadRequestError` before any request.
