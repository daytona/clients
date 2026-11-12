/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { defineConfig } from "oxlint";

export default defineConfig({
  // Build output and the publish-time v0 bundle are not handwritten sources.
  ignorePatterns: ["dist/**", "src/v0/**", "lint/**"],
  jsPlugins: [
    {
      name: "anti-slop",
      specifier: "./lint/anti-slop/index.ts",
    },
  ],
  // This workflow gates only the explicitly configured anti-slop rules.
  categories: { correctness: "off" },
  rules: {
    "anti-slop/no-chained-type-assertions": "warn",
    "anti-slop/no-conditional-empty-object-spread": "warn",
    "anti-slop/no-known-value-widening": "warn",
    "anti-slop/no-module-mocking": "error",
    "anti-slop/no-object-parameters": "error",
    "anti-slop/no-reflect-apply": "error",
    "anti-slop/no-reflect-get": "warn",
    "anti-slop/no-runtime-typeof": "warn",
    "anti-slop/no-shape-in-symbol-names": "error",
    "anti-slop/no-unknown-parameters": "warn",
    "anti-slop/no-unknown-returns": "warn",
    "anti-slop/no-unknown-type-aliases": "error",
    "anti-slop/no-unsafe-dictionary-type": "warn",
    "anti-slop/no-widen-then-assert": "error",
    "anti-slop/require-safety-comment-for-type-assertion": "warn",
  },
  overrides: [
    {
      files: ["tests/**"],
      rules: {
        // Tests deliberately construct invalid inputs with chained assertions.
        "anti-slop/no-chained-type-assertions": "off",
        // Tests deliberately widen invalid inputs before assertions.
        "anti-slop/no-widen-then-assert": "off",
        // Test-only assertions do not represent production invariants.
        "anti-slop/require-safety-comment-for-type-assertion": "off",
      },
    },
    {
      files: ["src/v2/ComputerUseTypes.ts"],
      rules: {
        // These wire types deliberately accept open-vocabulary fields.
        "anti-slop/no-unsafe-dictionary-type": "off",
      },
    },
  ],
});
