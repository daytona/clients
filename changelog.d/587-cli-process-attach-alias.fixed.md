**CLI: retain process identity during attachment** — attaching by a live
process alias resolves its canonical id first, so waiting still returns the
process's exit status after that alias is released.
