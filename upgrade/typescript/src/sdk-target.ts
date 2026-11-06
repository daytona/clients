/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { readFileSync } from 'node:fs';
import { gte, minVersion, parse, validRange } from 'semver';
import type { SemVer } from 'semver';
import type { Mapping } from './model.js';
import { UpgradeError } from './model.js';

export function version(): string {
  const manifest: unknown = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'));
  if (typeof manifest !== 'object' || manifest === null || !('version' in manifest) || typeof manifest.version !== 'string') throw new UpgradeError('Package version is missing');
  return manifest.version;
}

/**
 * The command that runs the Python tool of this release: both tools are
 * published at the SDK's version (uv accepts the npm spelling). An
 * unpublished build (0.x) names no release.
 */
export function companionCommand(toolVersion: string): string {
  return parse(toolVersion)?.major === 0 ? 'uvx daytona-upgrade' : `uvx daytona-upgrade@${toolVersion}`;
}

/**
 * The SDK range this tool writes. A published tool has the version of the SDK
 * release it is published with (upgrade-tool-ci checks its mapping against
 * that tree's SDKs), so it targets that release: a pre-release pins it (pre-releases may break), a
 * stable release takes it or any later release of the same major. An
 * unpublished build (version 0.x, the placeholder in git) keeps the mapping's
 * range.
 */
export function targetRange(toolVersion: string, fallback: string): string {
  const parsed = parse(toolVersion);
  if (parsed === null) throw new UpgradeError(`Invalid package version ${toolVersion}`);
  if (parsed.major === 0) return fallback;
  return parsed.prerelease.length ? parsed.version : `^${parsed.version}`;
}

/**
 * The exact version to write for a pre-release range at or above a pre-release
 * target (`^2.0.0-alpha.9` under 2.0.0-alpha.8 -> `2.0.0-alpha.9`): pinned like
 * the target, never below the range's own floor. Undefined otherwise.
 */
export function pinnedFloor(range: string, target: string): string | undefined {
  // An exclusive bound (`>2.0.0-alpha.8`) names no release to pin.
  if (validRange(range) === null || validRange(target) === null || exact(range)) return undefined;
  const floor = minVersion(range);
  // An exclusive bound on the floor's alternative names no release to pin.
  const owners = range.split('||').filter(alternative => minVersion(alternative)?.version === floor?.version);
  if (!owners.some(alternative => !/(?<![<>])>(?!=)/.test(alternative))) return undefined;
  const wanted = minVersion(target);
  if (floor === null || wanted === null || !wanted.prerelease.length || !floor.prerelease.length || staleForTarget(floor, wanted)) return undefined;
  return floor.major === wanted.major && gte(floor, wanted) ? floor.version : undefined;
}

export function withTarget(mapping: Mapping, toolVersion: string): Mapping {
  return { ...mapping, packages: { ts: { ...mapping.packages.ts, range: targetRange(toolVersion, mapping.packages.ts.range) } } };
}

/**
 * While the target is an alpha, the published `2.0.0-rc.1` is not ahead of it:
 * a stale pre-alpha build without the v0 namespace. Any other beta or rc is.
 */
function staleForTarget(floor: SemVer, wanted: SemVer): boolean {
  return wanted.prerelease[0] === 'alpha' && floor.version === '2.0.0-rc.1';
}

/** A range that admits exactly one version: `2.0.0`, `=2.0.0`, `v2.0.0`. */
function exact(range: string): boolean {
  const normalized = validRange(range);
  return normalized !== null && normalized === minVersion(range)?.version;
}

/**
 * A `@daytona/sdk` range already at or above the target in the target's major
 * (`2.0.0-alpha.9` for a `2.0.0-alpha.8` tool, `^2.5.0` for `^2.3.1`): left as
 * it is, so an older tool release never downgrades a project.
 */
export function atOrAboveTarget(range: string, target: string): boolean {
  if (validRange(range) === null || validRange(target) === null) return false;
  // A union is at or above the target only when every alternative is (a later
  // major counts: it is never downgraded either).
  const alternatives = range.split('||');
  if (alternatives.length > 1) return alternatives.every(alternative =>
    atOrAboveTarget(alternative.trim(), target) || aboveTargetMajor(alternative.trim(), target));
  // An exclusive bound at or above the target (`>2.0.0-alpha.8`) admits only
  // later releases: kept, never rewritten to a version it excludes.
  const exclusive = /(?<![<>])>(?!=)\s*v?(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)/.exec(range)?.[1];
  const wantedExact = minVersion(target);
  if (exclusive !== undefined && wantedExact !== null && parse(exclusive)?.major === wantedExact.major && gte(exclusive, wantedExact)) return true;
  const floor = minVersion(range);
  const wanted = minVersion(target);
  if (floor === null || wanted === null || floor.major !== wanted.major || !gte(floor, wanted) || staleForTarget(floor, wanted)) return false;
  // While the target is a pre-release, only an exact pre-release counts: a
  // pre-release range (`^2.0.0-alpha.8`) also admits the stale 2.0.0-rc.1.
  if (wanted.prerelease.length && floor.prerelease.length && !exact(range)) return false;
  // While the target is a pre-release, a range floored on that same release's
  // stable version (`^2.0.0` under 2.0.0-alpha.8, what pre-lockstep releases
  // wrote) installs nothing yet; an exact `2.0.0` pin or any later stable
  // floor is a project ahead of this tool.
  return !wanted.prerelease.length || floor.prerelease.length > 0 || exact(range) ||
    floor.minor !== wanted.minor || floor.patch !== wanted.patch;
}

/** A valid range whose floor is in the target's major (an earlier pre-release included). */
export function inTargetMajor(range: string, target: string): boolean {
  if (validRange(range) === null || validRange(target) === null) return false;
  const floor = minVersion(range);
  const wanted = minVersion(target);
  return floor !== null && wanted !== null && floor.major === wanted.major;
}

/** A valid range whose floor is in a later major than the target's. */
export function aboveTargetMajor(range: string, target: string): boolean {
  if (validRange(range) === null || validRange(target) === null) return false;
  const floor = minVersion(range);
  const wanted = minVersion(target);
  return floor !== null && wanted !== null && floor.major > wanted.major;
}
