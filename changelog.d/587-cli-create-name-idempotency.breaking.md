**CLI: named creates replay by name** — `sandbox create*` with `--name` derives
its idempotency key from the name as the SDKs do, so creating the same name
again replays the first create; `--idempotency-key` overrides it.
