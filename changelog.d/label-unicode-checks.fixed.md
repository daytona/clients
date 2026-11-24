**Labels and names with unencodable text are refused locally** — a label key or
value, a `find_by_name()` / `findByName()` name, a create name or a fork name
holding a lone surrogate (Go: invalid UTF-8) is a `BadRequestError` naming the
field, before any request, the same in every SDK. Before, Python failed with
`UnicodeEncodeError`, TypeScript sent it and got a misleading server error, and
Go replaced the bytes silently.
