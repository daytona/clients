**CLI: `sandbox access-token`** — mints a scoped, short-lived access token for
one sandbox (`--operations`, `--ports`, each comma-separated or `none`, and
`--ttl`) and prints it with its expiry and grant. Reserved daemon ports and
empty entries are refused before the request, and a response without a
usable token or expiry is an error rather than printed.
