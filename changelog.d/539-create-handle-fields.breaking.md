**A create handle holds only what the response carries** — a handle built from
a create response holds the response's fields, plus an `img_…` / `snap_…` id
the caller passed (a name is never recorded as an id), and the class the
create route fixes (`linux`, or `macos` for `create_macos` / `createMacos`),
and the handle sets it at once, so `sandbox_class` / `sandboxClass` and the
JSON name it (TypeScript recorded the class only for `createMacos`); Python
used to record a name as an image or snapshot id. A create response without
its `id`, or with an empty or blank one, is a `BadRequestError`.
