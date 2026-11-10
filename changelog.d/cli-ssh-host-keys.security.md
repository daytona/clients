**CLI: remember SSH gateway host keys privately** — `sandbox ssh` uses persistent,
gateway-scoped known-hosts files in the CLI configuration directory instead of
the user's or system's host-key databases. The first connection trusts the
gateway key automatically; subsequent connections reject changed keys.
