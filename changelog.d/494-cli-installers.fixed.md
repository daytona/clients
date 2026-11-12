**CLI installers** — `install.sh` and `install.ps1` check that the downloaded
binary runs before it replaces the installed one, so a failed check leaves the
working `daytona` in place, and `install.ps1` run through `irm … | iex` no
longer leaves `$ErrorActionPreference = "Stop"` set in the calling session.
