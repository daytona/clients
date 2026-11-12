**CLI output** — `role list -o json|yaml` prints the `items` envelope every
other list command prints (it printed `roles`); `org limits -o json|yaml`
keeps integer limits exact past 2^53; `project budget get` no longer reports
a missing project (404) as a project without a budget; and a token refresh
that loses a race with another `daytona` process no longer reports the session
as expired.
