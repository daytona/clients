import { Daytona as D } from '@daytona/sdk';
async function client(): Promise<D> { return new D(); }
const d = await client();
await d.v0.create();
async function f(other: D | undefined) {
  await other?.v0.get('id');
}
