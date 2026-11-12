**TypeScript: a create `name` must be a string** — `create()`,
`createFromSnapshot()`, `createFromBuild()` and `createMacos()` refuse a `name`
that is not a string (or `null` / `undefined`, which mean no name) with
`BadRequestError` before any request, as Python does.
