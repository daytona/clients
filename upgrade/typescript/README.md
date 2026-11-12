# daytona-upgrade

Upgrade a Daytona 0.x TypeScript or JavaScript project to SDK 2.x **while
keeping its workloads on the v0 platform**. Requires Node.js 20.0.0 or newer.
Documentation links point at the migration guide in this repository
([`upgrade/MIGRATION.md`](https://github.com/daytona/clients/blob/main/upgrade/MIGRATION.md)).

```sh
npx @daytona/upgrade --dry
npx @daytona/upgrade . --yes
npx @daytona/upgrade --suggest-v2
```

Each release migrates to the SDK release of the same version (see
[Versions](#versions)). Until SDK 2.0.0 is out, run the exact alpha you are
migrating to, e.g. `npx @daytona/upgrade@2.0.0-alpha.N`.

The default command moves proven client calls from `client.create()` to
`client.v0.create()` and unconditionally routes resource imports and all 15 shared errors to `@daytona/sdk/v0`, preserving 0.x error constructors, fields and catches; constructor catches need a guaranteed `.v0` access inside the try, and wildcard re-exports require review. Conditional accesses (even in both `if/else` branches) and deferred callbacks keep the constructor FIXME; only straight-line accesses in the construction's block or an enclosing block suppress it. The tool updates
Daytona dependencies to the SDK release this tool was published with (see
[Versions](#versions)). It preserves surrounding source
formatting, including the quote and semicolon style of inserted imports.
When an import or export is split, specifier comments follow their names and multiline lists stay multiline.
Declaration trailing comments are copied to both statements, as are preceding `eslint-disable-next-line` comments.
Commit or back up your project first; dirty Git trees are refused
unless you pass `--force`. Read-only modes (`--dry`, `--print`, `--suggest-v2 --json`) skip the Git check and
write nothing. Outside a Git repository it proceeds with a note.

Each file uses an including TypeScript/JavaScript config: `tsconfig.json`, `jsconfig.json`, their `tsconfig.*.json` / `jsconfig.*.json` variants and recursive references, including unreferenced app configs. References are deduplicated.
Configs mapping more of the file's imports through `paths` take precedence over auxiliary configs without those mappings. Then an explicit `files` entry wins, followed by the most specific matching `include` (longest literal prefix, then fewest wildcards), then the nearest config directory. Remaining ties favor `tsconfig.spec.json` / `jsconfig.spec.json` for `*.spec.*` / `*.test.*` files and `tsconfig.app.json` / `jsconfig.app.json` for other files, then named configs over generic configs.
If including configs with matching `paths` mappings resolve a file's alias imports to different modules, the file stays unchanged with a manual FIXME item: `<file> is included by several tsconfig files that resolve its imports differently; review it manually.` A config without a matching mapping is auxiliary for that import and does not create a conflict. Among mapping configs, a resolved alias and an unresolved alias conflict; two unresolved targets agree. Symlink paths reaching the same real file agree too. These unchanged ordinary files still receive Daytona configuration and environment URL checks; unrelated objects' `apiUrl` or `serverUrl` fields do not cause a refusal.
Installed SDK types identify clients across modules, including type parameters constrained to the root `Daytona` class; without them, syntactic tracking handles constructors and annotations. Unknown receivers are never guessed into `.v0`. They receive a `FIXME(daytona-upgrade)` only when Daytona construction, imports or types participate in their value flow, including factory returns, binding assignments, setter arguments and matching container insertions. Container insertions establish linked provenance only: even an exclusively Daytona `.set()` cannot prove a `.get()` result and never justifies a rewrite. Project import/re-export chains and factory returns are followed with bounded traversal; merely importing Daytona in a file on that path is not evidence about its other exports.

Alternatively, an unknown receiver receives a FIXME when its binding, imported/exported symbol or module-path token contains `daytona` (case-insensitive) and the project declares or imports the SDK. Generic `client` / `clients` names and arbitrary string arguments are not identity evidence: `daytonaClients.get(key).create()` qualifies, but `map.get('daytona').get(key)` does not. Known non-Daytona types and external npm-package origins override names, even before dependencies or typings are installed. Imported clients without SDK types receive review, not a guessed rewrite.

Namespace imports/re-exports retain module-path identity. Linked value flow follows imported container-key symbols and direct call-site arguments, including constructor parameters and selected object properties, for review only. Calls through `.call`, `.apply`, `.bind`, tagged templates, `{ run }` shorthand and destructured method aliases such as `const { run } = wrapper` are not followed. Callback analysis indexes callees and callback flow once per project, memoizing invocation context and recursion exclusions. Local overloads retain callback evidence through implementation parameters; unrelated calls consume no parameter-search budget. Exhausting the shared budget or 64-node recursion limit discards partial results: affected calls receive a review FIXME only if the project imports the SDK, never a guessed rewrite. Parameter defaults supply review evidence, not proof. These bounds cover generated async helpers and handwritten wrappers.
Object-property payload selection, including spreads and duplicate or computed keys, supplies review evidence only; a definite later non-Daytona replacement suppresses that evidence. Getter replacements use their return values. Independent SDK type proofs still allow rewriting. Calling a supplied callback follows its return, not the callback object; an untraceable callback return stays unknown.
Duplicate object-property definitions and potentially replacing later spreads or computed keys invalidate receiver proof, including in JavaScript; an unwritten getter/setter pair is one property, not a duplicate. Detected writes to that property, including compound assignments and `Object.assign` with spread sources or target aliases, keep the pair ambiguous and never justify a rewrite. The last definite non-Daytona value stays unchanged; surviving Daytona candidates require review. Callback parameters link review evidence only from arguments forwarded by the same wrapper invocation, never unrelated invocations. This includes constructor callbacks: `new Wrapper(c => c.create())` requires review when the constructor supplies a Daytona client. Generic callbacks receiving no linked Daytona value remain unchanged. Local options destructuring retains that callback link. An assignment to the callback property anywhere in a wrapper severs its identity with the call-site property; identifiable replacement callbacks still receive review-only argument evidence. The tool does not infer which assignment executes or promote that evidence into a rewrite.

This is precision-first, best-effort migration, not a completeness verifier. For example, an opaque global `client.create()` is an accepted miss; an Express app's `app.get('/daytona')` is a suppressed false positive. A missing FIXME does not prove migration is complete: an opaque 0.x call that still reaches a Daytona root client fails with the SDK 2.x runtime guard's message. `--strict` means **detected manual items remain**, not that every possible 0.x call was detected. Review the migration and run your tests.

A proven client supplied by a file outside the scanned folder still migrates in its consumer, with a manual item per provider file asking you to migrate that file too, including an outside subclass instantiated inside the scanned folder; `--strict` counts those items. Values produced by `<receiver>.v0.<member>(...)`, awaited or not, and stable derivations of those values are v0 objects only when no later call determines the value; missing SDK types on a later run do not add root-client FIXMEs to proven v0 objects. Destructuring assignments and `for…of` / `for…in` targets invalidate initializer-only provenance; linked opaque replacement values require review rather than a guessed rewrite.
Reassignment invalidates syntactic proof, including writes inside branches,
loops or nested functions. Inherited SDK methods are migrated, but a subclass's
own overrides stay untouched, including through nested generic and intersection constraints. A variable widened to the SDK root type with a visible subclass initializer declaring that member keeps its original access with a FIXME. String-literal element access is supported too.
Computed client keys get FIXMEs. Jest/Vitest module helpers rename package targets for review; project-module factory mocks and `mockReturnValue[Once]`, `mockResolvedValue[Once]` or `mockImplementation[Once]` overrides get a `mock-target` review item when result consumers move but replacement v0 shapes are unresolved. Factory alias/declaration traversal cutoffs also require review. Function replacements must return the required shape on every normally completing path; uncertain control flow requires review. Chains through `mockRejectedValue[Once]` are followed, but rejection reasons are not client replacements and need no v0 shape. `spyOn` moves proven clients to `.v0`, flagging prototypes and unresolved targets (`mock-target`). Both resolve globals, imports and CommonJS bindings. Local bare doubles move their literal keys, member writes and assertions to `.v0` only when every whole-object use reaches a scanned project function with an exact SDK root-client parameter whose accesses also move in that run. Overloads are checked against their implementations. Consumers without direct parameter references, or using `arguments`, `eval` or `Function`, keep the double unchanged. Subclass-typed, external and declaration-only consumers also keep its shape. Unadapted arguments reaching a rewritten consumer receive a persistent `mock-target` FIXME, including casts, classes, shared exports, mixed consumers, identity-observed doubles and existing `v0` members; `--strict` exits 1. Review follows constructor injections (including parameter properties), rest elements, destructured parameters and option properties; these flows do not authorize wrapping. Subclasses overriding a moved consumer member are reported at the supplied argument. Its message is: `Mock replacement reaches a Daytona client consumer but its v0 shape is unresolved; give the replacement a v0 attribute before using SDK 2.x.` Recognized `mock` / `mockDeep` factories from `jest-mock-extended` and `vitest-mock-extended` support named imports (including aliases) and namespace imports. Empty `mockDeep<Daytona>()` factories keep their root shape while setup and assertions move through `.v0`; shallow typed mocks, supplied implementations and unresolved type arguments require review instead of wrapping. SDK declaration identity also follows workspace and npm-link package manifests, rather than requiring a `node_modules` path.
Literal `in` checks on proven clients move to `.v0`; unproven clients require
manual review. Unresolved `super` calls in Daytona subclasses also require review.
Unparsable files mentioning Daytona (case-insensitive) or importing a relative
module, a configured alias or a workspace package block the entire run
before source or manifest edits. Other unparsable
files are skipped with an informational `unsupported-file` item. A Flow pragma
alone does not block source that parses successfully.
Build directories (`dist/`, `build/`, `out/`, `.next/`, `.output/`, `coverage/`
and `storybook-static/`) are excluded from traversal. Git-ignored source files
are not rewritten, but still participate in all safety scans. When Git-ignored
source files mention Daytona or would change during rewriting, one manual
notice reports their count and up to five project-relative paths, followed by `and N more`;
migrate those files or their generator. A skipped source receives only its more specific skip item, not a second ignored-file notice. The notice is omitted when no ignored source meets either
condition. Ignored sources do not count toward unresolved-type notices. Environment
and deployment files are never excluded by Git ignore rules. Without Git ignore
information, the fixed directory exclusions still apply.
Generated or minified sources (`*.min.js` / `*.min.ts` variants or files with a `sourceMappingURL` comment)
are not rewritten or given suggestions. Size and average line length alone do not make a file generated.
Generated sources mentioning Daytona (case-insensitive) receive URL safety scans. Known custom `apiUrl` / `serverUrl` literals always refuse, even with `--drop-api-url`; for those fields the flag only covers values the tool cannot read. Environment writes (`process.env`, including static string and template-literal process sources) follow the environment override rules in [Safety and scope](#safety-and-scope).
Skipped sources mentioning Daytona or importing another module receive an `unsupported-file` manual item asking you to review the file or its generator; this includes relative imports, path aliases and workspace packages.
Other sources with a line of at least 10,000 characters are skipped for speed only without Daytona or
`apiUrl`/`serverUrl` references (case-insensitive), with an `unsupported-file` item; otherwise they are analyzed normally.
If a dependency update to 2.x is planned while a file is skipped, its item warns that the file needs manual migration, including in dry runs and when confirmation is declined.
Environment-write scanning also runs on Daytona-mentioning skipped sources, including computed setting names.
Git-ignore handling and generated/long-line skipping are TypeScript-only; Python applies neither exclusion.
Informational skip items do not block a normal run; `--strict` counts them as
manual items. Unresolved SDK types produce one notice per run with a file count.
For inline statements, suggestion insertion may add a line break so comments
cannot consume code. Removal deletes only matching comment lines; it does not
join source lines or restore the original inline spacing. Inserted line breaks
may remain. Re-running suggestions preserves those breaks and existing CRLFs.
Only files with planned edits must be valid UTF-8. Each edited file with invalid
bytes produces an `unsupported-file` item and the run exits 1 without writing
any files. Analysis and environment scanning replace invalid bytes for reading
only; unedited files stay byte-identical, and custom environment URLs still
refuse the upgrade. Existing UTF-8 byte order marks are preserved.
In both tools, unreadable relevant files and directories each produce an `unsupported-file`
item at line 0, column 0 with a project-relative path. Permission failures say
`<relative> could not be read: Permission denied; fix its permissions, then rerun.`
(`fix the problem, then rerun.` for a non-permission error)
Other read failures use the error's human description instead of `Permission denied`.
The tool continues checking readable files, reports all discovered unreadable
paths, and exits 1 without writing anything. A custom API URL found in a
readable file still takes precedence and exits 2. Unrelated binary files are
not read.
Writes to object-literal properties invalidate client proof too. Structural
types such as `Pick<Daytona, 'create'>`, `Partial<Daytona>` and interfaces do
not prove a client instance. Extracted client methods (`bind`, `call`, `apply`,
values passed to other functions) and client destructuring receive manual
items instead of rewrites: moving methods can change their `this` receiver.
Service properties used as values still move to `.v0`.
`Daytona.createAxiosInstance`, including constructor-alias accesses and destructuring, receives a FIXME: `/v0` exports no `Daytona` class (only the internal `RootOnlyClientV0` alias), so use a standalone HTTP client. A subclass's own static implementation, including one inherited from a project base class, stays untouched.

Namespace resource destructuring and never-reassigned namespace aliases are
supported, including CommonJS. Direct properties of `require(...)` and `(await import(...))` route by export name; un-awaited `import(...)` Promise methods (`then`, `catch`, `finally`) keep the root import and require dynamic-import review. Exported or otherwise escaping namespaces,
computed names, some namespace-dependent destructuring defaults and reassigned
aliases require manual review. When a pinned v0 export list is
available, names missing from it are reported instead of silently accepted.
Escaping or exported holders lose property proofs. Calls to `this.m()` preserve proofs only when every implementation in the receiver's class and its direct/transitive project subclasses does not expose `this`, transitively. `super.m()` follows the base implementation on the same instance. Unresolved methods, opaque overrides, dynamic calls and helper chains beyond eight methods require review. Unresolved bases affect only their own hierarchy, and a helper declared before that base is still analyzed. Exported classes retain the project-only policy: hypothetical subclasses outside the analyzed project are not enumerated. Recursive helpers are cycle-checked; dispatch targets, subclass sets and escape summaries are memoized in one shared project index.
Calling a proven root SDK method preserves its client's service objects; user-holder methods and passing the client as an argument still drop service-property proofs. This also applies to a proven client's `.v0` after upgrading and installing SDK 2.x, including class-field clients: the shared SDK namespace getter retains each client's own identity, so one client's escape does not affect another client. If the default pass can prove a root-client access but suggestions cannot prove its service call, the suggestion report includes a manual FIXME instead of silently omitting it.
Only lexical arrows inherit the method's `this`; ordinary nested functions and object methods have separate receivers. Explicitly passing the owner as a callback receiver conservatively counts as an escape. `this` escapes fence later uses in the same function, including earlier uses on a loop back-edge, except readonly client fields or annotations with proof-grade, resolved SDK client types. Passing a client itself preserves identity.
Reflective writes (`Reflect.set`, `Reflect.defineProperty`, `Object.defineProperty`, `Object.defineProperties`, and `Object.assign`), including target aliases, inherited prototypes, `Reflect.set` receivers, stable `const` mutator aliases and `globalThis.Reflect` / `globalThis.Object` forms, invalidate affected member proofs too.
An unannotated parameter default does not prove the values callers supply.
Subclass constructors returning `this` or using bare `return` still prove a
client. Any other constructor return requires a manual FIXME; returns inside
nested functions do not count. Explicit SDK type annotations still provide
independent client evidence unless the constructor can replace the instance.

SDK `declare module` augmentations move to `@daytona/sdk/v0` for pinned 0.x
resource entities. Augmentations touching `Daytona` or `DaytonaConfig`, including
mixed blocks, stay unchanged with a `private-import` manual item:
`Review this module augmentation: the 0.x Daytona client is exported from @daytona/sdk/v0 as RootOnlyClientV0, and DaytonaConfig changed in SDK 2.x.`
The bundle exports the old config only as `RootOnlyConfigV0`; neither old public
name is exported from `/v0`. Augmentations of shared errors
such as `DaytonaError` move to `@daytona/sdk/v0`. The same routing applies to old
private SDK subpaths. Mixed, empty, or unrecognized augmentations stay unchanged
with a `private-import` manual item:
`Review this module augmentation: SDK 2.x moved 0.x types to @daytona/sdk/v0.`

## Versions

Each release of this tool is published with the SDK release of the same
version, and migrates to exactly that release: `npx @daytona/upgrade@2.0.0-alpha.N`
writes `"@daytona/sdk": "2.0.0-alpha.N"` (a pre-release is pinned, because
pre-releases may break), and a stable `X.Y.Z` writes `^X.Y.Z`. Run the release
that matches the SDK you want; the SDK's runtime guidance for 0.x calls names
it.

SDK 2.x runs one 0.x release as `.v0`; the tool's warnings and refusals name
it. The tool reads the project's installed 0.x
version from its lockfile (`package-lock.json`, `npm-shrinkwrap.json`,
`yarn.lock`, `pnpm-lock.yaml` or `bun.lock`, the closest one up to the Git
root), else from the lower bound of the manifest range, and compares:

| Installed 0.x version | Result |
| --- | --- |
| the `.v0` release | nothing to report |
| older, or a newer patch of the same minor | a warning (`warnings` in `--json`): moved calls now run the `.v0` release; review the 0.x changes in between |
| a newer minor | refused (exit 2, reason `newer-v0`): 0.x APIs added after the `.v0` release are missing from it and would get no FIXME. Use a later tool release, or pass `--allow-newer-v0` to migrate anyway (with a warning) |
| unknown | a warning |

Warnings are advice, not manual items: `--strict` ignores them.

## Options

| Option | Behavior |
| --- | --- |
| `[path]` | Project directory; defaults to the current directory. A file path is refused (exit 2); the Python tool also accepts a single file |
| `--suggest-v2` | Add replaceable `// daytona-v2:` guidance above v0 calls; never change code or dependencies |
| `--remove-suggestions` | Delete all `// daytona-v2:` comment lines |
| `--dry` | Show a unified diff with three lines of context, write nothing, skip the Git check |
| `--print` | Print changed files instead of writing them; skip the Git check |
| `--json` | Emit the structured report; with `--suggest-v2`, write nothing |
| `--force` | Skip the Git check entirely, allowing uncommitted changes |
| `-y`, `--yes` | Skip confirmation; non-interactive input never prompts |
| `--drop-api-url` | Accept API URLs the tool cannot verify or that come from the environment, asserting they point at Daytona Cloud |
| `--strict` | Exit 1 if manual items remain, even when confirmation is declined (no writes) |
| `--allow-newer-v0` | Migrate a project on a newer 0.x minor than the 0.x release SDK 2.x runs as `.v0` (see [Versions](#versions)) |
| `-v`, `--version` | Print the package version |
| `-h`, `--help` | Print help |

## Safety and scope

Custom API URLs stop the entire upgrade **before any files are written**:
SDK 2.x targets Daytona Cloud. Stay on SDK 0.x for a custom endpoint. The
Daytona Cloud literal `https://app.daytona.io/api` (also with a trailing slash)
is removed silently, as are `undefined` and `null` URL fields and standalone
assignments of these values, only when no other config readers need the fields. Shared or caller-owned configs (including parameters and imports) keep their URL fields and receive a constructor FIXME: pass a separate config without URL fields to SDK 2.x. Within config objects, `--drop-api-url` permits
non-literal values only, with a warning; it does not override an explicit custom
config URL.

Literal custom `DAYTONA_API_URL` or `DAYTONA_SERVER_URL` settings in `.env*`, loader-selected env files, deployment YAML or source-level `process.env` assignments also stop the upgrade. For symlinked Compose manifests, relative includes and environment files are scanned from both the configured path's directory and the symlink target's directory; canonical identities prevent include cycles without replacing configured paths.
Literal paths in dotenv/dotenvx `config`, dotenv-flow directories and Node `process.loadEnvFile` are scanned, including arrays, plain templates and `path.join`/`resolve` with `__dirname`, `import.meta.dirname` or `process.cwd()`; relative strings check both project root and source directory, and nested dotenv calls inside dotenv-expand are checked too. Explicitly loaded `.env.example`, `.env.sample` and `.env.template` files are scanned too. Compose manifests include `compose*.yml`/`.yaml` and `docker-compose*` files, YAML with a top-level `services` mapping, and include-only YAML whose entries are strings or mappings with only `path`, `env_file` and `project_directory` keys. Literal package-script `docker compose -f` / `--file` selections and `COMPOSE_FILE` values in scripts or project dotenv files select manifests regardless of filename; `.gitlab-ci.yml` is never inferred as Compose unless explicitly selected. Selection scans colon, semicolon and literal `COMPOSE_PATH_SEPARATOR` candidates. Service `env_file` strings, lists and `path` entries load environment files; literal `include` paths (including `path` lists) recursively load Compose manifests with cycle protection, and include `env_file` strings or lists load interpolation files relative to a literal `project_directory` or the included manifest's directory, alongside every existing candidate relative to all Compose manifest directories, the project root and literal script `--project-directory` values. Aliased service or include loader inputs require review. Package-script `--env-file` / `--env-file-if-exists` paths resolve relative to `package.json`. Backslashes are accepted as separators in YAML paths and, before letters, digits, `.`, `_` or `-`, in script paths; ambiguous shell escapes still require review. On POSIX, existing literal-backslash filenames are scanned too. Missing or non-literal paths and non-literal Compose controls require `env-api-url` review. Only regular files are scanned; environment/YAML reads and Compose parsing are capped at 5 MiB, with oversized Compose-looking or runtime environment inputs requiring review. Because the app's runtime cwd is unknown, cwd-relative loader paths (including default `.env` loads) require review unless the project-root candidate exists and is scanned; unclassified loader bindings require review too. `dotenv/config` imports, requires and script preloads always require review because `DOTENV_CONFIG_PATH` or `dotenv_config_path=` can select another file; the root `.env` is still scanned.
This includes `Object.assign(process.env, {...})`, replacing `process.env`
with an object literal, literal spreads, and named `env` imports from `process`
or `node:process`, including aliases. Destructured aliases such as
`const { env: settings } = process` or `require('node:process')` are recognized too,
as are `globalThis.process.env`, `global.process.env`, `window.process.env` and `self.process.env` writes when the global name is not locally bound. Stable variable keys are resolved; writes
with unknown keys or unresolved object sources receive an `env-api-url` review
item without refusing the run. Known unrelated keys and reads are ignored.
Docker `ENV` accepts multiple `KEY=value` pairs and backslash continuations.
Docker `ENV` / `ARG` scanning includes `ONBUILD` instructions. Closed heredoc
bodies are ignored, including those in `ONBUILD RUN`; if a terminator is missing,
the remaining lines are scanned as instructions so malformed input cannot hide
later URL diagnostics.
YAML under `k8s/`, `helm/`, `charts/` or `deploy/` is matched on the path
relative to the Git checkout (the whole absolute path when Git cannot answer), so scanning
`deploy/` directly applies the same checks as scanning its parent.
`.env.example`,
`.env.sample` and `.env.template` are ignored unless explicitly loaded; `.env.test` is scanned.
Comments and empty environment values are ignored. Cloud URL schemes and hosts
are compared case-insensitively. Whole-value `${NAME:-default}`, `${NAME-default}`, `${NAME:+default}` and `${NAME+default}` expressions classify their literal fallback; `${NAME:?message}` / `${NAME?message}` (the text is an error message) and missing or dynamic fallbacks require review. References inside another variable's value are not declarations.
For these environment settings only, `--drop-api-url` permits proceeding with
an `env-api-url` review item; it leaves the setting unchanged. Cloud and
non-literal environment settings are reported without refusing the run.
Without the override, a literal custom environment URL produces both
`env-api-url` and `custom-api-url` items.

The tool never migrates workloads to v2, changes sandbox calls into client
calls, edits lockfiles directly, or modifies Python code. It reports Python
manifests with the companion command `uvx daytona-upgrade@<this release>`. Peer dependencies,
private imports, framework component files, environment URL settings and
unsupported config fields require review. Suggestions include docs links and
workflow/data warnings; re-running replaces the tool's comments.
Shared-error import guidance appears in comments and JSON suggestion items, including namespace imports used through stable local aliases or destructuring.
Workflow headers summarize data migration once per statement and link to the data guide.
They group sandbox methods and client/service calls that receive that sandbox
as a bare argument or a top-level object property, in source order within the binding's scope, including nested closures declared before or after its creation.
Calls inside nested functions that use the sandbox count too.
Client calls retain their callee text in the header.
Workflows using root sandbox `start`, `stop`, `pause`, `archive`,
`setAutostopInterval`, `setAutoPauseInterval` or `setAutoArchiveInterval`, or
client `start` / `stop` calls receiving that sandbox, instead recommend keeping
the existing 0.x lifecycle workflow on `.v0`. In v2, stop, start and pause
apply to macOS sandboxes. The header retains the
workflow name and lists only its lifecycle calls in source order, including deferred calls
inside nested functions and arrows referencing that workflow's sandbox, even when declared before its creation.
`setAutoDeleteInterval` and nested service lifecycle calls do not trigger it.
Service aliases use their proven sandbox owner in suggestions; service-typed
parameters with no known owner use a service-level form when the service path
is unchanged. When the v2 service moved, a manual item asks you to move the call
to its owning sandbox and shows a form using the literal placeholder `sandbox`.

Every output is computed before writing starts. All new contents are prepared
in exclusive `.<name>.<random>.daytona-upgrade.tmp` files beside their targets,
with the original permission bits copied and the contents flushed to disk
before any target is replaced. The tool then renames them into place in order.
If preparation fails, no target changes; if a rename fails, already-replaced
files are restored from their in-memory original bytes using the same
temp-file-and-rename route, and newly created targets are removed. Leftover
temporary files are cleaned up. Filesystem errors preventing rollback or
cleanup are reported with the affected paths. Each replacement is atomic,
but a crash or kill between renames can leave a partially applied upgrade;
this is not a crash-atomic transaction across multiple files.

After changing dependencies, the nearest lockfile within the selected project
root selects the next-step command: `npm install`,
`yarn install`, `pnpm install`, or `bun install` (npm if no lockfile exists).
Printed install directories are relative to your current working directory
(`.` for that directory). The tool **never runs the package manager**: it edits
`package.json` only and prints the command for you to run. It does not execute
project lifecycle scripts or install dependencies over the network.

Config preflight follows local literal spreads and assignments to const config
objects, stable constructor/config aliases, `Object.assign` sources, and simple
same-file helpers returning a config. It starts at proven Daytona constructors
(including object-property aliases, parameters typed `typeof Daytona`, `Daytona.bind(null, config)` and `Reflect.construct(Daytona, [config])` with simple array spreads),
explicit SDK `DaytonaConfig` annotations, `satisfies` / `as` expressions, and
functions with an SDK `DaytonaConfig` return type:
unrelated objects with `apiUrl` or `serverUrl` fields are left alone in normal
source analysis. Generated sources mentioning Daytona instead receive a
conservative URL-only scan of `apiUrl` / `serverUrl` object properties, property assignments, class fields, getter returns and `Object.defineProperty` / `Reflect.defineProperty` value descriptors, including constant computed keys, compound assignments and fields inside closures or exported objects.
Every non-empty, non-Cloud string or no-substitution-template literal in those fields always refuses, even with `--drop-api-url`, including unrelated vendor URLs. To proceed past an unrelated vendor URL, remove the bundle from the scanned tree: delete it, or run the tool on the source directory instead of the project root.
Each generated file reports at most five custom URL literals, followed by one summary item, `<n> more custom URL literals in <file>`, when additional literals are found.
Only non-literal values can proceed with `--drop-api-url`, with a `config-field` manual item and without rewriting the generated source. Cloud URLs pass and empty strings are ignored.
A computed or otherwise unverifiable
config blocks the upgrade unless `--drop-api-url` explicitly asserts Cloud use; unresolved
config sources then remain unchanged with a manual item. Explicit custom URL
literals still block the upgrade, including in a spread, assignment, or possible first argument after an opaque spread in `Reflect.construct` or `Daytona.bind`.
An explicit URL followed by a spread, duplicate key or computed key is unresolved,
including when passed through a destructured subclass constructor. An earlier
spread does not invalidate a later explicit property in that destructuring path;
whole-config preflight still checks all spread sources for other URL fields.
Parameters, imported objects, cross-module factory results, and configs passed
to another function require this assertion when their origin or mutations cannot
be resolved. The override leaves unproven sources untouched and reports
`config-field`; it does not assume they are safe to edit.
A wrapper forwarding an optional config (`constructor(opts, config?: DaytonaConfig) { this.daytona = new Daytona(config) }`) refuses with `custom-api-url` (exit 2) because its URL cannot be proven; rerun with `--drop-api-url` if that config always targets Daytona Cloud.
Module-level const configs remain resolved inside functions, methods and
constructors. Read-only `console.*`, `JSON.stringify` and imported `util.inspect`
calls do not count as escapes; shadowed names do. Unknown calls receiving a
config through an object, array or property alias still require the assertion.

A POSIX source path containing a literal backslash is reported as unsupported.
Other files are still analyzed, but no changes are written until the path is
renamed so that the entire project can be checked safely.

Plain-string Daytona entries in npm `overrides`, yarn `resolutions` and
`pnpm.overrides` are updated with dependencies. Non-string constraints require
manual review, including `$@daytonaio/sdk` and `$@daytona/sdk` references in
other override entries. Custom dependency sources (`workspace:`, `file:`, `link:`,
Git URLs, npm aliases under any key and registry tags) and scoped Yarn resolution selectors are preserved
with manual dependency items rather than replaced with a registry version.
CommonJS object rest bindings stay on the root module when
resource imports split; resource reads through those bindings require review.

Suggestion removal and replacement recognize real own-line comment tokens,
never tagged-looking text inside strings, templates, JSX text or block comments.
The own-line prefixes `// daytona-v2:` (TypeScript/JavaScript) and
`# daytona-v2:` (the Python companion) are reserved: every matching comment
line is deleted, including hand-written notes.
Do not use these prefixes for comments you want to keep.
Formatting whitespace is collapsed for comment display only; string, template
and regular-expression literal contents are preserved. Multiline literals use
comment continuations so their contents remain intact.

## Exit codes

- **0**: completed; manual items may remain.
- **1**: unreadable relevant files/directories, source parse errors or invalid UTF-8 in files with planned edits; manual items remain with `--strict`; or an internal/write error.
- **2**: refused: dirty tree, custom API URL, a newer 0.x minor than `.v0`
  (without `--allow-newer-v0`), bad arguments, or no Daytona code or
  dependency found.

From `2.0.0` on, within a major version the flags, these exit codes and the
fields and values of the `--json` report ([`report.schema.json`](https://github.com/daytona/clients/blob/main/upgrade/mapping/report.schema.json))
stay compatible. A minor release can add flags, report fields and values to
the `language`, `mode`, `kind`, `refused.reason` and `suggestion.category`
vocabularies, rewrite more calls, report new manual items and refuse a case it
newly recognizes as unsafe. Ignore fields and values you don't know.

Licensed under Apache-2.0.
