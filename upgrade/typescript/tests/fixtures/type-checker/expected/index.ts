import { Daytona } from '@daytona/sdk';
function make() { return new Daytona(); }
const arbitrary = make();
const sandbox = await arbitrary.v0.create();
await sandbox.delete();
function custom() { return { delete() {} }; }
const client = custom();
client.delete();
