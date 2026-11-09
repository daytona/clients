# Python upgrade-tool development

## Project summary convergence

Project indexing uses a dependency worklist without a pass limit. Each module's
analysis records the export summaries it reads, including local factory summaries,
imports, namespace attributes, and implicit pytest fixtures. A changed summary
queues its dependent modules; unchanged modules retain their analyzed flow.

Worklist waves read the same index before publishing their updates, preserving
the previous whole-project fixed-point semantics. Class-mutation fences are
global inputs read by every flow, so new fences invalidate every analyzed module.
Config preflight runs only after convergence, with retained flows attached to the
final index.

`tests/test_performance_factory_worklist.py` exercises the real CLI with 200 factory
links across 50 consumer-before-provider modules. It checks that the final call
retains its FIXME, bounds module analyses to catch quadratic rescanning without
depending on machine speed, and allows 20 seconds for the complete CLI run.

Long factory chains within one module still require one whole-module pass per
new link. Moving that loop inside the module would not reduce the work. Safe
function-granularity caching needs dependencies for runtime bindings, annotation
bindings, deferred writes and class-mutation fences: `FunctionProofs` currently
keys its cache on entire execution/annotation environments, and `ExportReads`
records modules, not functions. This optimization is deferred rather than
changing fixed-point semantics or introducing a pass limit.

## Environment and mock safety regressions

`test_module_return_proofs.py` and `test_module_return_boundaries.py` cover lazy
module helpers, including Harbor's verbatim error-classification source.
`ReturnFlow` certifies module returns only when all observed returns are root
module proofs and no path falls through. Mixed results, unknown decorators and
deferred writes retain module-specific review evidence. These kinds use the
same cached function summaries, project exports and binding joins as client
factories; they do not introduce another project analysis.

`test_module_factory_mocks.py` covers replacement of those lazy helpers through
string and module-object patch targets, including Harbor's fake-module fixture.
The mock pass reuses indexed target lookup. A locally constructed module fake
used only as the replacement gets its known 0.x submodule paths rewritten under
`.v0`; escaped objects, dynamic paths and review-only helpers retain one
`mock-target` item at the patch site. `test_local_module_doubles.py` covers the
ownership boundary and supported lambda/mock replacement forms.

`test_local_client_doubles.py` and `test_client_parameter_boundaries.py` cover
bare local doubles whose every whole-object use supplies an explicitly proven
root-client parameter. Indexed contracts keep bound and unbound method slots
separate, preserve class-owner metadata solely for contract lookup, and reject
unpacked calls, replaced consumers and unknown decorators. Ordinary receiver
proofs are not promoted by these contracts. Source-position refinements route
the double's member setup and assertions together without exporting its shape.
`test_double_runtime.py` executes migrated consumers and doubles together.
Non-autovivifying module fakes get an identity-preserving `fake.v0 = fake` alias;
owned plain client classes get a read-only `v0` self property. Client namespace
literals wrap only moved members, including nested mocked client-returning methods
whose original contract is indexed. Mixed or escaped shapes retain `mock-target`
review, and existing `.v0` mock setup cannot be merged with root setup. Unannotated
factories with one proven project-class result carry parameter-owner metadata only:
this enables contract lookup without promoting ordinary receiver proofs.
`test_double_boundaries.py` covers strict-mode refusals and idempotence.

`test_client_proof_precision.py` covers indexed submodule imports inside
constructors, exact client-kind matching on review-only instances, shadowed
nested-helper parameters, FastAPI dependency factories, and cached-property
receivers. Dependency registration preserves captured class-field proofs; a
factory returning a different project type still requires review.
`test_double_adaptation.py` executes one-line fake classes, reflective-write
and layout refusals, semicolon import expansion, and mock factories with extra
explicit keywords. Module self-aliases follow the rewritten assignment's identity,
not its position in an import-expanded statement line. Ambiguous `side_effect`
or unpacked mock keywords retain the existing `mock-target` item.

`test_factory_consumer_adaptation.py` executes patched client factories through
both migrated and unrewritten consumers. A client-return annotation supplies a
review link, not permission to reshape a replacement: every indexed result use
must reach changed accesses without opaque forwarding. Escaped factory callables
also prevent adaptation. The result-use check reuses the typed-relay inspector;
factory identities are retained even for zero-argument declarations.
This gate applies to directly patched client-returning factories; existing
bounded nested manager-literal expansion retains its separate contract lookup.
`test_field_injection_and_async_forwarding.py` covers review-only links for `model_construct`,
`model_copy(update=...)`, attrs `evolve`, and dataclasses `replace` when their
keyword or update key names a proven client field. `anyio.run`, `trio.run`, and
the inner call of `asyncio.run` link forwarded arguments to changed consumers
without authorizing automatic double adaptation. All use the existing
`mock-target` diagnostic.

`test_factory_patches.py` and `test_factory_patch_consumer_links.py` cover
default decorator/context mocks, later `return_value` setup, opaque side effects,
and monkeypatch/mock-fixture installations. The index retains observed namespace
consumers separately from adaptation permission. A patched client factory reaching
those consumers gets the existing `mock-target` item at its patch site unless its
bounded replacement is already adapted. Unchanged-only consumers do not acquire
default-mock warnings; manager-returning factories keep their nested adaptation.
`test_required_field_forwarding.py` covers required client-field annotations as
review-only injection evidence, including attrs and Pydantic fields without a
default factory. `test_mock_discovery.py` covers isolated mocks-only CLI
scans: recognized SDK patch paths establish Daytona presence even without an SDK
import or dependency manifest.

Harbor's snapshot builder also needs literal tuple-return proofs. A declared
heterogeneous tuple can certify its root-client slots only when every return
supplies a literal tuple of the same length with proven clients in those slots,
and no path falls through. Unknown returns retain the annotation's existing
review evidence. `test_tuple_client_returns.py` covers both outcomes.
Known signature-preserving retry/cache decorators and binding-proven
`typing.override` / `typing_extensions.override` retain the instance receiver;
unknown or shadowed decorators do not. Class-wide field analysis also resolves
function-local SDK imports, retaining its normal rebinding and control-flow fences.
`test_override_and_local_import_provenance.py` covers both mechanisms. For these tuple summaries only, a guarded optional
SDK constructor import can certify a successful call when all module writers
are the same SDK import, a runtime ImportError-guarded import exists, and no
global rebinding or opaque namespace writes exist. A foreign fallback, a local
shadow or type-checking-only import cannot supply that proof.

`test_singleton_client_methods.py` covers Harbor's async singleton manager,
including cross-module callers using a local manager or a holder attribute.
Project-class annotations produce the existing instance kind, but a contradictory
concrete subclass default or return reduces that proof to review-only; exported function
summaries qualify that instance's class owner for downstream method lookup.
Client return annotations remain declared contracts even with opaque returns.
The existing sole-writer optional-import proof also supports these annotations;
foreign fallback writers and local shadows do not qualify. Ordinary process
`.exec()` calls are not Python's namespace-mutating builtin; builtin aliases
remain fenced through the existing reflective-alias analysis.
That fence follows assignment, conditional, annotated and unpacked aliases of
namespace writers and literal `getattr(builtins, ...)` lookups
(`test_reflective_aliases.py`).
Namespace exposure through `vars`, `locals`, `globals` or `__dict__` disables
sole-writer certification, as do potentially aliasing attribute stores and
shared declarations in class suites or nested functions. Method parameter
contracts resolve annotations against each method's definition-time bindings,
including class-local shadows, before they can certify a bare double.
Module/client joins keep a combined review-only kind through opaque calls and
return summaries; repeated demotion preserves function-wrapped review markers.
`test_namespace_certification.py`, `test_method_annotation_scope.py`
and `test_module_join_uncertainty.py` cover these precision boundaries.
Explicit client-parameter annotations now feed the same reassignment contracts
as annotated locals, scoped to their function. An opaque manager fallback keeps
that declaration's proof; a concrete foreign-client replacement does not.
Alias snapshot construction lives with export freezing and reuses the annotation
resolver.

`test_client_defaults.py` covers optional-client fallback stores and
review-only mixed operands. `test_field_defaults.py` covers annotated
attrs/Pydantic factory fields; converters, validators and opaque initialization
retain annotation evidence rather than certifying a store. Both use
`DAYTONA_UPGRADE_TEST_SDK_PYTHON` for network-free execution on a bundled SDK.
`test_relay_runtime.py` executes typed identity/tuple relays and literal
argument forwarding. Parameter summaries retain return-slot identity separately
from changed accesses; caller analysis follows results to actual namespace reads.
Opaque return identity or incompatible uses require `mock-target`, never wrapping.
Constructor injection into these newly proven fields also retains `mock-target`
for unresolved doubles; holder ownership never authorizes automatic wrapping
(`test_constructor_doubles.py`).

`test_fallback_joins.py` covers non-null Boolean/conditional client joins.
Non-null constructor and direct annotation evidence travels with lexical aliases
and is invalidated with their bindings; nullable annotations alone cannot certify
an `and` result or an unguarded conditional. Generated constructor contracts use
actual init slots, including required fields, excluded fields, keyword-only
inputs and literal attrs aliases (`test_constructor_contracts.py`). Uncertain
signatures retain review-only argument evidence, never adaptation authority.
Method-store scope setup lives in `class_method_stores.py`, separate from the
class-wide accumulator. Conflicting alias return slots remain ambiguous for both
homogeneous and heterogeneous tuples (`test_relay_slots.py`). The optional
field-default runtime witness requires `DAYTONA_UPGRADE_TEST_SDK_PYTHON` and skips
when it is unset. It checks imports inside that SDK interpreter and also skips
snippets whose optional framework dependencies are unavailable.
`test_proof_retention.py` covers unresolved generated aliases, typed method
varargs, non-null annotated attribute stores and mixed-collection relay contracts.

`test_manifest_table_order.py` covers disconnected TOML parent/child tables.
Manifest readers accept tomlkit's live `OutOfOrderTableProxy` alongside ordinary
tables, including resolver policy and source overrides, and mutate the original
document rather than rebuilding tables and losing formatting.
Scalar replacements also copy the original value's complete trivia, independent
of the containing table or array; `test_dependency_trivia.py` covers
inline comments and whitespace in project, optional, Poetry and PDM dependencies.

`module_proofs.V0_SUBMODULES` records the six root package directories in the
bundled v0.230.0 SDK, separately from public root exports. Refresh this list with
the bundle pin. Missing-export checks exclude these directories, not arbitrary
unknown names or all private-looking identifiers.

`test_explicit_dotenv_preflight*.py` exercises the real CLI/preflight, including refusal
before source or dependency writes, literal/default/aliased loader paths,
Pydantic settings declarations, shadowed loaders and notebook coordinates.
Loaded files use the dotenv scanner even if their extension suggests YAML or a
sample file; the runtime loader, not the filename, establishes their role.

`test_synchronous_mock_specs.py` checks positive sync-class provenance separately
from shared client types, including method factories, imports, aliases and
postponed annotations. `test_mock_runtime.py` follows the existing
`DAYTONA_UPGRADE_TEST_SDK_PYTHON` convention to inspect property-backed mocks
against an installed, bundled SDK without network requests.

Environment path calls and ordinary call-result proofs share one callee
resolution through `Resolver.call_kind` and `CallProofs`. Do not resolve the
callee again before falling back: each extra resolution recursively repeats
the work for the preceding links in a chained call. The environment resolver
only resolves path arguments after the callee establishes a supported path
operation. No proof cache crosses a binding change.

`test_performance_environment_resolution.py` bounds inference work relative to
the ordinary resolver, rather than relying on machine-specific timing. The
regression required 13,092 inferences versus 874 for an ordinary SDK chain;
sharing the callee removes that amplification without changing the result.

`test_dotenv_search_and_settings_overrides.py`, `test_mock_constructor_mutations.py`
and `test_report_items.py` cover upward dotenv searches, constructor `_env_file`
overrides, overwritten/nullable mock factories, unrelated versus unresolved
mutation owners, and stable report de-duplication. Search provenance survives
filename aliases; a clean root file never hides an intermediate candidate or
resolves runtime-dependent inputs. Canonical JSON report keys keep encounter
order without comparing each item against every preceding prefix.

## Shared configuration and deployment inputs

Environment-only settings provenance survives class decorators. A decorator may
replace a settings class, but cannot establish that its literal `_env_file` input
is safe. Literal constructor and inherited `env_file` inputs are scanned; dynamic
inputs retain dotenv review. This provenance never authorizes SDK rewrites.

Compose `env_file` scalars, lists and long-form `path` entries resolve relative to
the declaring YAML directory. Existing files are scanned as dotenv regardless of
their names or extensions. Interpolation, missing files and unreadable inputs
retain environment review. Comments and YAML block-scalar payloads are not loader
declarations; `required: false` does not hide an existing custom endpoint.
Bounded tokenization also recognizes nested flow mappings and quoted keys without
adding a YAML dependency. Anchors, aliases, merges, tags and document markers
retain review rather than certifying a clean literal candidate. Custom-named YAML
with a top-level `services` mapping receives the same inline and loaded-file
checks; nested Kubernetes data does not establish Compose provenance.

Compose include uncertainty is scoped to the include value: unrelated service
anchors and aliases do not make a literal include unresolved. Included manifests
retain their lexical locations for nested lookups; both lexical and canonical
directories supply candidates. Canonical ancestor paths stop include cycles,
while lexical locations are deduplicated separately so two symlinks to one
manifest cannot hide different nested inputs. Every existing candidate is scanned.
`test_compose_include_boundaries.py` exercises these rules through the CLI.

Config URL verification and destructive URL cleanup have separate ownership
rules. A config used only as a client argument can lose its Cloud URL fields.
Other lexical readers or readers across project imports preserve those fields,
with a `config-field` report and constructor FIXME explaining that the client
needs its own URL-free config. Mutation and escape refusal rules still apply;
URL writes on caller-owned or shared configs also remain intact with a FIXME.
`--drop-api-url` does not override a known custom config URL. Review-only mock
links also survive conditional and assignment-expression specs, without enabling
automatic rewrites for an unknown mock shape.

`test_settings_and_compose_environment.py`, `test_shared_config.py` and
`test_conditional_mock_specs.py` cover these boundaries. The existing
`test_config_call_stays_unchanged_when_asserted_origin_is_unresolved` now checks
executable-code preservation rather than byte equality, because shared URL
ownership requires an additional FIXME and report item.

Deployment reads open nonblocking where supported, validate the opened descriptor
as a regular file, and enforce the byte cap on that same descriptor. A path swapped
to a FIFO cannot block discovery. Deployment-directory classification uses the
checkout-relative path, including the oversized-input review gate.

Unresolved include aliases alone do not establish Compose provenance. Literal
include paths and Compose include fields do; filename, explicit selection, or a
root `services` mapping independently preserve review for unresolved aliases.
Root flow mappings remain recognizable after comments, blank lines and document
markers; quoted root block keys receive the same loader checks.
`test_compose_provenance.py` and `test_file_boundaries.py` cover
these discovery boundaries, including the FIFO replacement race.

## Public-surface snapshot

`upgrade/api-surface/daytona-upgrade.api.txt` lists what scripts depend on, for
both tools: every field of the `--json` report with its type and whether it is
always present, each vocabulary's listed values (read from the schema's
`examples` or `enum`), and the flags read from both argument parsers (this
package's argparse parser and the npm tool's `parseArgs` options). Regenerate
after changing any of them and commit the result; the Upgrade tool CI runs the
check, and the `Breaking changes` workflow compares the file with the last
stable release:

```bash
uv run python ../scripts/api_surface.py            # regenerate
uv run python ../scripts/api_surface.py --check    # exit 1 when stale
```
