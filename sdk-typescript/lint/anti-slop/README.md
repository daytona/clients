# Vendored anti-slop rules

Source: https://github.com/dmmulroy/anti-slop

Pinned revision: `6d538555cb151d4121ed51a27db81890eacf8ae9`

## License

This directory contains code from anti-slop, licensed under the MIT License:
Copyright (c) 2026 Dillon Mulroy. The full license text is in [LICENSE](./LICENSE).
The MIT license applies to this vendored code, rather than the repository's
default Apache-2.0 license. Daytona maintains local modifications, including
the package adapter described below.

These files are vendored and are ours to maintain, as the upstream project instructs. The optional Effect rules are not included because this repository does not use Effect.

The local adapter resolves `@oxlint/plugins` from the independent package that Oxlint checks.
