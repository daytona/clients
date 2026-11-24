**CLI: credentials must be printable ASCII** — a credential with a control
character (such as a trailing line break) or a non-ASCII character, from
`--api-key`, `DAYTONA_API_KEY`, a saved profile or `daytona login --token`,
is refused before any request (exit 2) with an error naming its source and
never its value. Before, a line break was reported as a network problem
("check the API URL … and your network") and a non-ASCII character was sent
as raw UTF-8.
