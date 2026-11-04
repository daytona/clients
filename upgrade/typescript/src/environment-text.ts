/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { closeSync, constants, fstatSync, openSync, readSync, statSync } from 'node:fs';
import type { ReadFailures } from './read-failures.js';

export const MAX_ENVIRONMENT_BYTES = 5 * 1024 * 1024;

/** Bound scanning work; never open a pipe or device as a configuration file. */
export function environmentText(path: string, failures?: ReadFailures): {
  readonly text: string; readonly truncated: boolean;
} | undefined {
  const read = (): { readonly text: string; readonly truncated: boolean } | undefined => {
    if (!statSync(path).isFile()) return undefined;
    // Nonblocking open also protects against replacement with a FIFO after stat.
    const descriptor = openSync(path, constants.O_RDONLY | constants.O_NONBLOCK);
    try {
      const stat = fstatSync(descriptor);
      if (!stat.isFile()) return undefined;
      const bytes = Buffer.alloc(MAX_ENVIRONMENT_BYTES + 1);
      let length = 0;
      while (length < bytes.length) {
        const count = readSync(descriptor, bytes, length, bytes.length - length, null);
        if (count === 0) break;
        length += count;
      }
      const truncated = length > MAX_ENVIRONMENT_BYTES;
      const text = bytes.subarray(0, Math.min(length, MAX_ENVIRONMENT_BYTES)).toString('utf8');
      // A cut-off URL must not become a fabricated custom literal.
      return { text: truncated ? text.slice(0, Math.max(0, text.lastIndexOf('\n'))) : text, truncated };
    } finally { closeSync(descriptor); }
  };
  return failures ? failures.read(path, read) : read();
}
