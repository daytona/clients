**Python renames** — `LifecyclePolicyConfig` is renamed
`LifecyclePolicyOptions`; `ImageBuilder.render_dockerfile()` is renamed
`to_dockerfile()`, and `ImageBuilder.build_request_source()` is removed (it
was internal: pass the builder to `images.build()` or `create_from_build()`).
