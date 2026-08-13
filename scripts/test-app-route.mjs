import assert from 'node:assert/strict';
import test from 'node:test';

import { readAppRoute, routeHash } from '../frontend/src/app-route.js';

test('defaults to the publishing workspace', () => {
  assert.deepEqual(readAppRoute(''), { page: 'workspace', settingsTab: 'general' });
  assert.equal(routeHash('workspace'), '#/workspace');
});

test('normalizes retired task and split routes to the publishing workspace', () => {
  for (const hash of ['#/tasks', '#/split']) {
    const route = readAppRoute(hash);
    assert.deepEqual(route, { page: 'workspace', settingsTab: 'general' });
    assert.equal(routeHash(route.page, route.settingsTab), '#/workspace');
  }
});

test('preserves valid settings deep links and rejects unknown tabs', () => {
  assert.deepEqual(readAppRoute('#/settings/scheduler'), { page: 'settings', settingsTab: 'scheduler' });
  assert.equal(routeHash('settings', 'scheduler'), '#/settings/scheduler');
  assert.deepEqual(readAppRoute('#/settings/retired'), { page: 'settings', settingsTab: 'general' });
});
