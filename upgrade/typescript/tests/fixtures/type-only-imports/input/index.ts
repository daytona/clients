import type { Daytona as D, Sandbox as S } from '@daytona/sdk';
import { type Image, DaytonaError } from '@daytona/sdk';
export type { Sandbox as Box, DaytonaConfig } from '@daytonaio/sdk';
async function f(d: D | null | undefined, s: S) {
  await d?.create();
  await s.delete();
}
