**`secrets.set()` keeps the stored description and refuses an empty value** —
the API replaces the whole secret, so `secrets.set(name, value, ...)` without
`description` erased an existing secret's description. When `description` is
omitted (or `None` / `null`), the SDKs now read the secret first (one more
request, not atomic with the write; a failed read other than not-found fails
the call) and send its stored description (no read with `if_not_exists` / `ifNotExists`); `""` clears
it. An empty `value` is refused with `BadRequestError` before any request
instead of overwriting the stored value, as the CLI already refused it.
