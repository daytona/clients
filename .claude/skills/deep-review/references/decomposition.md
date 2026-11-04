# Independent review areas

Split only when areas can produce independent evidence. A small diff needs
one reviewer, not a panel. Suggested boundaries:

| Change | Areas |
|---|---|
| SDK behavior | Request/error semantics in both SDKs; async/sync bridge and exports; docs/tests |
| CLI + SDK | CLI command/config/output; shared SDK behavior and contract; packaging/docs |
| Package or release | Python wheel/sdist; TypeScript ESM/CJS tarball and types; public workflow permissions/artifacts |
| Docs only | Usually one reviewer; verify every executable example and code claim |

Give every reviewer a shared context file with:

- Pinned head, base, merge base, read-only checkout, and diff command.
- Root `AGENTS.md`, component contributor docs, and this skill's references.
- Claims and unresolved cubic concerns assigned to that area.
- Exact owned files/concerns, neighboring callers, and required mirror pairs.
- A unique scratch directory and the gate commands the lead will run.
- The report format and requirement to save `REPORT.md` locally.

Hard rules: no source edits, no GitHub writes, no credentials in reports,
no building in a shared checkout, no speculative findings. Fake the network
boundary, not the behavior under review. Every finding must name a reachable
scenario, mechanism, head citations, consequence, probe output, and action.
Inspect both mirrors before calling parity broken.

Copy packages to separate scratch trees with their relative repository
layout intact: SDK drift tests read `contract/`, packaging uses `scripts/`,
and namespace bundling can require root metadata. Do not share `.venv`,
`node_modules`, generated output, or CLI profile directories across agents.
Use ephemeral ports and synthetic credentials. Run package-manager commands
only in the assigned writable copy. The lead runs gates in another copy.

The lead must not duplicate delegated searches while waiting. Collect
reports, open every load-bearing citation at the pinned head, verify driver
evidence, merge duplicate defects into one bullet naming both sides, and
record contrary evidence. State skipped or blocked areas explicitly.
