import { readFile } from 'node:fs/promises';
import { messages, validateCatalogs } from '../frontend/locales.js';

const errors = validateCatalogs();
const source = await readFile(new URL('../frontend/flow-app.jsx', import.meta.url), 'utf8');

const cjkMatches = [...source.matchAll(/[\u3400-\u9fff]/g)];
if (cjkMatches.length) {
  const lines = [...new Set(cjkMatches.map(match => source.slice(0, match.index).split('\n').length))];
  errors.push(`frontend/flow-app.jsx contains untranslated CJK text on line(s): ${lines.join(', ')}`);
}

const staticKeys = [...source.matchAll(/\bt\(\s*['"]([^'"]+)['"]/g)].map(match => match[1]);
for (const key of new Set(staticKeys)) {
  if (!Object.prototype.hasOwnProperty.call(messages['zh-CN'], key)) errors.push(`flow-app.jsx uses unknown message key: ${key}`);
}

const generatedKeys = [
  ...['queued', 'running', 'done', 'failed', 'canceled', 'waiting_input'].map(value => `task.status.${value}`),
  ...[1, 2, 3, 4, 5, 6].map(value => `task.command.${value}`),
  ...['off', 'japan', 'strict'].map(value => `pixiv.preset.${value}`),
  ...['mosaic', 'blur', 'bar', 'heart'].map(value => `pixiv.method.${value}`),
  ...['bottom_right', 'bottom_left', 'top_right', 'top_left', 'center'].map(value => `pixiv.position.${value}`),
  ...['general', 'pixiv', 'llm', 'scheduler'].map(value => `settings.tab.${value}`),
  ...['random', 'oldest', 'latest', 'nameAsc', 'nameDesc'].map(value => `scheduler.order.${value}`),
];
for (const key of generatedKeys) {
  if (!Object.prototype.hasOwnProperty.call(messages['zh-CN'], key)) errors.push(`generated message key is missing: ${key}`);
}

for (const [key, value] of Object.entries(messages.en)) {
  const variants = typeof value === 'string' ? [value] : Object.values(value);
  if (variants.some(item => /[\u3400-\u9fff]/.test(item))) errors.push(`English message contains CJK text: ${key}`);
}

if (errors.length) {
  console.error(`i18n validation failed:\n- ${errors.join('\n- ')}`);
  process.exit(1);
}
console.log(`i18n validation passed (${Object.keys(messages['zh-CN']).length} keys across ${Object.keys(messages).length} locales)`);
