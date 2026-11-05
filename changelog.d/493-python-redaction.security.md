**Python: credentials out of diagnostics** — the generated wire models' `to_str()`
and `repr()` show `<redacted>` instead of credential values (preview and
signed-preview tokens and signed preview URLs, sandbox access tokens,
invitation tokens, newly created API keys, secret and registry values, OTEL
exporter headers, git credentials), including credential-named undeclared
fields. Serialization (`to_dict()`, `to_json()`, `model_dump()`) still
carries the real values.
