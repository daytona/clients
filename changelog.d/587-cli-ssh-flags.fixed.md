**CLI: parse flags before an SSH destination** — `sandbox ssh --help` needs
no credential, root flags before the sandbox id apply to the CLI, and flags
after the id are forwarded to OpenSSH.
