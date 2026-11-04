import { Daytona } from '@daytona/sdk';
const d = new Daytona();
await d.snapshot.list();
await d.volume.get('id');
await new Daytona().secret.list();
