import { Daytona } from '@daytonaio/sdk';
function make() { return new Daytona(); }
const arbitrary = make();
const sandbox = await arbitrary.create();
await sandbox.delete();
function custom() { return { delete() {} }; }
const client = custom();
client.delete();
