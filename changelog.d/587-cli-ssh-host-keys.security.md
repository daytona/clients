**CLI: remember SSH gateway host keys privately** — `sandbox ssh` uses
persistent, gateway-scoped known-hosts files in the CLI configuration directory
instead of the user's or system's host-key databases. The first connection
trusts the gateway key automatically (trust on first use: an attacker
intercepting that first connection could supply the trusted key); subsequent
connections reject changed keys. SSH options forwarded after the sandbox id,
such as `-o StrictHostKeyChecking=no` or `-o UserKnownHostsFile=…`, override
this policy.
