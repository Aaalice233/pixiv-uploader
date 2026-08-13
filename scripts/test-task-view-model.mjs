import assert from 'node:assert/strict';
import test from 'node:test';

import {
  normalizeTaskItems,
  retryableTaskFiles,
  safeExternalUrl,
  taskSummary,
  taskViewModel,
} from '../frontend/src/task-view-model.js';

const structuredTask = {
  cmd: 2,
  item_index: 2,
  items: [
    { index: 1, name: 'done.png', status: 'succeeded', retryable: true, targets: { pixiv: { status: 'success', post_url: 'https://www.pixiv.net/artworks/1' } } },
    { index: 2, name: 'partial.png', status: 'partial', retryable: true, reason_code: 'pixiv_upload_failed', targets: { civitai: { status: 'success', post_url: 'https://civitai.com/images/2' }, pixiv: { status: 'failed', error_code: 'pixiv_upload_failed' } } },
    { index: 3, name: 'uncertain.png', status: 'uncertain', retryable: true, targets: { pixiv: { status: 'maybe_posted', post_url: 'javascript:alert(1)' } } },
    { index: 4, name: 'later.png', status: 'unprocessed', retryable: true, targets: { pixiv: { status: 'queued' } } },
    null,
  ],
};

test('normalizes malformed item fields and safe target links', () => {
  const items = normalizeTaskItems(structuredTask);
  assert.equal(items.length, 5);
  assert.equal(items[0].targets.pixiv.postUrl, 'https://www.pixiv.net/artworks/1');
  assert.equal(items[2].retryable, false);
  assert.equal(items[2].targets.pixiv.postUrl, '');
  assert.equal(items[4].status, 'failed');
  assert.equal(safeExternalUrl('javascript:alert(1)'), '');
  assert.equal(safeExternalUrl('https://user:pass@example.com/work'), '');
  assert.equal(safeExternalUrl('https://example.com/work?token=secret#private'), 'https://example.com/work');
});

test('derives consistent batch counts from authoritative item states', () => {
  assert.deepEqual(taskSummary(structuredTask), {
    total: 5,
    processed: 4,
    succeeded: 1,
    failed: 3,
    canceled: 0,
  });
});

test('selects only safe retryable unsuccessful images in order', () => {
  assert.deepEqual(retryableTaskFiles(structuredTask), ['partial.png', 'later.png']);
  const model = taskViewModel(structuredTask);
  assert.equal(model.canExpand, true);
  assert.deepEqual(model.retryableFiles, ['partial.png', 'later.png']);
});

test('falls back cleanly for legacy v3 snapshots without items', () => {
  const legacy = { progress_version: 3, cmd: 3, total: 2, current: 1, succeeded: 1, failed: 0, canceled: 0 };
  const model = taskViewModel(legacy);
  assert.equal(model.hasStructuredItems, false);
  assert.equal(model.canExpand, false);
  assert.equal(model.summary.processed, 1);
});
