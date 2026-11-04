import { Daytona } from '@daytona/sdk';
const d = new Daytona();
// daytona-v2: workflow `box`: move these v0 calls on it to v2 together: fs.downloadFile, delete
// daytona-v2:   data: Sandbox IDs do not carry over.
// daytona-v2:   docs: https://example.test/migrate#data
// daytona-v2: d.v2.create()
// daytona-v2:   differs: Choose a v2 image.
// daytona-v2:   docs: https://example.test/migrate#client-create
const box = await d.v0.create();
// daytona-v2: box.fs.downloadFile(...)
// daytona-v2:   docs: https://example.test/migrate#fs-download
await box.fs.downloadFile('/file');
// daytona-v2: box.delete()
// daytona-v2:   docs: https://example.test/migrate#sandbox-delete
await box.delete();
