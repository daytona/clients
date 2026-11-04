**CLI reservation renew help:** `daytona reservation update --help` says that a
reservation with auto-renew on renews near `period_end` only while a sandbox
runs or is paused on it, and that stopped sandboxes do not renew it. Before, it
said that paused sandboxes do not renew it.
