import { Daytona } from '@daytona/sdk';
const d = new Daytona();
const box = await d.v0.create();
await box.fs.downloadFile('/file');
await box.delete();
