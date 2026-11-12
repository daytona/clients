**CLI organization and project names** — `org create`, `org update --name`,
`project create` and `project update --name` refuse a name that is blank,
longer than 64 characters after trimming or contains control characters, and
`project create` / `project update --name` also refuse the reserved project
name `default`, as the API does, with a usage error (exit
code `2`). A create or a blank rename is refused before any request. Any other rename
is refused after one read of the current name (left to the API when the
credential may not read it): a rename to the current name is sent and left
to the API.
