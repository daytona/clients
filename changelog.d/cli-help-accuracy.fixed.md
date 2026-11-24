**CLI help** — command help now matches the CLI's behaviour: `secret set`
requires a value and, on create, `--allowed-domain`; key, org-key,
service-account and member commands mark `--permission` / `--role` as
required; `reservation create --auto-renew` no longer mentions `create-macos`;
`image|snapshot|volume get` take an id or a name; `volume create` explains
that the positional name wins over `--file`; `sandbox create --volume`
documents the `:ro` grammar; `sandbox cp` documents parent-directory creation;
`sandbox search` explains API statuses, deleted sandboxes and `--image-ref`;
and the lifecycle commands say they need a persistent sandbox.
