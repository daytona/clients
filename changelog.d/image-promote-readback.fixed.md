**`images.promote` returns the image's current state** — every SDK now reads
the image again after naming it, by the id the promotion answered with, so the
result reports its regions (`availableIn` / `available_in` / `AvailableIn`);
TypeScript and Go returned the promotion's answer, whose region list could be
empty. Promoting by a name `get()` refuses (`search`) no longer fails after the
name was applied in Python.
