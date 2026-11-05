**Python: credentials out of diagnostics** — the generated wire models' `to_str()`
and `repr()` show `<redacted>` instead of credential values (preview and
signed-preview tokens, sandbox access tokens, invitation tokens, newly created
API keys, secret and registry values, git passwords). Serialization
(`to_dict()`, `to_json()`, `model_dump()`) still carries the real values.
