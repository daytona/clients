**Create validates environment names like `update_env`** — `create*(env=...)`
(`create*({ env })`, Go `CreateOptions.Env` / `CreateMacOSOptions.Env`) refuses
an empty name, a name with `=`, NUL, CR or LF (`env key is not a valid variable
name`) and a value with NUL (`env values must not contain NUL`) before any
request, as `update_env()` / `updateEnv()` / `UpdateEnv()` do. They used to
reach the server. The NUL-value message is now the same in every SDK. Python
and TypeScript also refuse a lone surrogate in an env name or value (it has no
UTF-8 form), in both calls. TypeScript `updateEnv()` refuses a `Map` (or
another built-in) instead of silently sending `{}`; plain objects,
`process.env` and class instances are still sent by their own entries.
