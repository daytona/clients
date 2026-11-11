**CLI organization and project names** — `org create`, `org update --name`,
`project create` and `project update --name` refuse, before any request, a
name that is blank, longer than 64 characters after trimming, contains control
characters, or is the reserved project name `default`, as the API does. A
rename that sends the current name back unchanged is always sent, as the API
accepts it.
