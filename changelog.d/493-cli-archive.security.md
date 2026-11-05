**CLI directory copies** — on Windows, `daytona sandbox cp -r` downloads now
create every entry relative to its already-opened parent directory, so a
concurrent rename or link swap cannot redirect a write outside the
destination. An existing file is replaced with a new file, so other hard
links to it keep their content. Extracting into a filesystem root (`/`,
`C:\`) no longer rejects valid entries.
