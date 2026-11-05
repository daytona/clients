**CLI interrupts** — Ctrl+C during `sandbox list` or `image list` exits 130
instead of reporting the unfinished regions as failed (exit 7); a sandbox
lookup interrupted while it searches other regions exits 130 instead of
answering with a partial result; and a second Ctrl+C now terminates a command
that does not stop after the first.
