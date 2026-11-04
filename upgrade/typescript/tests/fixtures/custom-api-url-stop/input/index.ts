import { Daytona } from '@daytonaio/sdk';
const d = new Daytona({ apiUrl: 'https://custom.example/api' });
const e = new Daytona({ serverUrl: process.env.URL });
await d.create();
