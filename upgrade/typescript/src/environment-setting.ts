/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { environmentLiteral } from './env-api-url.js';

/** Classify a declared endpoint's fallback, never resolve loader paths this way. */
export function environmentSettingLiteral(text: string): string | undefined {
  const scalar = text.trim();
  const quoted = scalar.match(/^(['"])(.*?)\1\s*(?:#.*)?$/);
  const value = quoted?.[2] ?? scalar.replace(/\s+#.*$/, '').trim();
  const interpolation = value.match(/^\$\{[A-Za-z_]\w*(:?[?+\-])([^{}]+)\}$/);
  // `:?` / `?` text is Compose's error message for a missing variable, not a value.
  if (interpolation?.[1]?.endsWith('?')) return undefined;
  return environmentLiteral(interpolation?.[2] ?? text);
}

/** Only declaration keys (including Kubernetes name/value entries) are writes. */
export function environmentKeyPosition(prefix: string): boolean {
  return /^\s*(?:-\s*)?(?:export\s+)?['"]?$/.test(prefix) ||
    /(?:(?<!\$)\{|[\[,])\s*['"]?$/.test(prefix) || /\bname\s*:\s*['"]?$/.test(prefix);
}
