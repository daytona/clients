**CLI: `whoami` reports only what it knows** — `org_id` and `project_id` hold
IDs only; a selection given by name stays in `org_ref` / `project_ref`, and
`context_resolved` is `false` when a name could not be resolved. With no
credential, `credential_source` is omitted (it was `""`).
