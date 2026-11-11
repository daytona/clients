**`v0`: image exec forms are valid JSON** — with the 0.231.1 bundle,
`Image.cmd()` / `entrypoint()` in `d.v0` write exec forms as valid JSON, so
quotes and backslashes in an argument no longer break the Dockerfile line.
