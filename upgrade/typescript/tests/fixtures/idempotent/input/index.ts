import { Daytona } from '@daytona/sdk';
import type { Sandbox } from '@daytona/sdk/v0';
const d = new Daytona();
const s: Sandbox = await d.v0.create();
await s.delete();
// FIXME(daytona-upgrade): if `client` is a Daytona client, use `client.v0.create(...)` (SDK 2.x moved client methods to .v0)
client.create();
