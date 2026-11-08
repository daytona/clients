**TypeScript: unknown option keys are rejected** — every client, sandbox
lifecycle and platform-service options object (nested `resources`, port and
volume-mount entries included) now throws `BadRequestError` for a key it does
not define, instead of ignoring it; a renamed key names its replacement
(``LifecycleWaitOptions: `timeout` was renamed to `timeoutSeconds` (seconds)``).
