**CLI points 0.x users at `daytona-v0`** — when the CLI has no saved profile
and finds the 0.x CLI's `config.json`, the "not logged in" error and `whoami`
say that file belongs to the 0.x CLI, which is available as `daytona-v0`
(Homebrew `daytonaio/cli/daytona@0`), and that `daytona login` signs in with
this CLI. The 0.x file is never read or changed.
