**Python: credentials out of diagnostics** — the generated wire models that
carry credentials now show `<redacted>` in `to_str()` and `repr()` instead of
the values (preview and signed-preview tokens and signed preview URLs,
sandbox access tokens, invitation tokens, newly created API keys, secret and
registry values, OTEL exporter headers, git credentials), and those models
also redact credential-named undeclared fields. Serialization (`to_dict()`,
`to_json()`, `model_dump()`) still carries the real values.
