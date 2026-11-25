**`images.promote` returns the image's current state** — every SDK now reads
the image again after naming it, by the id the promotion answered with, so the
result reports its regions (`availableIn` / `available_in` / `AvailableIn`);
TypeScript and Go returned the promotion's answer, whose region list could be
empty. An answered id that cannot be a request path id is refused before that
read. In Python, promoting an image by the reference `search` (which `get()`
refuses, as it is the search route) used to apply the name and then fail on the
read-back; it now succeeds, because the read-back uses the returned id.
