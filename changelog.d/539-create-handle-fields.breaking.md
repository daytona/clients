**A create handle holds only what the response carries** — a handle built from
a create response holds the response's fields, plus an `img_…` / `snap_…` id
the caller passed (a name is never recorded as an id), and its class only when
the call fixes it (`create_macos` / `createMacos`); Python used to record
`linux` and a name as an image or snapshot id. A create response without its
`id` is a `BadRequestError`.
