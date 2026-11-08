**TypeScript: unknown configuration keys are rejected** — the root
`DaytonaConfig` and `SandboxEventsConfig` now throw `BadRequestError` for an
enumerable key they do not define (own or inherited), instead of ignoring it,
and for a value that is not an options object (a primitive, an array, a
function, or a built-in such as a `Date` or a `Map`; the root constructor
still reads `null` as no configuration, like Python's `Daytona(None)`); a
renamed key names its
replacement (``DaytonaConfig: `requestTimeout` was renamed to
`requestTimeoutSeconds` (seconds)``). `SandboxEventsConfig.reconcileIntervalMs`
and `idleCloseMs` are renamed `reconcileIntervalSeconds` and
`idleCloseSeconds` (seconds). The root constructor is
`new Daytona(config?: DaytonaConfig)`: the second, internal parameter is gone.
