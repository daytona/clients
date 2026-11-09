**Handles and services are built by the SDK** — the handle and service classes
are no longer constructed by callers: TypeScript's constructors refuse callers
(get them from the client or the sandbox; their published declarations carry
no wire, axios or transport types), and Python's take `_`-prefixed parameters.
Python's model dataclasses stay constructible but are keyword-only: `ImageRef`
and `SnapshotRef` took positional arguments and now need keywords
(`ImageRef(id="img_…", name=None)`), and `ImageBuilder` is no longer built
directly with positional arguments: use `ImageBuilder.from_registry()` or
`ImageBuilder.from_dockerfile()`.
