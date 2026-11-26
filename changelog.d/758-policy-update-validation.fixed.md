**`updateLifecyclePolicy()` / `update_lifecycle_policy()`** — TypeScript and
Python no longer reject `stop` and `pause` locally when the API omits the
sandbox's persistence; the API decides. Persistent Linux sandboxes accept both.
GPU `stop` and non-Linux `pause` are still rejected.
