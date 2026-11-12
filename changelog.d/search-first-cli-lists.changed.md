**CLI lists read search first** — `daytona sandbox list`, `image list` and `snapshot list` read
the search routes where the deployment serves them (one request across every region,
eventually consistent: `get` is the fresh read) and fall back to their list endpoints where it
does not. On the search path `sandbox list --region R` sends R as the search's region filter,
while `image list --region R` filters the results itself, keeping every image available in R
(its `region_id` or any `region_ids` entry), not only those homed there, so a page can hold
fewer than `--limit` items.
