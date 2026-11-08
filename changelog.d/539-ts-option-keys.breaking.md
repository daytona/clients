**TypeScript: unknown option keys are rejected** — every client, sandbox
lifecycle and platform-service options object (nested `resources`, port and
volume-mount entries and `IfMatchOptions` included) now throws
`BadRequestError` for a key it does not define, instead of ignoring it; a
renamed key names its replacement
(``LifecycleWaitOptions: `timeout` was renamed to `timeoutSeconds` (seconds)``).
Every one of these options parameters also refuses a value that is not an
options object — `null`, a boolean, a string, a number, an array or a
function, such as the former positional `volumes.get(name, true)` — before
any request; only an omitted options argument takes the defaults, and
`lifecyclePolicy: null` inside create options still means no policy.
`labels` (on create, search and `setLabels()`) must be a plain object of
strings: a `Map`, a class instance or inherited keys are refused, and
`volumes.create()` requires a non-empty `name`.
