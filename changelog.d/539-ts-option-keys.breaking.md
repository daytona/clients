**TypeScript: unknown option keys are rejected** — every client, sandbox
lifecycle and platform-service options object (nested `resources`, port and
volume-mount entries and `IfMatchOptions` included) now throws
`BadRequestError` for a key it does not define, instead of ignoring it; a
renamed key names its replacement
(``LifecycleWaitOptions: `timeout` was renamed to `timeoutSeconds` (seconds)``).
A value that is not an options object, such as the former positional
`volumes.get(name, true)`, is refused too.
