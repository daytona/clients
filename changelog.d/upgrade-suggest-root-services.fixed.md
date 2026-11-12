**Migration tool (TypeScript): `--suggest-v2` on root services** —
`client.volume` / `snapshot` calls on an unmigrated project now get the same
suggestions they get after the default pass, where the tool reported that it
could not prove the owning client.
