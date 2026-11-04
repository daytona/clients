import { Daytona, type DaytonaConfig } from '@daytonaio/sdk';
const config: DaytonaConfig = { apiKey: 'key', target: 'us', requestTimeoutMs: 5, useDeprecatedPolling: true };
const d = new Daytona(config);
await d.create();
