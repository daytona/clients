**`ImageBuilder`: one rule in both SDKs** — every value an `ImageBuilder`
takes must be a string that is not blank by Python's whitespace set, else
`BadRequestError` (Python raised a raw `AttributeError` for a non-string ref,
Dockerfile, step or env value, and for `images.pull(5)`; TypeScript tested
blankness with `trim()`). The registry ref, the Dockerfile text and the text
steps (commands, packages, env keys, the workdir and the TypeScript `user`)
are stripped with that set, and no value may contain a surrogate code point
(in TypeScript that means an unpaired UTF-16 surrogate, since a paired one is
a real astral character, which both SDKs accept; a Dockerfile is UTF-8 text;
before this change, for an env value TypeScript wrote the escape `\ud800`
where Python kept the raw character); env values and the TypeScript-only `cmd`
/ `entrypoint` arguments are kept as given, since they render as JSON strings.
So `images.pull(' python:3.12 ')` sends `python:3.12` from both SDKs
(TypeScript sent the padded ref), as `create()` already did, and the rendered
Dockerfile is byte-identical. An env mapping whose keys collide once stripped
(`{"A": "1", " A": "2"}`) is refused (both SDKs rendered both entries, so the
build kept the later value). `env()` takes a name and a value, or a mapping
alone (a mapping with a second argument is refused; both SDKs dropped the
argument); in TypeScript the mapping is a plain object, so a `Map`, a `Date`,
a class instance, an object with inherited keys or one with an enumerable
symbol key is refused. An env name, by name and value or in a mapping, must
contain no `=`, no whitespace (Python's set) and no control character (C0 or
DEL): it renders as `ENV name=value`, so such a name changed the instruction
or added one (both SDKs rendered it as given). Every other value rendered raw
into one Dockerfile line (the registry ref, each command of `run_commands()` /
`runCommands()`, each package of `pip_install()` / `pipInstall()`, the
`workdir()` path, and TypeScript's `user()` name) must contain no control
character (C0 or DEL): a line break ended the instruction and started another,
so `workdir("/app\nUSER root")` added a `USER root` line (both SDKs rendered
it as given).
