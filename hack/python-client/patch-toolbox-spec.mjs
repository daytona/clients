#!/usr/bin/env node
// Python-only overlay for the vendored toolbox spec.
//
// NOTE: This is a temporary solution.
//
// Older runner daemons return `modifiedAt: null` (or omit it) in FileInfo
// responses, which the strict pydantic models reject. Until all runners
// are upgraded, the Python toolbox clients are generated from a patched copy
// of the spec where `modifiedAt` is not required (=> Optional[StrictStr]).
//
// The vendored openapi-specs/toolbox.json stays pristine; only the Python
// client generation consumes the patched copy. Other languages are untouched.
//
// Usage: node hack/python-client/patch-toolbox-spec.mjs <input-spec> <output-spec>

import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';

const [input, output] = process.argv.slice(2);
if (!input || !output) {
  console.error('Usage: patch-toolbox-spec.mjs <input-spec> <output-spec>');
  process.exit(1);
}

// schema -> properties to drop from its `required` list
const OPTIONAL_OVERRIDES = {
  FileInfo: ['modifiedAt'],
};

const spec = JSON.parse(readFileSync(input, 'utf8'));
const schemas = spec.definitions ?? spec.components?.schemas ?? {};

for (const [schemaName, props] of Object.entries(OPTIONAL_OVERRIDES)) {
  const schema = schemas[schemaName];
  if (!schema?.required) continue;
  const before = schema.required.length;
  schema.required = schema.required.filter((p) => !props.includes(p));
  if (schema.required.length !== before) {
    console.log(
      `patch-toolbox-spec: ${schemaName}: made optional -> ${props.join(', ')}`,
    );
  }
  if (schema.required.length === 0) delete schema.required;
}

mkdirSync(dirname(output), { recursive: true });
writeFileSync(output, JSON.stringify(spec, null, 2) + '\n');
