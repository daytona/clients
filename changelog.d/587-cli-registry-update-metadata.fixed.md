**CLI: Preserve registry usernames on update** — `registry set` requires
`--username` only for a new registry and preserves omitted metadata on updates.
Help clarifies that the API still requires resupplying a password for every
non-role update, including description-only changes.
