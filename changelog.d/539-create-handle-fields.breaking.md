**A create handle holds only what the response carries** — a handle built from
a create response holds the response's fields, plus an `img_…` / `snap_…` id
the caller passed (a name is never recorded as an id), and its class, which
the create route fixes (`linux`, or `macos` for `create_macos` /
`createMacos`), so `sandbox_class` / `sandboxClass` and the handle's JSON name
it at once (TypeScript recorded the class only for `createMacos`); Python used
to record a name as an image or snapshot id. A create response without its
`id` is a `BadRequestError`.
