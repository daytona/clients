**Python renames and signatures** — `LifecyclePolicyConfig` is renamed
`LifecyclePolicyOptions`; `ImageBuilder.render_dockerfile()` is renamed
`to_dockerfile()`, and `ImageBuilder.build_request_source()` is removed (it
was internal: pass the builder to `images.build()` or `create_from_build()`).
`sandbox.set_labels()` takes `labels` positionally, as TypeScript's
`setLabels(labels)` does (`labels=` still works), and returns the applied
labels typed `Mapping[str, str]` (a fresh plain `dict`, as `Sandbox.labels`
is) instead of `Dict[str, str]`.
