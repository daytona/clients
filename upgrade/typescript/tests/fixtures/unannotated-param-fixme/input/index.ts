import { Daytona } from '@daytona/sdk';
async function f(client, sandbox, other) {
  await client.create();
  await sandbox.delete();
  await other.create();
}
