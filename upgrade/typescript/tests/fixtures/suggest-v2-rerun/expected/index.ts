import { Daytona } from '@daytona/sdk';
const d = new Daytona();
// daytona-v2: d.v2.list()
// daytona-v2:   docs: https://example.test/migrate#client-list
await d.v0.list();
