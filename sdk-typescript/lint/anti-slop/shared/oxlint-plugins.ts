/*
 * Copyright (c) 2026 Dillon Mulroy
 * SPDX-License-Identifier: MIT
 */

import { createRequire } from "node:module";

// Each independently installed package supplies the matching Oxlint plugin adapter.
const requireFromPackage = createRequire(`${process.cwd()}/package.json`);

export const { defineRule, eslintCompatPlugin } = requireFromPackage("@oxlint/plugins");
