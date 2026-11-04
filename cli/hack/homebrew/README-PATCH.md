# homebrew-cli: add the `daytona-alpha` channel

Apply to https://github.com/daytonaio/homebrew-cli (a separate repository):

1. `daytona-alpha.rb` → repo root (new formula; placeholder version/sha are
   overwritten by the first `update-version-alpha` dispatch).
2. `brew_publish_alpha.yaml` → `.github/workflows/` (listens on
   `repository_dispatch: update-version-alpha`).
3. `update_sha.sh` → `scripts/update_sha.sh` (per-channel suffix guard: stable
   = bare, -rc = `-rc.N`, -alpha = `-alpha.N`).

`brew install daytona-alpha` installs the 2.x CLI as `daytona`; the stable
and release-candidate formulae are unchanged. Trigger: this repository's
`.github/workflows/sdk_publish.yaml` with a `v2.0.0-alpha.N` version.

Keep `brew_publish_alpha.yaml` here byte-identical to the tap's copy (the
release job matches the downstream run by its `run-name`).
