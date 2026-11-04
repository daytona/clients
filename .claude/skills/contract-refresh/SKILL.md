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

1. **See what changed.** `git diff --stat -- contract/` and, for the two
   OpenAPI files, the added/removed paths and schemas:

   ```bash
   for f in api toolbox; do
     git show HEAD:contract/$f-openapi.json > /tmp/$f-old.json
     python3 - "$f" <<'PY'
   import json, sys
   f = sys.argv[1]
   old = json.load(open(f"/tmp/{f}-old.json")); new = json.load(open(f"contract/{f}-openapi.json"))
   for k in ("paths",):
       print(f, k, "added:", sorted(set(new[k]) - set(old[k])), "removed:", sorted(set(old[k]) - set(new[k])))
   o, n = old["components"]["schemas"], new["components"]["schemas"]
   print(f, "schemas added:", sorted(set(n) - set(o)), "removed:", sorted(set(o) - set(n)))
   PY
   done
   ```

2. **Regenerate the vendored types:** `scripts/gen-contract-types.sh`
   (needs node, a JRE and Go).

3. **Let the tools list the work.** Run, and collect every failure:

   ```bash
   (cd typescript && npm run typecheck && npm test)
   (cd python && .venv/bin/python -m mypy && .venv/bin/python -m pytest -q)
   (cd cli && go build ./... && go test ./...)
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

6. **Verify once:** the full `AGENTS.md` "Verification" block, then
   `scripts/gen-contract-types.sh --check`.

## PR

- Title: `feat(sdk): …` / `fix(sdk): …` for the user-visible change, or
  `chore(contract): refresh` when nothing user-visible changed.
- Describe the change in terms of the SDK and CLI surface. Do not reference
  where the contract came from beyond "updated API contract".
- If the e2e suite fails because the deployment does not serve the new
  surface yet, keep the PR a draft until it does.
