**Create refuses environment names `update_env` refuses** — `create*(env=...)`
(`create*({ env })`, Go `CreateOptions.Env` / `CreateMacOSOptions.Env`) refuses
an empty name, a name with `=`, NUL, CR or LF (`env key is not a valid variable
name`) and a value with NUL (`env values must not contain NUL`) before any
request, as `update_env()` / `updateEnv()` / `UpdateEnv()` do. They used to
reach the server. The NUL-value message is now the same in every SDK.
