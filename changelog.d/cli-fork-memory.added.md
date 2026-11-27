**CLI: `sandbox fork --memory`** — `--memory=false` forks only the disk and
does not pause the source; `--memory` on a macOS sandbox is refused before
the fork request. The command now waits for the state the fork actually rests
in, as the SDKs do: paused only for a memory fork of a paused Linux sandbox,
stopped for every disk-only fork (a stopped source included, which it used to
wait on as paused).
