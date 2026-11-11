**CLI: `sandbox ssh` no longer prints the credential** — OpenSSH names the
gateway login, which carries the credential, in its own messages (an
authentication failure, `-v` output); the CLI now replaces the credential with
`<redacted>` in everything `ssh` writes to stderr.
