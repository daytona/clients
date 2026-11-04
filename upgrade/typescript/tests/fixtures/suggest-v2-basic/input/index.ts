import { Daytona } from '@daytona/sdk';
import type { Sandbox } from '@daytona/sdk/v0';
const d = new Daytona();
await d.v0.list();
async function f(box: Sandbox) {
  await box.fs.downloadFile('/file');
  await box.archive();
}
