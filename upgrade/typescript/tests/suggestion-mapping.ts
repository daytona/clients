/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { loadMapping } from '../src/mapping.js';
import type { Mapping } from '../src/model.js';

export const suggestionMapping: Mapping = {
  ...loadMapping(), docs: { base: 'https://example.test/migrate', placeholder: false },
  dataTraps: [{ id: 'ids', text: 'Sandbox IDs do not carry over.' }],
  suggestions: [
    { id: 'client.create', receiver: 'client', v0: { ts: 'create' }, v2: { ts: '{client}.v2.create()' },
      category: 'differs', notes: { ts: ['Choose a v2 image.'] }, anchor: 'client-create', startsWorkflow: true },
    { id: 'client.list', receiver: 'client', v0: { ts: 'list' }, v2: { ts: '{client}.v2.list()' },
      category: 'direct', notes: { ts: [] }, anchor: 'client-list', startsWorkflow: false },
    { id: 'sandbox.delete', receiver: 'sandbox', v0: { ts: 'delete' }, v2: { ts: '{sandbox}.delete()' },
      category: 'direct', notes: { ts: [] }, anchor: 'sandbox-delete', startsWorkflow: false },
    { id: 'sandbox.fs.downloadFile', receiver: 'sandbox.fs', v0: { ts: 'downloadFile' }, v2: { ts: '{sandbox}.fs.downloadFile(...)' },
      category: 'direct', notes: { ts: [] }, anchor: 'fs-download', startsWorkflow: false },
    { id: 'sandbox.archive', receiver: 'sandbox', v0: { ts: 'archive' }, v2: { ts: null },
      category: 'none', notes: { ts: ['No matching operation.'] }, anchor: 'sandbox-archive', startsWorkflow: false },
  ],
};
