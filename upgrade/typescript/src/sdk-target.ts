/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { readFileSync } from 'node:fs';
import { parse } from 'semver';
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
