# daytona-upgrade for Python

Upgrade Daytona SDK 0.x projects to SDK 2.x **without moving workloads to v2**. The tool keeps the root `Daytona` / `AsyncDaytona` client, adds `.v0` to proven client calls, moves resource imports and all shared errors unconditionally to `daytona.v0` to preserve 0.x constructors, attributes and `except` behavior, and updates dependencies to the SDK release this tool was published with (see [Versions](#versions)). Unproven calls get a `FIXME(daytona-upgrade)` only when their receiver is linked to Daytona; calls with no such evidence (including generic names, dict-held or `Any`-typed clients) get no FIXME and must be reviewed by hand. The tool is precision-first and best-effort, not a completeness verifier. Dynamic `__all__` keeps known unrelated exports non-client while making client or unknown exports uncertain, including nested wildcard imports. Possible replacements never preserve stale local Daytona proofs.

Requires Python 3.9 or later.
SDK 2.x requires Python 3.10 or later; update your application's supported versions before installing it. Dependency markers stay unchanged.
Run this tool with an interpreter that supports your source syntax.

```bash
uvx daytona-upgrade --dry                 # preview the patch
uvx daytona-upgrade . --yes               # upgrade a clean Git tree
pipx run daytona-upgrade path/to/project  # alternative runner
```

Each release migrates to the SDK release of the same version (see
[Versions](#versions)). Until SDK 2.0.0 is out, run the exact alpha you are
migrating to, e.g. `uvx daytona-upgrade@2.0.0aN`.

**No installers or lockfile updates run.** Review the changes, run the printed lock/install command, and test your application. Keep custom/self-hosted API URLs on SDK 0.x: the tool refuses those projects before writing any files.
SDK 2.x builds the v0 client lazily. A root constructor inside a `try` that catches Daytona errors receives a manual item and FIXME only when that client's `.v0` is not accessed there, including accesses added by the upgrade. Access the assigned client's `.v0` inside that `try` to keep credential errors within its handlers.
For constructor keywords, `--drop-api-url` only permits dropping non-literal URLs that you assert target Daytona Cloud; it does not permit a literal custom URL.
Unresolved `DaytonaConfig(**options)` mappings also require that assertion;
the call is left unchanged with a manual-review item. Literal dictionaries
are inspected like explicit keyword arguments.
An unknown `**options` remains unresolved even alongside an explicit Cloud
`api_url` or `server_url`, in either order. Python rejects duplicate keywords at
runtime, but the tool does not infer URL safety from that runtime constraint.
Attribute assignments and `setattr` writes of `api_url` / `server_url` are
checked only on names constructed or annotated as `DaytonaConfig`, including
lexically enclosing module configs used in functions or methods. Unrelated URL
fields are untouched. Every proven `Daytona(config)` / `AsyncDaytona(config)`
construction requires a config whose origin and URL safety can be verified across
its uses, including later nested writes. Printing, standard logging, `repr`, `str`,
`json.dumps`, f-strings and config field reads do not escape; unknown calls and aliases do.
SDK 2.x `DaytonaConfig` is a dataclass, not a Pydantic model: known Pydantic APIs get
`config-field` items and FIXMEs. Replace `model_dump` / `dict` with `dataclasses.asdict(cfg)`,
`model_copy` / `copy` with `dataclasses.replace(cfg, ...)`, and `model_validate` / `parse_obj` with `DaytonaConfig(**values)`.
Literal copy updates and validation dictionaries receive the same URL checks as constructors.
Undecorated synchronous factories consisting of a direct `return DaytonaConfig(...)` carry literal-origin proof, including through project imports. Their returned constructors receive the same URL checks as inline configs; a return annotation alone does not establish URL safety.
Other unresolved configs (including parameters and opaque factories or imports) require `--drop-api-url`, named in messages and JSON next steps; they stay manual. Shadowed readers are untrusted.
Literal Cloud URLs and `None` (the 0.x default) are removed; other writes refuse
the upgrade. With `--drop-api-url`, those unresolved writes are instead left
unchanged with a `config-field` item for manual removal.

## Optional v2 guidance

```bash
uvx daytona-upgrade --suggest-v2
uvx daytona-upgrade --suggest-v2 --json   # report only; writes nothing
uvx daytona-upgrade --remove-suggestions
```

Suggestions are tagged `daytona-v2:` comments, with the v2 equivalent, behavior differences, workflow dependencies, and migration documentation links.
Shared-error guidance covers named imports and namespace imports such as
`import daytona.v0 as sdk` when their shared error classes are used.
Service aliases use the owning sandbox when known; otherwise suggestions use the service handle directly when the v2 call stays on that service.
Calls that move to another service ask you to move the call to the owning sandbox, with `sandbox` as the example receiver. Workflow headers include calls through sandbox and service aliases.
Calls inside nested functions that use the sandbox count too.
If a closure declares the binding `global` or `nonlocal`, rebinds it, and calls a lifecycle method, the earlier producer's workflow is also marked persistence-dependent; keep it on `.v0`.
Workflows with root sandbox `start`, `stop`, `pause`, `archive`, `set_autostop_interval`, `set_auto_pause_interval`, or `set_auto_archive_interval` calls, or client `start`/`stop` calls taking the sandbox, are marked to stay on `.v0`: these calls require a different lifecycle workflow in SDK 2.x.
The header names those calls in source order within that workflow, ending direct-call membership when its sandbox binding is reassigned. Deferred calls in nested functions and lambdas count whether declared before or after the producer, including simple local sandbox aliases inside those functions; unrelated shadowed parameters and locals do not. The first iterable of a comprehension or generator expression is evaluated eagerly in its enclosing scope and follows that binding's direct-call window. Client calls retain their single-line callee text (for example, `d.v0.stop`), while sandbox calls use the bare method name. `set_auto_delete_interval` does not trigger this header.
They never change calls or dependencies. Re-running replaces the tagged comments;
removal deletes only those comments. `FIXME(daytona-upgrade)` comments are kept
for you to resolve manually. v0 IDs, snapshots, volumes and preview URLs
do not automatically carry over to v2. Documentation links point at the migration
guide in this repository
([`upgrade/MIGRATION.md`](https://github.com/daytona/clients/blob/main/upgrade/MIGRATION.md)).

## Versions

Each release of this tool is published with the SDK release of the same
version, and migrates to exactly that release: `uvx daytona-upgrade@2.0.0aN`
writes `daytona[v0-sdk]==2.0.0aN` (a pre-release is pinned, because
pre-releases may break), and a stable `X.Y.Z` writes `>=X.Y.Z,<X+1`. Run the release
that matches the SDK you want; the SDK's runtime guidance for 0.x calls names
it.

SDK 2.x runs one 0.x release as `.v0`; the tool's warnings and refusals name
it. The tool reads the project's installed 0.x
version from its lockfile (`uv.lock`, `poetry.lock`, `pdm.lock` or `Pipfile.lock`,
the closest one up to the Git root), else from the lower bound of the
requirement, and compares:

| Installed 0.x version | Result |
| --- | --- |
| the `.v0` release | nothing to report |
| older, or a newer patch of the same minor | a warning (`warnings` in `--json`): moved calls now run the `.v0` release; review the 0.x changes in between |
| a newer minor | refused (exit 2, reason `newer-v0`): 0.x APIs added after the `.v0` release are missing from it and would get no FIXME. Use a later tool release, or pass `--allow-newer-v0` to migrate anyway (with a warning) |
| unknown | a warning |

Warnings are advice, not manual items: `--strict` ignores them.

## Options

| Option | Behavior |
|---|---|
| `[path]` | Project root (default: current directory), or a single file |
| `--dry` | Print a unified diff; write nothing and skip Git safety |
| `--print` | Print changed files; write nothing and skip Git safety |
| `--json` | Print the structured report (the fields of `report.schema.json`, linked below); with `--suggest-v2`, write nothing and skip Git safety |
| `--force` | Skip the Git check entirely, allowing uncommitted changes |
| `-y`, `--yes` | Skip confirmation (also automatic for non-interactive stdin) |
| `--drop-api-url` | Accept API URLs the tool cannot verify or that come from the environment, asserting they point at Daytona Cloud |
| `--strict` | Exit 1 if manual review items remain |
| `--allow-newer-v0` | Migrate a project on a newer 0.x minor than the 0.x release SDK 2.x runs as `.v0` (see [Versions](#versions)) |
| `-v`, `--version` | Print version |
| `-h`, `--help` | Print usage |

Exit codes: **0** completed (manual items may remain), **1** internal/input
processing error or strict-mode manual items, **2** refused (dirty tree, custom
API URL, a newer 0.x minor than `.v0` without `--allow-newer-v0`, a project-owned SDK module, invalid arguments, or no Daytona dependency/code).

From `2.0.0` on, within a major version the flags, these exit codes and the
fields and values of the `--json` report ([`report.schema.json`](https://github.com/daytona/clients/blob/main/upgrade/mapping/report.schema.json))
stay compatible. A minor release can add flags, report fields and values to
the `language`, `mode`, `kind`, `refused.reason` and `suggestion.category`
vocabularies, rewrite more calls, report new manual items and refuse a case it
newly recognizes as unsafe. Ignore fields and values you don't know.

## Scope and safety

Synchronous `Daytona` spec/autospec mocks route proven members through `.v0`, including proven pytest fixture returns and yields.
Bare local doubles adapt only when their consumers' accesses through the parameter
move to `.v0` too, without forwarding to unrewritten code or other unresolved uses. Otherwise they stay
unchanged; a consumer with moved accesses requires `mock-target` review.
Default factory-patch review follows indexed calls within the decorated function or
`with` block, or the test function for started patches and patch fixtures. An unused
default patch, or one reaching only unchanged consumers, produces no item. Unknown
patch lifetimes and unpacked options retain conservative review. Explicit replacement
expressions still undergo the existing double-shape review.
Local fake modules replacing proven SDK-module helpers route known 0.x submodule paths;
uncertain replacements retain `mock-target` review.
Check FastAPI `dependency_overrides` fakes, `functools.wraps`-decorated consumers,
and detached bound-method aliases by hand; their test-double setup and assertions are not followed.
Indexed, undecorated functions retain client-argument links in unannotated parameters, across project imports and forwarding chains.
These links produce FIXMEs, never inferred annotations or automatic `.v0` rewrites.
The spec must be positively resolved to the synchronous class, including aliases or a synchronous factory annotated `type[Daytona]`. Nullable class-return annotations and overwritten factories require review, not `.v0` routing. A client-class annotation alone is not proof of synchronous origin. Class/static/instance factories and postponed annotations use the same rule; other client-typed specs require `mock-target` review.
`AsyncDaytona` spec mocks, including walrus aliases and conditional async/unresolved specs, require review:
SDK 2.x's `.v0` property does not auto-create async mock methods. Configure it with a separate async-spec'd mock before migrating awaited calls.
For synchronous instance mocks, `configure_mock` literal moved-member keys become `v0.<key>`, including dotted paths and keywords.
Dynamic or colliding keys stay unchanged with `mock-target` review and invalidate later rewrite proofs for the mock and possible aliases.
`v0`, `v2`, and `close` configuration stays on the root. Manual items fail `--strict`.

Constructor mocks such as `patch("app.Daytona")` receive a `mock-target` item: configure moved methods under `return_value.v0`. Unrelated mock targets and user-defined `patch` helpers are left alone. Mock review retains earlier Daytona construction evidence after rebinding, never as proof for rewriting ordinary calls. Generated dataclass constructors prove unchanged defaults and direct factories; annotated attrs and Pydantic fields also prove direct client factories without converters, validators or custom initialization hooks. Uncertain initialization retains FIXME review. An explicit dataclass field argument takes precedence, but proves a field only with generated initialization or a direct, same-name parameter store. Unknown spreads, inherited signatures and uncertain `kw_only` options preserve linked calls as FIXMEs. `ClassVar` and `KW_ONLY` declarations occupy no constructor position; fields after `KW_ONLY` are keyword-only unless explicitly overridden. `InitVar` inputs occupy constructor positions but never prove instance attributes. Explicitly typed `self` fields retain their existing policy unless custom or replaced attribute read, write or delete hooks make field lookups opaque.

Python `.py`, `.pyi`, and notebook code cells are discovered recursively. Pytest fixture evidence applies to collected test functions and methods, decorated fixtures, and functions in `conftest.py`, never ordinary production functions merely sharing fixture parameter names. Collection defaults to `test_*.py` / `*_test.py` modules, `test`-prefixed functions and `Test`-prefixed classes. The tool searches from the scan directory (the parent for a single file) upward to the filesystem root for collection settings. At each directory it checks `pytest.ini`, `pyproject.toml` with `[tool.pytest.ini_options]`, `tox.ini` with `[pytest]`, then `setup.cfg` with `[tool:pytest]`; the first applicable file wins without merging. Nearest fixture declarations still take precedence over ancestor `conftest.py` declarations.
Virtual environments, generated trees, Git metadata and symlinks are skipped. Scans exceeding 200 files print progress to stderr; smaller scans also print a status line after three seconds. JSON reports remain on stdout. Analysis is whole-project and grows with the code scanned: a ~40k-line tree takes about five minutes, and a large monorepo can take much longer, so point the tool at the directories that use Daytona (plus their tests and the dependency manifest) rather than the repository root.
Files are syntax-checked before flow filtering; relevant imports, environment writes, mocks, dynamic imports, unresolved wildcard exports and implicit pytest fixture links retain analysis. Fixture definitions, conftests and pytest plugin declarations are retained even without an explicit SDK import. Unrelated files retain import ownership.
In both tools, an unreadable file or directory inside the scanned tree produces
an `unsupported-file` item: `<path> could not be read: <strerror>; fix its permissions, then rerun.`
(`fix the problem, then rerun.` for a non-permission error)
Paths are project-relative; the run exits 1 without writing any files (a readable
custom API URL still takes precedence and refuses with exit 2).
Upgrades refuse for a `daytona` or `daytona_sdk` module (`.py` or `.pyi`) or
regular package (containing `__init__.py`) or namespace directory containing
Python sources (`.py` or `.pyi`) directly in an import root: the scan base,
`src/`, directories containing a supported dependency manifest, or source roots
declared in `pyproject.toml` by setuptools, Poetry or Hatch. Single-file scans
also check sibling modules, regular packages and namespace sources.
Rename a colliding local module before rerunning: it can shadow the SDK, or the
installed SDK can override a namespace portion and break local imports.
Nested regular packages elsewhere produce a non-blocking `private-import` item:
they shadow the SDK only if their parent directory is on `sys.path`.
Directories without Python sources do not refuse; ignored trees such as
`.venv`, `.tox`, `site-packages` and `node_modules` remain excluded.
Suggestion/removal modes remain available.
Notebook cells share flow analysis in document order; IPython magic lines are
preserved, and `%pip install` / `!pip install` Daytona requirements are updated.
Notebook edits preserve the original JSON bytes outside changed code-cell
sources, including metadata, outputs, key order, indentation, and the presence
or absence of a final newline.
Notebook diagnostics use `file.ipynb#cell=N:line:column`, with a one-based cell
index (including non-code cells) and a line number local to that cell.
Before masking magic lines, `%env` / `%set_env` assignments (`NAME=value` or
`NAME value`) and single `!export NAME=value` assignments receive the same
environment URL checks as Python writes. `%env` reads are ignored;
`$variable` / `{expression}` interpolation requires manual review rather than
being treated as a literal URL.

Supported manifests: `requirements*.txt`, `*-requirements.txt` (including
`dev-requirements.txt`), `requirements*.in`, `constraints*.in`, `constraints*.txt`,
`pyproject.toml`, `setup.cfg`, `setup.py`, and `Pipfile`.
Standalone `constraints*.txt` files retain constraint semantics: their versions
can migrate, but a manual item reminds you to enable `v0-sdk` on the consuming
requirement. Invalid TOML produces a file-specific `unsupported-file` item and
blocks writes without printing a traceback; other supported manifest parse
failures also name the project-relative file.
Recognized environment/deployment files (`.env*`, `Dockerfile*`, compose YAML, YAML under `k8s/`, `helm/`, `charts/`, or `deploy/`, and `.github/workflows/`) are scanned for Daytona API URL variables and reported for review, never modified. Compose service `env_file` and `include[].env_file` inputs are scanned at every plausible project base, including literal include `project_directory` values; literal `include[].path` manifests are scanned recursively with a cycle guard, regardless of filename. Dynamic, aliased, or unparseable includes retain environment review. Deployment directories are matched relative to the Git checkout (the whole absolute path when Git cannot answer), so scanning `deploy/` directly applies the same checks as scanning its parent.
Binding-proven `load_dotenv` / `dotenv_values` calls also scan explicitly loaded files regardless of filename, including `dotenv_path=`, literal aliases, `find_dotenv("name.env")`, `Path(__file__).parent / "name.env"`, and `os.path.join(os.path.dirname(__file__), "name.env")`. Explicit relative paths are checked against both the selected project root and the source file's directory. Default loaders and `find_dotenv` scan every existing candidate from the source directory upward through the selected root, including intermediate directories. Pydantic-settings `SettingsConfigDict(env_file=...)`, nested `class Config: env_file = ...`, and constructor `_env_file=` overrides on proven `BaseSettings` subclasses receive the same checks, including literal lists/tuples. These inputs are scanned conservatively even without a proven downstream client use.
Dynamic, missing or unreadable loader inputs produce an `env-api-url` review item, as do unresolved keyword expansions, CWD-dependent searches (`usecwd=True`), or searches with no match before leaving the selected root. No project loader is executed. Other locations such as `config/prod.env` remain uninspected unless explicitly loaded.
`.env.example`, `.env.sample`, and `.env.template` are ignored unless explicitly loaded; `.env.test` is scanned. Comments and empty values are ignored. Literal non-Cloud URLs in active files or Python `os.environ` / `os.putenv` writes refuse the upgrade, including `os.environ.update({...})` and `os.environ |= {...}`.
Environment declarations using `${NAME:-default}`, `${NAME-default}`, `${NAME:+word}` or `${NAME+word}` receive the same Cloud/custom checks on the selectable value. Bare references and required-variable expressions (`:?` / `?`) remain unresolved review; a reference inside another setting is not a declaration.
Compose detection uses Compose filenames, explicit `COMPOSE_FILE` or include selection, a top-level `services` mapping, or an include list containing only paths or Compose include fields (`path`, `env_file`, `project_directory`). Unselected `.gitlab-ci.yml` files are not Compose. Unrelated YAML is filtered before tokenization; Compose YAML over 5 MiB retains environment review instead of being tokenized. Explicitly loaded environment inputs also have a 5 MiB byte limit; pipes and device files are never read.
Tuple/list assignments pair literal RHS elements with their targets; unresolved unpacking is reported for review. `os.environ.__setitem__(key=..., value=...)` is recognized too.
`--drop-api-url` acknowledges these environment settings without removing them; they remain manual-review items. Only if this environment refusal fires and you have verified the URL really points at Daytona Cloud, rerun with `--drop-api-url`. Cloud or dynamic environment values are reported without refusing. Cloud URL schemes and hostnames are case-insensitive; the `/api` path is case-sensitive.
Docker `ENV` / `ARG` scanning includes `ONBUILD` instructions. Closed heredoc
bodies are ignored, including those in `ONBUILD RUN`; if a terminator is missing,
the remaining lines are scanned as instructions so malformed input cannot hide
later URL diagnostics.
A `package.json`
produces guidance to run `npx @daytona/upgrade@<this release>` separately.

Local requirements/constraints includes are followed within the project root.
Included filenames need no particular extension. For partial-root upgrades,
ancestor includes are inspected read-only within the nearest Git checkout;
cycles and symlinks are skipped, and constraints never certify a runtime dependency.
Hash-locked or backslash-continued Daytona requirements remain unchanged with a
`manual-dependency` item: change them to the target requirement (see
[Versions](#versions); `daytona[v0-sdk]>=2,<3` below stands for it) and regenerate
hashes before running upgraded code. Poetry constraint alternatives retain their
Python, marker and platform conditions; unsupported alternatives need manual edits.
UTF-8 BOMs, CRLF endings and Python source encoding declarations are preserved.
Only files with planned edits require lossless decoding. Each edited file with
invalid UTF-8 (or an invalid Python coding declaration) produces an
`unsupported-file` item with a project-relative path; the run exits 1 without
writing any files. Convert those files to UTF-8 or correct their Python coding
declarations before rerunning. Scan-only files and unchanged sources, manifests
and notebooks are read with replacement characters when needed and never written.
If replacement-decoded Python cannot be parsed, the tool reports the encoding
remedy instead of a generic syntax error, even when no edit could be planned.
Editable, VCS/URL and unknown dependency
syntax that cannot be rewritten is reported as `manual-dependency`, never skipped
silently. A project Python range allowing versions below 3.10 (or an unparsed
range) also gets a `manual-dependency` compatibility item.
In `setup.cfg`, single-line dependency lists split on semicolons; put requirements
with environment markers on separate, indented lines. Ambiguous declarations
stay unchanged with a `manual-dependency` item; `file:` requirements stay unchanged
and require manual migration of the referenced files. Static `setup(**kwargs)` dictionaries
are checked for `python_requires`; unresolved expansions require manual floor review.
Legacy uv `dev-dependencies` arrays and PDM development groups are migrated too.
uv `override-dependencies` migrate to `daytona[v0-sdk]>=2,<3`; PDM
`[tool.pdm.resolution.overrides]` registry versions migrate to
`daytona = ">=2,<3"` because its table form cannot express extras. uv
`constraint-dependencies` remain extra-less because uv ignores constraint
extras; override and constraint requirements retain markers. Unrestricted constraints and constraints
already matching `>=2,<3` migrate automatically; other constraint policies
(including `<1` and old version pins), source/URL overrides, and ambiguous PDM
overrides stay unchanged with a `manual-dependency` item. Review their intent
before changing them; `--strict` exits 1 while those items remain.
If source changes without an updated Daytona requirement or a declaration already
requiring SDK 2.x with the `v0-sdk` extra, a `manual-dependency` item asks you to
update its declaration.
Manifests within the project and its parents up to the Git root are checked;
parent manifests are never edited.
Partial-root scans emit one informational `types-unresolved` item at `.` when
a manifest lives above the scan root, or Python sources elsewhere in the Git
checkout import Daytona. It does not fail `--strict`; scan the full project to
include shared clients. Files outside the selected root remain read-only.
uv and PDM resolver declarations in ancestor manifests that need changes are
reported for manual review, even when a nearer runtime dependency is already upgraded.
Daytona `[tool.uv.sources]` overrides in local or ancestor manifests produce a `manual-dependency` item; review them manually before installing SDK 2.x.
A `setup.py` requirement list modified between its definition and the
`setup()` call, including through an alias, cannot certify that the runtime
dependency already supports SDK 2.x.
When the mapping supplies the pinned v0 export list, historical names missing
from that bundle get a `private-import` item: use the v2 API or remove the name; it is unavailable from `daytona.v0`. Known bundled 0.x submodules are not missing exports. All dunder names (including `__file__` and `__version__`) stay on the renamed root in both attributes and `from` imports, without an item.

All transformations are calculated before the first write, so a custom URL or
parse failure in a later file cannot leave earlier files upgraded. Every output
is first written to a temporary file in its target directory, flushed and
`fsync`ed, with the original's permission bits copied. Only after all outputs
are prepared does the tool atomically replace each target in order. If a write
or replacement fails, the tool restores already-replaced targets from their
original bytes using the same temporary-file and atomic-replacement procedure,
and removes newly created targets before exiting 1. Temporary files are cleaned
up on success; error-path cleanup is best-effort. Rollback failures are reported
explicitly, with any leftover temporary paths appended to the original write error.
Write failures name the project-relative target, not its temporary staging file.
Each replacement is atomic, not the multi-file operation: a crash or forced
termination can leave some complete replacements applied and temporary files
behind, but cannot truncate an original file during writing.
Flow analysis is conservative: optional expression writes, match captures and
base-object rebinding invalidate receiver proofs. Escaping holders lose unannotated field proofs: call arguments (including containers, keys, keywords and spreads), method receivers, called captures, or storage elsewhere. Calls on `self` are not escapes: class-wide writes already cover methods. Explicit client annotations survive escapes; any call receiving `self` fences unannotated fields later in that function. Recognized literal reflective writes below affect only their destination. Later linked uses get FIXMEs, never `.v0`; direct clients keep identity proofs. Known class construction alone is not an escape. Class attributes require every
write to agree; an annotation cannot overrule an unknown or non-client write. Joins retain client uncertainty across reaching paths in `if`, `try`, loops, and `match`; returns do not contribute, breaks bypass loop `else`, and continues feed exhaustion. Loop-backedge joins affect only names assigned in the body, preserving invariant parameter evidence; cached loop/function write sets avoid rescanning during replay. Bounded fixed-point replay carries evidence through successive aliases across iterations. Comprehension walrus assignments retain both the zero-execution entry evidence and uncertain enclosing-scope writes, including possibly unbound names. Sequential `except*` handlers include preceding handlers' writes, even when those handlers raise. `finally` joins remain conservative across unwinding paths.
An empty first iterable literal (`[]` or `()`) prevents comprehension-body writes, so prior client proofs survive. Expression-wide invalidation clears parent names before dotted members, including when a later operand rebinds an alias. Nested-definition decorators, defaults, annotations and class bases retain possible outer-generator client yields for review; nested function and lambda bodies are excluded. A proven `AsyncExitStack.enter_async_context(cm=value)` accepts the explicit keyword like its positional form; extra keywords and `**kwargs` do not establish proof. After `with` / `async with` using unknown context managers, receiver proofs must
agree at entry and every body prefix because an exception may be suppressed; differing client evidence produces a FIXME rather than disappearing.
Binding-proven non-suppressing contexts preserve body proofs: `open`, `io.open`,
`Path(...).open()`, threading locks and semaphores (including proven lock bindings),
`Condition()` with its default lock, tempfile contexts, and `Daytona(...)` /
`AsyncDaytona(...)`. Binding-proven `nullcontext(value)` returns `value` under either context protocol. A proven `AsyncExitStack` instance's `enter_async_context(cm)` returns the asynchronous entry value of `cm`; shadowed stack bindings do not establish proof. Project subclass entry overrides leave the target unproven rather than assuming it is the manager. Shadowed or unknown context managers remain conservative.
Reflective writes (`setattr`, `object.__setattr__`, `__dict__` and `vars(...)` subscripts/updates) participate in sequential, deferred and loop analysis. Recognized literal-key writes through proven-unshadowed builtins invalidate only the named field; replacement values and nested opaque calls still follow the holder-escape rule above. Unknown keys fence every field. Proven Daytona values establish literal-field evidence unless the builtin operation is shadowed. Short-circuit paths, mixed factory annotations and augmented client collections retain uncertain evidence for review; augmented client/subclass results stay unproven. Proven `ExitStack`/`AsyncExitStack.enter_context(cm)` (also `cm=...`) preserves entry evidence, unless overridden. Container mutations invalidate proofs through aliases. Return annotations on
functions with unknown decorators do not prove that the decorated call returns a
client; calls relying on that lost proof get a FIXME rather than a silent miss. Collection results retain uncertain client-element evidence through loops and `yield from` wrappers. Pytest fixtures resolve from the test module to the nearest ancestor `conftest.py`; nearer non-client or unknown declarations shadow ancestor fixtures. Except for proven synchronous spec mocks, injected client evidence requires review even with a conflicting non-client annotation and never establishes rewrite proof. Concrete property and cached-property results take precedence over annotations; conflicting client evidence requires a FIXME. Generator parameters use annotation aliases and type-checking-only imports, including variadic positional element annotations. Nested client iterables retain manual-review evidence in dictionaries.
Dataclass `field(default_factory=Daytona)` / `AsyncDaytona` and zero-argument lambdas directly constructing either client can prove instance fields, including alongside options such as `repr=False`.
An unproven Daytona-linked factory requires a FIXME. Stable concrete defaults also prove class lookup; factory results do not.
Methods and lambdas do not inherit class-local names. External class-attribute mutations conservatively invalidate class defaults.
Imported project instances carry linked client-field provenance for FIXMEs and `mock-target` items, not proof to rewrite mutable fields. Unrelated same-named classes do not establish that link. Constructing an imported project class uses its defining module's field proofs, constructor metadata and safety hooks, just like a locally defined class. Writes to that class through imports or aliases anywhere in the indexed project demote the named field and its default to linked review evidence; computed names and opaque dictionary updates demote every field. Unrelated members retain their proofs. Exporting that instance through another module still carries only review evidence for its mutable client fields.
Classes or project bases with `__getattribute__` / `__getattr__`, or a replaced `__init__` (assignment or `setattr`), cannot prove instance or `self` fields; linked reads require a FIXME. Lookup hooks anywhere in a client subclass's project hierarchy also leave inherited member dispatch unproven. Direct assignments, `setattr` and `object.__setattr__` replacing moved client methods or service accessors with an unproven value get a `mock-target` item and FIXME; subsequent accesses of that member remain manual, while independent members retain their proofs. `default_language` is a data setting: its direct writes still migrate automatically to `.v0` without an item. Proven same-service direct assignments also migrate automatically.
Relative imports are never treated as SDK imports. Legacy SDK root wildcards split into `from daytona import *` followed by `from daytona.v0 import *`: the bundled v0 wildcard excludes client/config constructors, preserving the root client while retaining v0 resource and error classes. Known SDK exports carry import proofs; later unknown wildcard imports demote them to review-only evidence. Project wildcard imports honor literal `__all__` lists/tuples (including concatenation and `+=`); unresolved declarations do not import receiver proofs. Unproven values may require
manual fixes. Review reports and run tests. `patch.object(client, "create", ...)` and `patch.multiple(client, create=..., ...)` route proven receivers to `client.v0` when every patched member moved, including imported aliases, decorators, context managers and `.start()` calls; already migrated targets stay unchanged. Mixed `patch.multiple` targets require splitting by hand and get a `mock-target` item plus a FIXME. Class targets, unproven receivers and dynamic or non-moved members remain `mock-target` review items. String targets such as `patch("app.client.create")` route through `.v0` when the project index proves the client; unproven Daytona-related prefixes require review, while unrelated targets stay unchanged. Project-owned modules remain exempt even when named `daytona`; actual SDK client bindings and re-exports still follow their proofs. Resource paths still move under `daytona.v0`, and root-client class member paths still require review.
Generic client-like names (`client`, `http_client`) are not evidence: an
unresolved factory result gets a FIXME only with linked Daytona provenance or
a name, callee or resolved path containing `daytona` (case-insensitive).
External module attributes such as `settings.DAYTONA_CLIENT` and `deps.daytona` get a FIXME when untyped.
Local SDK `Daytona` / `AsyncDaytona` assignment annotations (quoted/optional too), including declarations before a separate assignment in the same scope, prove client receivers; unresolved SDK-like annotations require review. Module-level assignment aliases, `TypeAlias` declarations and PEP 695 aliases carry this evidence, including FastAPI `Annotated[Daytona, Depends(...)]` dependencies and quoted alias chains (bounded to 32 resolution steps). Imports carry resolved proof kinds from the alias's defining module, never alias text interpreted against the consumer's names. Aliases under `if TYPE_CHECKING:` also export annotation evidence, not runtime constructors. Forward alias references use module type evidence; conflicting branch assignments, imports and type aliases retain uncertainty. Unresolved client-bearing aliases produce a FIXME, so `--strict` fails. `type[Daytona]` and `Callable` values are not client instances; constructing a proven client class does establish an instance.
Annotations never override concrete non-client evidence or prove unknown `self.client` writes. Typed sequences (`list[Daytona]`, `Sequence[Daytona]`, `Iterable[Daytona]`, `tuple[Daytona, ...]`), generator return annotations (`Iterator[Daytona]`, `Generator[Daytona, ...]`, `AsyncGenerator[Daytona, None]`, `AsyncIterator[Daytona]`, `AsyncIterable[Daytona]`) and `*clients: Daytona` prove loop/comprehension elements; ambiguous client-bearing element types require review. Unannotated local generators support straight-line client yields and strict joins across conditional branches, including asynchronous generators; mixed or unknown yielded values never prove a client. Definition-time constructor evidence is discarded when later writes can replace it. Homogeneous typed tuples support unpacking, including starred remainder lists; heterogeneous tuples retain review evidence per position, without flagging unrelated positions. Single-generator comprehensions forwarding their iteration variable retain element evidence. `Optional`, `Union`, and `Annotated` wrappers require binding-proven imports from `typing` or `typing_extensions`, including aliases. Collection mutations and target rebinding invalidate this evidence. Dictionary lookups, `.values()`, `.items()` positions and client-typed key iteration require review; value annotations never establish client evidence for key iteration. Dictionary spreads, builtin `dict(...)` copies (including `dict(**clients)`), and dictionary unions carry existing client evidence, never invent it for unrelated or unknown mappings. Literal pair variables retain element evidence without flagging typed non-client values. Dictionary evidence remains unproven even when its value annotation is a proven client type.
String annotations using binding-proven SDK module paths move to `.v0`, including composite types such as `"list[daytona.Sandbox]"`; bare imported names follow their imports. Local types and unrelated imports are left alone. Unsupported moved-type strings get a FIXME.
Type strings passed to `typing.cast`, `ForwardRef`, `TypeVar` (bounds, constraints and defaults), `ParamSpec` (bounds and defaults), and `NewType` are rewritten like string annotations, including aliased imports and `typing_extensions` helpers. Starred type operands and `Unpack` references are traversed too.
Explicit `TypeAlias` values and PEP 695 `type` alias values, type-parameter bounds and constraints are treated as annotations too, including quoted types. Python 3.13 type-parameter defaults are also traversed. Run the tool on a Python version that supports your source syntax.
Runtime strings, including arguments of arbitrary calls nested inside type positions, `Literal` values and `Annotated` metadata stay unchanged.
Split imports copy the original trailing comment to both lines, including a plain trailing comment, so line-scoped suppressions remain effective. An unused suppression may need manual removal if your linter enables `RUF100` or mypy enables `warn_unused_ignores` for a copied `# type: ignore`.
Partial-scan notices require omitted Daytona dependencies or imports. Ancestor checks stop at the Git root, or after five parent levels outside Git.

Instances of project subclasses of `Daytona` / `AsyncDaytona` also route inherited
SDK members through `.v0`, including aliases and cross-module bases known to the
project index. A class call stays unproven with any opaque base, class keyword expansion, project `__new__`, metaclass other than binding-proven `abc.ABCMeta`, or decorator (including `dataclass`). A project-defined member stays untouched only when its class
precedes the SDK class in Python's C3 method resolution order. An unknown or
external base before the SDK leaves member ownership unproven and gets a FIXME.
Passing a config to a subclass counts as an escape unless every project class
before the SDK has no construction hooks, or only an undecorated `__init__`
that forwards the same parameters to `super().__init__` without touching them.
Other `__init__`, `__new__`, or `__init_subclass__` hooks require
`--drop-api-url` and manual config review.
Inherited SDK member access through `self` inside a subclass gets a FIXME instead:
rewriting the class itself requires a manual decision about its design.
