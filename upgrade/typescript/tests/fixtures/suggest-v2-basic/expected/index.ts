import { Daytona } from '@daytona/sdk';
import type { Sandbox } from '@daytona/sdk/v0';
const d = new Daytona();
// daytona-v2: d.v2.list()
// daytona-v2:   docs: https://example.test/migrate#client-list
await d.v0.list();
async function f(box: Sandbox) {
  // daytona-v2: box.fs.downloadFile(...)
  // daytona-v2:   docs: https://example.test/migrate#fs-download
  await box.fs.downloadFile('/file');
  // daytona-v2: No v2 equivalent
  // daytona-v2:   none: No matching operation.
  // daytona-v2:   docs: https://example.test/migrate#sandbox-archive
  await box.archive();
}
