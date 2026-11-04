const { Daytona: D } = require('@daytona/sdk');
const { Sandbox } = require('@daytona/sdk/v0');
const sdk = require('@daytona/sdk'), sdkV0 = require('@daytona/sdk/v0');
const d = new D();
d.v0.create();
console.log(sdkV0.Image);
