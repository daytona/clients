import type { Daytona as D } from '@daytona/sdk';
import type { Sandbox as S } from '@daytona/sdk/v0';
import { type Image, DaytonaError } from '@daytona/sdk/v0';
export type { DaytonaConfig } from '@daytona/sdk';
export type { Sandbox as Box } from '@daytona/sdk/v0';
async function f(d: D | null | undefined, s: S) {
  await d?.v0.create();
  await s.delete();
}
