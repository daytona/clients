**`volumes.get(name, create=True)` / `{ create: true }` / `Get` with `Create`
return the volume a racing caller created** — when the create answers 409
`CONFLICT` because another caller created the same name first, the volume is
read again by name (a few times over about a second) and returned, instead of
raising `ConflictError`. Other conflicts and errors are raised unchanged.
