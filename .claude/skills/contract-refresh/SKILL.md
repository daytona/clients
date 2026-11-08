---
name: contract-refresh
description: >
  Applies an updated set of contract/*.json files to the SDKs and the CLI:
  regenerates the vendored wire types, finds what the new contract breaks or
  adds, adapts both SDKs (parity) and the CLI, and adds the changelog
  fragments. Use when a PR updates files under contract/, when the user says
  "refresh the contract", "apply the new spec", or when a drift test or the
  Contract types check fails.
---

# Contract refresh

The SDKs and the CLI are built against the committed contract under
`contract/` (see `AGENTS.md`, "The contract artifacts"). Maintainers supply
new contract files; this skill turns them into a working SDK change.

## Inputs

- The updated `contract/*.json` files, already in the working tree. If they
  are not, stop and ask for them. Never hand-edit a contract file to make a
  test pass, and never invent surface the contract does not describe.

## Steps

1. **See what changed** since the PR's target branch (default `origin/main`),
   committed or not: the contract files before the refresh are the ones at
   the merge base. For the two OpenAPI files, list the paths and schemas
   added, removed and changed (a changed one has a new definition: read its
   diff for field and enum updates):

   ```bash
   base=$(git merge-base HEAD origin/main)
   git diff --stat "$base" -- contract/
   python3 .claude/skills/contract-refresh/scripts/contract_diff.py origin/main
   git diff "$base" -- contract/api-openapi.json   # the detail of a changed entry
   ```

   After changing the script, run
   `python3 .claude/skills/contract-refresh/scripts/contract_diff_test.py`.

2. **Regenerate the vendored types:** `scripts/gen-contract-types.sh`
   (needs node, a JRE and Go).

3. **Let the tools list the work.** Run every command, even after one
   fails, and collect every failure:

   ```bash
   (cd sdk-typescript || exit; rc=0; npm run typecheck || rc=1; npm test || rc=1; exit $rc)
   (cd sdk-python || exit; rc=0; .venv/bin/python -m mypy || rc=1; .venv/bin/python -m pytest -q || rc=1; exit $rc)
   (cd cli || exit; rc=0; go build ./... || rc=1; go test ./... || rc=1; exit $rc)
   ```

   Drift tests name what must change: a new error code (both `codes.py` and
   `codes.ts`), a renamed or removed route (the route-drift tests), a new
   enum value (the closed public unions plus their `'unknown'` handling).

4. **Adapt, in parity.** Every SDK-visible change lands in both SDKs in this
   PR with tests in both; the CLI follows when its commands use the changed
   surface. New surface does not have to be exposed — unexposed spec is fine.
   A removed or renamed field the SDKs already ship is a breaking change:
   flag it in the PR description and add a `changelog.d/<slug>.breaking.md`
   fragment.

5. **Changelog.** One fragment per user-visible change
   (`changelog.d/README.md`). A contract refresh that changes nothing a user
   can see needs none.

6. **Verify once:** the full `AGENTS.md` "Verification" block (regenerate
   the public-surface snapshots it lists when the SDK or CLI surface moved),
   then `scripts/gen-contract-types.sh --check` and the breaking-change check
   against this PR's base, so only the breaks this refresh adds count:

   ```bash
   git fetch origin main
   OASDIFF=$(scripts/breaking/install-oasdiff.sh) \
     python3 scripts/breaking/breaking_changes.py --fetch origin --github-repo daytona/clients \
       --base "$(git merge-base HEAD origin/main)"
   ```

## PR

- Title: `feat(sdk): …` / `fix(sdk): …` for the user-visible change, or
  `chore(contract): refresh` when nothing user-visible changed.
- Describe the change in terms of the SDK and CLI surface. Do not reference
  where the contract came from beyond "updated API contract".
- If the e2e suite fails because the deployment does not serve the new
  surface yet, keep the PR a draft until it does.
