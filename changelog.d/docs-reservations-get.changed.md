**`reservations.get` docs** — the method now has a docstring: it fetches one
Mac reservation by id and raises `NotFoundError` for one that does not exist
or belongs to another organization.
