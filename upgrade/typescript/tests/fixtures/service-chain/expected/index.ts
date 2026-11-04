import { Daytona } from '@daytona/sdk';
const d = new Daytona();
await d.v0.snapshot.list();
await d.v0.volume.get('id');
await new Daytona().v0.secret.list();
