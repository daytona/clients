import { Daytona } from '@daytona/sdk';
import { Sandbox } from '@daytona/sdk/v0';
const daytona = new Daytona();
const sandbox: Sandbox = await daytona.v0.create();
await sandbox.delete();
