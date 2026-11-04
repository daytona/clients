import { Daytona, Sandbox } from '@daytonaio/sdk';
const daytona = new Daytona();
const sandbox: Sandbox = await daytona.create();
await sandbox.delete();
