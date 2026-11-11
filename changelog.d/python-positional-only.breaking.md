**Python: leading parameters are positional-only** — the required leading
parameters of every public v2 method (`fs.write_text(path, content, /)`,
`images.pull(image, /)`, `process.get(process_id, /)`,
`secrets.set(name, value, /)`, `git.clone(url, path, /)`,
`sandbox.set_labels(labels, /)`, …) can no longer be passed by keyword, so
their names, which differ from TypeScript's in places, are not part of the API;
options after them stay keyword-only. A test holds every public method to it.
