export const ITEM_STATUS_META = Object.freeze({
  queued: { tone: 'queued', icon: '○', terminal: false },
  running: { tone: 'running', icon: '●', terminal: false },
  succeeded: { tone: 'succeeded', icon: '✓', terminal: true },
  partial: { tone: 'partial', icon: '◐', terminal: true },
  failed: { tone: 'failed', icon: '×', terminal: true },
  uncertain: { tone: 'uncertain', icon: '!', terminal: true },
  canceled: { tone: 'canceled', icon: '–', terminal: true },
  unprocessed: { tone: 'unprocessed', icon: '○', terminal: true },
});

export const TARGET_STATUS_META = Object.freeze({
  queued: { tone: 'idle', labelKey: 'task.targetStatus.queued' },
  pending: { tone: 'idle', labelKey: 'task.targetStatus.pending' },
  running: { tone: 'running', labelKey: 'task.targetStatus.running' },
  success: { tone: 'success', labelKey: 'task.targetStatus.success' },
  failed: { tone: 'danger', labelKey: 'task.targetStatus.failed' },
  canceled: { tone: 'muted', labelKey: 'task.targetStatus.canceled' },
  maybe_posted: { tone: 'warning', labelKey: 'task.targetStatus.maybePosted' },
  skipped_already_done: { tone: 'success', labelKey: 'task.targetStatus.alreadyDone' },
  skipped_civitai_safety: { tone: 'muted', labelKey: 'task.targetStatus.safetySkipped' },
  dry_run: { tone: 'success', labelKey: 'task.targetStatus.dryRun' },
});

const RETRYABLE_STATUSES = new Set(['failed', 'partial', 'canceled', 'unprocessed']);
const FAILED_STATUSES = new Set(['failed', 'partial', 'uncertain']);
const SAFE_NAME_PATTERN = /^[^/\\\0]+$/;

function boundedInteger(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.max(0, Math.trunc(number)) : fallback;
}

function boundedFraction(value) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.max(0, Math.min(1, number)) : 0;
}

export function safeExternalUrl(value) {
  if (typeof value !== 'string' || !value.trim()) return '';
  try {
    const url = new URL(value);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) return '';
    url.search = '';
    url.hash = '';
    return url.href;
  } catch (_) {
    return '';
  }
}

export function normalizeTaskItems(task) {
  if (!Array.isArray(task?.items)) return null;
  return task.items.map((rawItem, offset) => {
    const item = rawItem && typeof rawItem === 'object' ? rawItem : {};
    const status = ITEM_STATUS_META[item.status] ? item.status : 'failed';
    const targets = item.targets && typeof item.targets === 'object' && !Array.isArray(item.targets)
      ? Object.entries(item.targets).reduce((result, [platform, rawTarget]) => {
        const detail = rawTarget && typeof rawTarget === 'object' ? rawTarget : {};
        const targetStatus = TARGET_STATUS_META[detail.status] ? detail.status : 'failed';
        const platformId = String(platform).toLowerCase();
        if (!/^[a-z0-9][a-z0-9_-]{0,31}$/.test(platformId)) return result;
        result[platformId] = {
          status: targetStatus,
          postUrl: ['success', 'skipped_already_done'].includes(targetStatus) ? safeExternalUrl(detail.post_url) : '',
          errorCode: typeof detail.error_code === 'string' ? detail.error_code : '',
        };
        return result;
      }, {})
      : {};
    return {
      index: boundedInteger(item.index, offset + 1) || offset + 1,
      name: typeof item.name === 'string' && item.name ? item.name : '—',
      status,
      stage: typeof item.stage === 'string' ? item.stage : status,
      stageProgress: boundedFraction(item.stage_progress),
      retryable: Boolean(item.retryable) && RETRYABLE_STATUSES.has(status),
      reasonCode: typeof item.reason_code === 'string' ? item.reason_code : '',
      targets,
    };
  }).sort((left, right) => left.index - right.index);
}

export function taskSummary(task, normalizedItems = normalizeTaskItems(task)) {
  if (normalizedItems?.length) {
    const succeeded = normalizedItems.filter(item => item.status === 'succeeded').length;
    const canceled = normalizedItems.filter(item => item.status === 'canceled').length;
    const failed = normalizedItems.filter(item => FAILED_STATUSES.has(item.status)).length;
    return {
      total: normalizedItems.length,
      processed: succeeded + failed + canceled,
      succeeded,
      failed,
      canceled,
    };
  }
  const total = boundedInteger(task?.total || task?.params?.files?.length);
  return {
    total,
    processed: Math.min(total || Number.MAX_SAFE_INTEGER, boundedInteger(task?.current)),
    succeeded: boundedInteger(task?.succeeded),
    failed: boundedInteger(task?.failed),
    canceled: boundedInteger(task?.canceled),
  };
}

export function retryableTaskFiles(task, normalizedItems = normalizeTaskItems(task)) {
  if (!normalizedItems) return [];
  const seen = new Set();
  return normalizedItems.reduce((files, item) => {
    const key = item.name.toLowerCase();
    if (item.retryable && RETRYABLE_STATUSES.has(item.status) && SAFE_NAME_PATTERN.test(item.name) && !seen.has(key)) {
      seen.add(key);
      files.push(item.name);
    }
    return files;
  }, []);
}

export function taskViewModel(task) {
  const items = normalizeTaskItems(task);
  const summary = taskSummary(task, items);
  const retryableFiles = retryableTaskFiles(task, items);
  const activeItem = items?.find(item => item.status === 'running')
    || items?.find(item => item.index === boundedInteger(task?.item_index))
    || null;
  return {
    items,
    summary,
    retryableFiles,
    activeItem,
    hasStructuredItems: items !== null && ([2, 3].includes(Number(task?.cmd)) || items.length > 0),
    canExpand: Boolean(items && items.length > 1),
  };
}
