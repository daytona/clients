// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

/** @type {import('jest').Config} */
module.exports = {
  displayName: 'sdk-typescript',
  preset: '../jest.preset.js',
  testEnvironment: 'node',
  transform: {
    '^.+\\.[tj]sx?$': [
      'ts-jest',
      {
        tsconfig: '<rootDir>/tsconfig.spec.json',
      },
    ],
  },
  moduleFileExtensions: ['ts', 'tsx', 'js', 'jsx'],
  roots: ['<rootDir>/src'],
  // The golden suite talks to a live sandbox and has its own `test:golden`
  // target; keep the default unit-test run hermetic.
  testPathIgnorePatterns: ['/node_modules/', '<rootDir>/src/__tests__/golden/'],
  moduleNameMapper: {
    '^@daytona/api-client$': '<rootDir>/../api-client/src/index.ts',
    '^@daytona/toolbox-api-client$': '<rootDir>/../toolbox-api-client/src/index.ts',
    '^@daytona/sdk$': '<rootDir>/src/index.ts',
  },
  coverageDirectory: '../coverage/sdk-typescript',
}
