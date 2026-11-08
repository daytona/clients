**`ImageBuilder`: one rule in both SDKs** — every value an `ImageBuilder`
takes must be a string that is not blank by Python's whitespace set, else
`BadRequestError` (Python raised a raw `AttributeError` for a non-string ref,
Dockerfile, step or env value, and for `images.pull(5)`; TypeScript tested
blankness with `trim()`). The registry ref, the Dockerfile text and the text
steps (commands, packages, env keys, the workdir and the TypeScript `user`)
are stripped with that set; env values and the TypeScript-only `cmd` /
`entrypoint` arguments are kept as given, since they render as JSON strings.
So `images.pull(' python:3.12 ')` sends `python:3.12` from both SDKs, as
`create()` already did, and the rendered Dockerfile is byte-identical. An env
mapping whose keys collide once stripped (`{"A": "1", " A": "2"}`) is refused
instead of keeping one value. `env()` takes a name and a value, or a mapping;
in TypeScript it takes only a plain object, so a `Map`, a `Date`, a class
instance or an object with only inherited keys is refused.
