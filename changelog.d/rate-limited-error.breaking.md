**`RateLimitedError` is removed** — the `RATE_LIMITED` code raises
`RateLimitError` itself, as `TOO_MANY_REQUESTS` does; read `code` to tell them
apart. The near-identical subclass name was easy to confuse with its base.
`LimitExceededError` and `OverloadedError` stay.
