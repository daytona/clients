**`update_lifecycle_policy()` / `updateLifecyclePolicy()` / `UpdateLifecyclePolicy()`** —
Python, TypeScript and Go no longer reject `stop` and `pause` locally when the API omits the
sandbox's persistence; the API decides. Persistent Linux sandboxes accept both.
GPU `stop` and non-Linux `pause` are still rejected.
Lifecycle-policy updates on a sandbox class the SDK does not know are now sent
to the API instead of refused with `UnsupportedOperationError` / `ErrUnsupportedOperation`.
