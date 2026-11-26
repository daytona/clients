**TypeScript `updateLifecyclePolicy()`** — `stop` and `pause` actions are no longer
rejected locally when the API omits the sandbox's persistence; the API decides.
GPU `stop` and non-Linux `pause` are still rejected.
