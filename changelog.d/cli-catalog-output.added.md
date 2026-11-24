**CLI: catalog output keeps the catalog's defaults** — `region list` adds
`default_region_id` next to `items` in JSON and YAML and marks that region
`(default)` in the table; `cpu-type list` adds the catalog's `regions` (each
region's default architecture and CPU type) and `arches` next to `items`, and
lists the region defaults under the table; `sandbox usage --history` prints
one table row per usage period (or says that history is not available)
instead of the `history true` request switch. The existing JSON fields are
unchanged.
