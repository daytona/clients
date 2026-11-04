import { Daytona, type DaytonaConfig } from '@daytona/sdk';
const config: DaytonaConfig = { apiKey: 'key', target: 'us', requestTimeoutMs: 5, useDeprecatedPolling: true };
const d = new Daytona(config);
await d.v0.create();
