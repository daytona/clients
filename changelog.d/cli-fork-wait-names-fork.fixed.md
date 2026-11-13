**CLI: a failed `sandbox fork` wait names the fork** — when `daytona sandbox fork`
has created the fork and the wait for it then fails or times out, the error
starts with `sandbox <id> was created, but`, as a create's does. Before, a
timeout did not name the new sandbox. Exit codes are unchanged.
