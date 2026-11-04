/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { invoke, temporaryProject } from './helpers.js';

it.each(['js', 'mjs', 'cjs'].flatMap(extension => ['globalThis', 'global', 'window', 'self'].flatMap(name => [
  `${name}.processData.env.DAYTONA_API_URL = 'https://custom.example';`,
  `const url = ${name}.process.env.DAYTONA_API_URL;`,
  `${name}.process.env.UNRELATED = 'https://custom.example';`,
  `const ${name} = {process:{env:{}}}; ${name}.process.env.DAYTONA_API_URL = 'https://custom.example';`,
  `let ${name}; ${name}.process.env.DAYTONA_API_URL = 'https://custom.example';`,
  `var ${name}; ${name}.process.env.DAYTONA_API_URL = 'https://custom.example';`,
  `function f(${name}) { ${name}.process.env.DAYTONA_API_URL = 'https://custom.example'; }`,
  `import ${name} from './other.js'; ${name}.process.env.DAYTONA_API_URL = 'https://custom.example';`,
  `function ${name}() {} ${name}.process.env.DAYTONA_API_URL = 'https://custom.example';`,
].map(input => ({ extension, input })))))('ignores reads and local global lookalikes in $extension: $input', async ({ extension, input }) => {
  // Given
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}', [`boot.${extension}`]: input });
  // When
  const result = await invoke(root, ['--dry', '--json']);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).not.toContainEqual(expect.objectContaining({ kind: 'env-api-url' }));
  expect(JSON.parse(result.output).refused).toBeUndefined();
});
