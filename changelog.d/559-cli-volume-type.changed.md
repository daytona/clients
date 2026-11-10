**CLI: `volume create` checks the type** — a volume type other than `disk` or
`bucket` (an empty one included, from `--type` or `--file`) is refused before
any request, as the SDKs refuse it; the CLI used to send it to the API.
