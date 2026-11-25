**`create_timeout_seconds` range** — the server accepts `-1` (its maximum)
or 1 up to a deployment-set maximum (a day by default); it refuses `0`, and
any value above that maximum, with 400.
