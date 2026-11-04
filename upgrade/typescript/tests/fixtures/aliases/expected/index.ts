import { Daytona as D } from '@daytona/sdk';
import { Sandbox as S } from '@daytona/sdk/v0';
const d = new D();
const s: S = await d.v0.get('id');
