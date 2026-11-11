**CLI: a macOS `sandbox start` that can buy a Mac asks first** — when the
organization holds no live Mac reservation in a stopped macOS sandbox's
region, auto-reserve buys one at the start, so `daytona sandbox start` now
asks for confirmation at a terminal and, off a terminal, needs `--yes` (or
`DAYTONA_YES=1`), as `sandbox create-macos` does when it may buy one. A start
that a live reservation in the region serves asks nothing.
