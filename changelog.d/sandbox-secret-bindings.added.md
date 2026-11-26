**Sandbox secret bindings** — `get_secrets()` / `replace_secrets()`
(`getSecrets()` / `replaceSecrets()`, Go `GetSecrets` / `ReplaceSecrets`) now
return `bindings`: each attached secret with the environment variable it is
injected under (`SandboxSecretBinding`, `name` and `env`). Empty when the API
does not report them.
