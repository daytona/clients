**`secrets.set()` refuses an empty value** — an empty `value` is refused with `BadRequestError` before any request
instead of overwriting the stored value, as the CLI already refused it.
