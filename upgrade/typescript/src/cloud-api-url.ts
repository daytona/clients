/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { Mapping } from './model.js';

export function isCloudApiUrl(literal: string, mapping: Mapping): boolean {
  const normalize = (url: string): string => url.replace(/^(https?:\/\/[^/?#]+)/i, host => host.toLowerCase()).replace(/\/$/, '');
  return mapping.cloudApiUrls.some(url => normalize(url) === normalize(literal));
}
