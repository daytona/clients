import * as sdk from '@daytona/sdk';
import * as sdkV0_ from '@daytona/sdk/v0';
const sdkV0 = 'occupied';
const d = new sdk.Daytona();
const sandbox: sdkV0_.Sandbox = await d.v0.create();
const image = sdkV0_.Image;
function unrelated(sdk: { Sandbox: string }) { return sdk.Sandbox; }
