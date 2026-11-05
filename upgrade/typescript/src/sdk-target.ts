/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { readFileSync } from 'node:fs';
import { gte, minVersion, parse, validRange } from 'semver';
import type { Mapping } from './model.js';
import { UpgradeError } from './model.js';

export function version(): string {
  const manifest: unknown = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'));
  if (typeof manifest !== 'object' || manifest === null || !('version' in manifest) || typeof manifest.version !== 'string') throw new UpgradeError('Package version is missing');
  return manifest.version;
}

/**
 * The SDK range this tool writes. A published tool has the version of the SDK
 * release it was built and tested with in the same publish run, so it targets
 * exactly that release: a pre-release pins it (pre-releases may break), a
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

export function withTarget(mapping: Mapping, toolVersion: string): Mapping {
  return { ...mapping, packages: { ts: { ...mapping.packages.ts, range: targetRange(toolVersion, mapping.packages.ts.range) } } };
}

/**
 * A `@daytona/sdk` range already at or above the target in the target's major
 * (`2.0.0-alpha.9` for a `2.0.0-alpha.8` tool, `^2.5.0` for `^2.3.1`): left as
 * it is, so an older tool release never downgrades a project.
 */
export function atOrAboveTarget(range: string, target: string): boolean {
  if (validRange(range) === null || validRange(target) === null) return false;
  const floor = minVersion(range);
  const wanted = minVersion(target);
  if (floor === null || wanted === null || floor.major !== wanted.major || !gte(floor, wanted)) return false;
  // While the target is a pre-release, a floor on that same release's stable
  // version (`^2.0.0` under 2.0.0-alpha.8, what pre-lockstep releases wrote)
  // installs nothing yet; any later stable floor is a project ahead of this tool.
  return !wanted.prerelease.length || floor.prerelease.length > 0 ||
    floor.major !== wanted.major || floor.minor !== wanted.minor || floor.patch !== wanted.patch;
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
