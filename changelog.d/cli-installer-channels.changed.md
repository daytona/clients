**CLI installers pick the highest stable release** — `install.sh` and
`install.ps1` without a version install the highest stable 2.x release by
version, not the first 2.x release GitHub lists, so a later alpha or a
backport published after a higher release is no longer installed by default.
Pre-releases come only with `--channel rc|alpha` (`DAYTONA_CHANNEL`) or an
exact `--version` (`DAYTONA_VERSION`); until `2.0.0` is out the default
installs the newest pre-release and says so. The README fetches the
installers from the `main` branch
(`https://raw.githubusercontent.com/daytona/clients/main/cli/install.sh`,
`…/install.ps1`) instead of a fixed release.
