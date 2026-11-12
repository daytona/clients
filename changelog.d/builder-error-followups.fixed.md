**Clearer `ImageBuilder` and `build_from_context()` errors** — a local source
whose parent folder became unreadable after it was added now raises
`BadRequestError` (`build context file is not readable`) at build time instead
of a raw permission error, and `buildFromContext()` given an `ImageBuilder`
now says to use `images.build()`, as Python already did.
