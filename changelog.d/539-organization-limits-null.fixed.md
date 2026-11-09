**Null organization limits read as absent** — `get_limits()` / `getLimits()`
read a `null` `members` or `sandbox_resources` as absent in TypeScript, as
Python does; it threw a raw `TypeError`.
