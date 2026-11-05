# Evidence-based rebuttals

A reviewer reports defects; the author decides fixes. A declined finding
must explain which evidence defeats it, rather than silently disappearing.

| Reason | Evidence required |
|---|---|
| Deliberate language difference | Root `AGENTS.md` or an applicable `docs/adr/` decision plus a probe of both implementations |
| Contract-imposed limitation | The exact committed operation/schema and a driver demonstrating the documented behavior |
| Already fixed at this head | Current code citation and the original failing probe now passing |
| Pre-existing and unchanged | Base/head comparison proving no introduced regression and no false claim that this work fixes it |
| Probe or reviewer misread | Reachable caller/branch evidence and a corrected probe that contradicts the finding |
| Explicitly accepted scope | Public documented decision, affected callers, and an honest statement of the remaining limitation |

“Outdated,” “tests pass,” “only one SDK does it,” “cannot happen,” and “out of
scope” are not evidence. A pre-existing issue is still relevant when the
description claims it is fixed. An intentional limitation with inaccurate
README or changelog language remains a documentation finding. An unavailable
tool or credential makes a probe unverified, not rebutted. The skill never
posts a rebuttal, resolves a thread, or files an issue.
