**Waiting for `destroyed` holds its long poll** — `delete()` and
`wait(until="destroyed")` / `wait({ until: 'destroyed' })` no longer re-poll
the API every 200 ms while a sandbox is `destroying`: each long poll now stays
open while the deletion runs, as the CLI's `sandbox delete` already did. A
deletion that takes minutes costs a handful of requests instead of hundreds.
