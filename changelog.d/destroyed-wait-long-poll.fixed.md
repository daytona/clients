**Waiting for `destroyed` holds its long poll** — `wait_for_state(['destroyed'])`
/ `waitForState(['destroyed'])` no longer re-polls the API every 200 ms while a
sandbox is `destroying`: the wait now holds one long poll until the deletion
ends, as the CLI's `sandbox delete` already did. A deletion that takes minutes
costs a handful of requests instead of hundreds.
