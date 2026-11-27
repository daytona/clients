**Volume ids and names** — `VolumeMount` takes a volume id or name,
`sandbox.volumes.pull()` takes only an id, `id_only` refuses a name with 400,
and a deleted volume is still readable by id but not by name.
