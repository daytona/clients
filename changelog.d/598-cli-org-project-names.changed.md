**CLI organization and project names** — `org create`, `org update --name`,
`project create` and `project update --name` refuse, before any request, a
name that is blank, longer than 64 characters after trimming, contains control
characters, or is the reserved project name `default`, as the API does. A
rename to the current name is still sent when that name is longer.
