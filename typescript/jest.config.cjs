/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

/** @type {import('ts-jest').JestConfigWithTsJest} */
module.exports = {
  preset: 'ts-jest',
  testEnvironment: 'node',
  roots: ['<rootDir>/tests'],
  testMatch: ['**/*.test.ts'],
  setupFiles: ['<rootDir>/tests/jest.setup.ts', '<rootDir>/tests/setup-env.cjs'],
  transform: {
    '^.+\\.ts$': ['ts-jest', { tsconfig: '<rootDir>/tsconfig.json' }],
  },
  // Source uses explicit `.js` specifiers (required for native ESM output);
  // map them back to the `.ts` sources for ts-jest resolution.
  moduleNameMapper: {
    '^@daytona/sdk$': '<rootDir>/src/index.ts',
    '^@daytona/sdk/v0$': '<rootDir>/src/v0-unbundled.ts',
    '^@daytona/sdk/v2$': '<rootDir>/src/v2/index.ts',
    '^#v0-loader$': '<rootDir>/loaders/cjs/v0-node.cjs',
    '^(\\.{1,2}/.*)\\.js$': '$1',
  },
}
