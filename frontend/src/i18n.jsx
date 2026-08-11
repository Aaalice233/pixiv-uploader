import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { DEFAULT_LOCALE, LOCALE_STORAGE_KEY, SUPPORTED_LOCALES, detectLocale, normalizeLocale, translate } from './locales.js';

const fallbackValue = {
  locale: DEFAULT_LOCALE,
  locales: SUPPORTED_LOCALES,
  setLocale: () => {},
  t: (key, variables) => translate(DEFAULT_LOCALE, key, variables),
  formatNumber: value => String(value ?? ''),
  formatDateTime: value => String(value || ''),
  formatTime: value => String(value || ''),
};

const I18nContext = createContext(fallbackValue);

function initialLocale() {
  if (typeof window === 'undefined') return DEFAULT_LOCALE;
  return detectLocale(window.localStorage.getItem(LOCALE_STORAGE_KEY), navigator.languages || [navigator.language]);
}

function safeDate(value) {
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function I18nProvider({ children }) {
  const [locale, updateLocale] = useState(initialLocale);
  const setLocale = useCallback(value => {
    const normalized = normalizeLocale(value);
    if (normalized) updateLocale(normalized);
  }, []);
  const t = useCallback((key, variables) => translate(locale, key, variables), [locale]);
  const formatNumber = useCallback((value, options = {}) => new Intl.NumberFormat(locale, options).format(Number(value)), [locale]);
  const formatDateTime = useCallback((value, options = {}) => {
    const date = safeDate(value);
    if (!date) return translate(locale, 'common.notAvailable');
    return new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short', ...options }).format(date);
  }, [locale]);
  const formatTime = useCallback((value, options = {}) => {
    const date = safeDate(value);
    if (!date) return translate(locale, 'common.notAvailable');
    return new Intl.DateTimeFormat(locale, { hour: '2-digit', minute: '2-digit', ...options }).format(date);
  }, [locale]);

  useEffect(() => {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, locale);
    document.documentElement.lang = locale;
    document.title = translate(locale, 'app.documentTitle');
  }, [locale]);

  const value = useMemo(() => ({ locale, locales: SUPPORTED_LOCALES, setLocale, t, formatNumber, formatDateTime, formatTime }), [locale, setLocale, t, formatNumber, formatDateTime, formatTime]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n() {
  return useContext(I18nContext);
}

export function requestLocale() {
  if (typeof document !== 'undefined') return normalizeLocale(document.documentElement.lang) || DEFAULT_LOCALE;
  return DEFAULT_LOCALE;
}

export function localizedError(error, t) {
  const code = error?.code || error?.payload?.error_code;
  const variables = error?.params || error?.payload?.error_params || {};
  if (code) {
    const key = `api.${code}`;
    const localized = t(key, variables);
    if (localized !== key) return localized;
  }
  return error?.message || t('common.unknownError');
}
