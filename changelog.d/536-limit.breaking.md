**CLI: `--limit` is checked** — on the lists that page with `--limit`,
`--cursor` and `--all` (the billing lists keep their own smaller bounds), a
`--limit` outside 1–200 is a usage error before any request instead of being
dropped or sent.
