**Volumes** — `volumes.create()` takes `name` and `size_gib` / `sizeGib`
(TypeScript `{ name, sizeGib }`, a positive integer) and creates a disk
volume; `volumes.get(name_or_id, create=…, id_only=…)` / `get(nameOrId, {
create, idOnly })`; `volumes.delete()` takes a name or id. Python
`Volume.options` is a plain `dict` owned by the volume (`Mapping` to type
checkers), so `dataclasses.asdict`, `copy.deepcopy`, `pickle` and `json.dumps`
work on it.
