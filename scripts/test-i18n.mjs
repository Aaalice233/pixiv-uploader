import assert from 'node:assert/strict';
import test from 'node:test';

import {
  detectLocale,
  normalizeLocale,
  translate,
  validateCatalogs,
} from '../frontend/locales.js';

test('uses the Pixiv Uploader product name in every locale', () => {
  assert.equal(translate('zh-CN', 'app.name'), 'Pixiv Uploader');
  assert.equal(translate('en', 'app.name'), 'Pixiv Uploader');
});

test('normalizes supported locale variants', () => {
  assert.equal(normalizeLocale('zh_CN'), 'zh-CN');
  assert.equal(normalizeLocale('zh-Hans-CN'), 'zh-CN');
  assert.equal(normalizeLocale('en-US'), 'en');
  assert.equal(normalizeLocale('ja-JP'), null);
});

test('stored locale takes precedence over browser preference', () => {
  assert.equal(detectLocale('en', ['zh-CN']), 'en');
});

test('detects a supported browser locale and otherwise uses the default', () => {
  assert.equal(detectLocale(null, ['ja-JP', 'en-GB']), 'en');
  assert.equal(detectLocale(null, ['ja-JP']), 'zh-CN');
});

test('translates interpolation and plural variants', () => {
  assert.equal(translate('en', 'common.page', { current: 2, total: 5 }), '2 / 5');
  assert.equal(translate('en', 'common.images', { count: 1 }), '1 image');
  assert.equal(translate('en', 'common.images', { count: 3 }), '3 images');
  assert.equal(translate('en', 'common.images', { count: 1000 }), '1,000 images');
  assert.equal(translate('en', 'common.imageUnit', { count: 1 }), 'image');
  assert.equal(translate('en', 'common.imageUnit', { count: 3 }), 'images');
});

test('catalog validation detects placeholder drift', () => {
  const errors = validateCatalogs({
    'zh-CN': { greeting: '你好 {name}' },
    en: { greeting: 'Hello {person}' },
  });
  assert.ok(errors.some(message => message.includes('placeholders')));
});
