**CLI: a dropped attach is an error** — when the connection of `process attach`
or `exec --tty` drops, the command now fails with an error instead of ending as
a finished stream, so scripts that treated it as success see a non-zero exit.
