**Clearer `ImageBuilder` local-source and `buildFromContext()` errors** — a
local source whose parent directory became unsearchable after
`add_local_file` / `add_local_dir` (`addLocalFile` / `addLocalDir`) now raises
`BadRequestError` (`build context file is not readable`) at build time instead
of a raw permission error, and `buildFromContext()` given an `ImageBuilder`
now says to use `images.build()`, matching Python's message.
