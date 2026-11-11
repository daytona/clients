**Python `Sandbox.raw_state` is `None` until known** — the wire status reads
`None` instead of `""` before a status is known (a create response without
one, an events frame without one), as TypeScript's `rawState` reads
`undefined`.
