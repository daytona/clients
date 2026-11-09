**An empty `allowed_domains` is refused** — `secrets.set()` refuses an empty
`allowed_domains` / `allowedDomains` with `BadRequestError` before the
request, in both SDKs, as the API rejects it (`["*"]` allows any verified
host).
