**Volumes** — TypeScript `volumes.create(name, sizeGib)` becomes
`volumes.create({ name, sizeGib })` (`sizeGib` a positive integer), as in
Python's `create(name=..., size_gib=...)`;
TypeScript `volumes.get(nameOrId, create, { idOnly })` becomes `get(nameOrId,
{ create, idOnly })` (Python keeps `get(name_or_id, create=...,
id_only=...)`); `volumes.delete()` takes a name or id: Python's first argument
is now `name_or_id` (it was `volume_id`), and TypeScript no longer takes a
`Volume` (`delete(volume.id)`). Python `Volume.options` is a plain `dict`
owned by the volume (`Mapping` to type checkers), so `dataclasses.asdict`,
`copy.deepcopy`, `pickle` and `json.dumps` work on it.
