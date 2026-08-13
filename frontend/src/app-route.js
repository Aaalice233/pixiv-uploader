export const APP_PAGE_IDS = Object.freeze(['workspace', 'logs', 'settings']);
export const SETTINGS_TAB_IDS = Object.freeze(['general', 'pixiv', 'llm', 'scheduler', 'system']);

const APP_PAGES = new Set(APP_PAGE_IDS);
const SETTINGS_TABS = new Set(SETTINGS_TAB_IDS);

export function readAppRoute(hash = '') {
  const [candidatePage = '', candidateTab = ''] = hash.replace(/^#\/?/, '').split('/');
  const page = APP_PAGES.has(candidatePage) ? candidatePage : 'workspace';
  const settingsTab = page === 'settings' && SETTINGS_TABS.has(candidateTab) ? candidateTab : 'general';
  return { page, settingsTab };
}

export function routeHash(page, settingsTab = 'general') {
  return page === 'settings' ? `#/settings/${settingsTab}` : `#/${page}`;
}
