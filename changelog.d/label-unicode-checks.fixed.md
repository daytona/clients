**Labels and names with unencodable text are refused locally** — a label key or
value, a `find_by_name()` / `findByName()` / `FindByName` name, a create name or
a fork name that cannot be encoded as UTF-8 is a bad-request error (400,
`BAD_REQUEST`, source `SDK`) naming the field, before any request. Python and
TypeScript raise `BadRequestError` for a lone surrogate (`label values must not
contain a lone surrogate`); Go returns an error matching `ErrBadRequest` for
invalid UTF-8 (`label values must be valid UTF-8`). Before, Python failed with
`UnicodeEncodeError`, TypeScript sent it and got a misleading server error, and
Go replaced the bytes with U+FFFD silently.
