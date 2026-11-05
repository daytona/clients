#!/usr/bin/env python3
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Patch coverage of a diff: the share of added, coverable lines that tests run.

Merges the coverage reports the CI workflows produce (Cobertura XML for
Python, lcov for TypeScript, a Go coverprofile for the CLI), intersects them
with the added lines of `git diff <base>`, and compares the covered ratio
against PATCH_TARGET (60%).

A line added by the diff counts as *coverable* only if some report mentions
it, so comments, blank lines, docs and generated or test files never count
against you.

Generate the reports first (each from its package directory):

  (cd sdk-python && .venv/bin/python -m pytest -q --cov=daytona --cov-report=xml)
  (cd sdk-typescript && npx jest --coverage --coverageReporters=lcov)
  (cd cli && go test ./... -coverprofile=coverage.out)

Exit codes: 0 = target met, 1 = below target or a changed component's report
is missing, 2 = usage/environment error.
"""

import argparse
import fnmatch
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict

# (name, component dir, report path, format)
COMPONENTS = [
    ("python", "sdk-python", "sdk-python/coverage.xml", "cobertura"),
    ("typescript", "sdk-typescript", "sdk-typescript/coverage/lcov.info", "lcov"),
    ("cli", "cli", "cli/coverage.out", "gocover"),
]

# Changed lines under these never count: tests, examples, live e2e, entry
# points, and generated output.
IGNORE_GLOBS = [
    "**/tests/**", "**/*_test.go", "**/*.test.ts", "examples/**",
    "e2e/**", "**/main.go",
    "sdk-python/src/daytona/v2/_gen/**", "sdk-python/src/daytona/v2/_sync/**",
    "sdk-typescript/src/v2/gen/**", "cli/gen/**",
    "sdk-python/gen/**", "sdk-typescript/scripts/**", "scripts/**",
]

PATCH_TARGET = 60.0

# Extensions whose added lines we expect to see in a coverage report; a
# changed file with one of these but absent from every report gets a warning.
SOURCE_EXTS = {".go", ".py", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".mts", ".cts"}


def run(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True,
                          text=True).stdout


def ignored(path):
    for glob in IGNORE_GLOBS:
        # fnmatch's "*" crosses "/" so it handles "**" too;
        # additionally match "dir/**" against files directly under "dir".
        if fnmatch.fnmatch(path, glob) or fnmatch.fnmatch("/" + path, "*/" + glob):
            return True
    return False


def resolve_path(raw, comp_dir, repo_root):
    """Map a report path (absolute, repo-relative, or component-relative)
    to a repo-relative path."""
    candidates = []
    if os.path.isabs(raw):
        candidates.append(os.path.relpath(raw, repo_root))
    else:
        candidates.append(os.path.join(comp_dir, raw))
        candidates.append(raw)
    for cand in candidates:
        cand = os.path.normpath(cand)
        if not cand.startswith("..") and os.path.exists(
                os.path.join(repo_root, cand)):
            return cand
    return None


def parse_lcov(report, comp_dir, repo_root, cov):
    current = None
    with open(report, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if line.startswith("SF:"):
                current = resolve_path(line[3:], comp_dir, repo_root)
            elif line.startswith("DA:") and current:
                num, hits = line[3:].split(",")[:2]
                lineno = int(num)
                cov[current][lineno] = max(cov[current][lineno], int(float(hits)))
            elif line == "end_of_record":
                current = None


def go_module(comp_dir, repo_root):
    with open(os.path.join(repo_root, comp_dir, "go.mod"), encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("module "):
                return line.split()[1].strip()
    raise RuntimeError(f"no module line in {comp_dir}/go.mod")


def parse_gocover(report, comp_dir, repo_root, cov):
    module = go_module(comp_dir, repo_root)
    pat = re.compile(r"^(.+):(\d+)\.\d+,(\d+)\.\d+ (\d+) (\d+)$")
    with open(report, encoding="utf-8") as fh:
        for line in fh:
            m = pat.match(line.strip())
            if not m:
                continue  # "mode:" header
            name, start, end, _stmts, count = m.groups()
            if name.startswith(module + "/"):
                path = os.path.join(comp_dir, name[len(module) + 1:])
            else:
                path = resolve_path(name, comp_dir, repo_root)
                if path is None:
                    continue
            path = os.path.normpath(path)
            for lineno in range(int(start), int(end) + 1):
                cov[path][lineno] = max(cov[path][lineno], int(count))


def parse_cobertura(report, comp_dir, repo_root, cov):
    tree = ET.parse(report)
    root = tree.getroot()
    sources = [s.text for s in root.findall("./sources/source") if s.text]
    for cls in root.iter("class"):
        filename = cls.get("filename")
        if not filename:
            continue
        path = None
        for src in sources:
            path = resolve_path(os.path.join(src, filename), comp_dir, repo_root)
            if path:
                break
        if path is None:
            path = resolve_path(filename, comp_dir, repo_root)
        if path is None:
            continue
        for ln in cls.iter("line"):
            lineno = int(ln.get("number"))
            hits = int(float(ln.get("hits", "0")))
            cov[path][lineno] = max(cov[path][lineno], hits)


PARSERS = {"lcov": parse_lcov, "gocover": parse_gocover, "cobertura": parse_cobertura}


def added_lines(base, repo_root):
    """{repo-relative path: set of added line numbers} for base..working tree."""
    out = run(["git", "diff", "-U0", "--no-color", base, "--"], repo_root)
    added = defaultdict(set)
    current = None
    for line in out.splitlines():
        if line.startswith("+++ "):
            target = line[4:].split("\t")[0]
            current = None if target == "/dev/null" else target[2:]
        elif line.startswith("@@") and current:
            m = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
            start = int(m.group(1))
            count = int(m.group(2)) if m.group(2) is not None else 1
            added[current].update(range(start, start + count))
    return added


def component_of(path):
    best = None
    for flag, comp_dir, report, fmt in COMPONENTS:
        prefix = comp_dir + "/"
        if path.startswith(prefix) and (best is None or len(comp_dir) > len(best[1])):
            best = (flag, comp_dir, report, fmt)
    return best


def as_ranges(nums):
    nums = sorted(nums)
    out, start, prev = [], nums[0], nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        out.append((start, prev))
        start = prev = n
    out.append((start, prev))
    return ", ".join(f"{a}" if a == b else f"{a}-{b}" for a, b in out)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", help="base commit (default: merge-base of HEAD "
                                   "and origin/main)")
    ap.add_argument("--allow-missing", action="store_true",
                    help="warn instead of fail when a changed component has "
                         "no coverage report on disk")
    args = ap.parse_args()

    repo_root = run(["git", "rev-parse", "--show-toplevel"], os.getcwd()).strip()
    base = args.base or run(["git", "merge-base", "origin/main", "HEAD"], repo_root).strip()

    diff = added_lines(base, repo_root)
    untracked = run(["git", "ls-files", "--others", "--exclude-standard"],
                    repo_root).splitlines()
    relevant_untracked = [
        p for p in untracked
        if os.path.splitext(p)[1] in SOURCE_EXTS
        and not ignored(p) and component_of(p)]

    # Which components does the diff touch, and which reports exist?
    touched = {}   # component name -> component tuple
    skipped = []   # ignored paths (informational)
    outside = []   # source files outside every component
    for path in list(diff):
        if ignored(path):
            skipped.append(path)
            del diff[path]
            continue
        comp = component_of(path)
        if comp is None:
            if os.path.splitext(path)[1] in SOURCE_EXTS:
                outside.append(path)
            del diff[path]
            continue
        touched[comp[0]] = comp

    missing = [c for c in touched.values()
               if not os.path.exists(os.path.join(repo_root, c[2]))]

    cov = defaultdict(lambda: defaultdict(int))
    for flag, comp_dir, report, fmt in touched.values():
        report_abs = os.path.join(repo_root, report)
        if os.path.exists(report_abs):
            try:
                PARSERS[fmt](report_abs, comp_dir, repo_root, cov)
            except (OSError, ET.ParseError, ValueError, TypeError,
                    RuntimeError) as e:
                # A truncated/malformed report is an environment problem, not
                # a coverage verdict — regenerate it rather than "failing" the
                # patch target.
                print(f"error: cannot parse {report} ({e}); re-run the {flag} "
                      f"coverage command and try again", file=sys.stderr)
                return 2

    total_coverable = total_covered = 0
    per_file = []  # (path, coverable, covered, uncovered line numbers)
    unreported = []
    for path, lines in sorted(diff.items()):
        file_cov = cov.get(path)
        if not file_cov:
            if (os.path.splitext(path)[1] in SOURCE_EXTS
                    and component_of(path)
                    and component_of(path)[0] not in [c[0] for c in missing]):
                unreported.append(path)
            continue
        coverable = [n for n in lines if n in file_cov]
        covered = [n for n in coverable if file_cov[n] > 0]
        total_coverable += len(coverable)
        total_covered += len(covered)
        if coverable:
            per_file.append((path, len(coverable), len(covered),
                             sorted(set(coverable) - set(covered))))

    print(f"base: {base}")
    print(f"components touched: "
          f"{', '.join(sorted(touched)) if touched else '(none)'}")
    if skipped:
        print(f"ignored (generated/test/example): {len(skipped)} file(s)")
    if outside:
        print("changed source files outside every component (not counted):")
        for p in outside:
            print(f"  - {p}")
    if relevant_untracked:
        print("WARNING: untracked source files invisible to `git diff` — "
              "commit or `git add -N` them and re-run:")
        for p in relevant_untracked:
            print(f"  - {p}")
    for c in missing:
        print(f"MISSING REPORT: {c[2]} — run the {c[0]} coverage command first")
    for p in unreported:
        print(f"WARNING: {p} changed but appears in no coverage report "
              f"(never compiled/imported by tests?) — it does not count, "
              f"but new dead code is worth a look")

    print()
    if total_coverable == 0:
        print("patch: no coverable lines in the diff")
    else:
        pct = 100.0 * total_covered / total_coverable
        verdict = "PASS" if pct >= PATCH_TARGET else "FAIL"
        print(f"patch coverage: {total_covered}/{total_coverable} added "
              f"lines covered = {pct:.2f}%  (target {PATCH_TARGET:g}%)  "
              f"→ {verdict}")
        for path, coverable, covered, uncovered in per_file:
            marker = "ok " if covered == coverable else "MISS"
            print(f"  [{marker}] {path}: {covered}/{coverable}")
            if uncovered:
                print(f"         uncovered: {as_ranges(uncovered)}")

    if missing and not args.allow_missing:
        return 1
    if total_coverable and (100.0 * total_covered / total_coverable) < PATCH_TARGET:
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as e:
        print(f"error: {' '.join(e.cmd)} failed: {e.stderr.strip()}",
              file=sys.stderr)
        sys.exit(2)
