import * as sdk from '@daytonaio/sdk';
const sdkV0 = 'occupied';
const d = new sdk.Daytona();
const sandbox: sdk.Sandbox = await d.create();
const image = sdk.Image;
function unrelated(sdk: { Sandbox: string }) { return sdk.Sandbox; }
