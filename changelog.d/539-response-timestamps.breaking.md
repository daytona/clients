**Timestamps are strings in both SDKs** — Python now refuses a number where
the contract has a date or timestamp string, in every response it maps (the
sandbox and its lifecycle policy, images, snapshots, preview endpoints, signed
preview links, the project budget, a build-log state's `deadline_at`), with
`BadRequestError` naming the field. TypeScript already refused one; Python's
generated models read a number as a Unix time.
