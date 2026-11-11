**CLI organization and project names** — `org create`, `org update --name`,
`project create` and `project update --name` refuse a name that is blank,
longer than 64 characters after trimming, contains control characters, or is
the reserved project name `default`, as the API does, with a usage error (exit
code `2`). A create is refused before any request. A rename is refused after
one read of the current name, because the API accepts the current name sent
back unchanged whatever it holds, and the CLI sends that unchanged name.
