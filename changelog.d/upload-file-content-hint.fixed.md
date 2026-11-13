**`upload_file` / `uploadFile` given content instead of a path** — passing
bytes (or any non-path) as the local path now raises `BadRequestError` naming
`write_bytes()` / `write_text()` (`writeBytes()` / `writeText()` in
TypeScript), the calls for in-memory content, instead of failing inside the
path handling.
