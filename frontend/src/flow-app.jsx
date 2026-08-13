import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { I18nProvider, localizedError, requestLocale, useI18n } from './i18n.jsx';
import { APP_PAGE_IDS, SETTINGS_TAB_IDS, readAppRoute, routeHash } from './app-route.js';
import { ITEM_STATUS_META, TARGET_STATUS_META, taskViewModel, upsertTask } from './task-view-model.js';

const THEME_KEY = 'flow-theme-v2';
const IMAGE_EXTENSIONS = ['.png', '.jpg', '.jpeg', '.webp'];
const PLATFORM_META = {
  civitai: { label: 'Civitai', short: 'C', tone: 'blue' },
  pixiv: { label: 'Pixiv', short: 'P', tone: 'cyan' },
};
const SIDEBAR_ICONS = Object.freeze({ workspace: 'upload', logs: 'terminal', settings: 'settings' });
const SETTINGS_ICONS = Object.freeze({ general: 'settings', pixiv: 'shield', llm: 'wand', scheduler: 'clock', system: 'refresh' });
const SIDEBAR_PAGES = Object.freeze(APP_PAGE_IDS.map(id => [id, SIDEBAR_ICONS[id]]));
const SETTINGS_NAV = Object.freeze(SETTINGS_TAB_IDS.map(id => [id, SETTINGS_ICONS[id]]));
const ACTIVE_TASK_STATUSES = new Set(['queued', 'running', 'waiting_input']);
const MAINTENANCE_COMMANDS = new Set([4, 5]);

function isMaintenanceTask(task) {
  return task?.category === 'maintenance' || MAINTENANCE_COMMANDS.has(Number(task?.cmd));
}

class ApiError extends Error {
  constructor(message, { code = '', params = {}, payload = {}, status = 0 } = {}) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.params = params;
    this.payload = payload;
    this.status = status;
  }
}

async function api(url, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set('Accept-Language', requestLocale());
  let response;
  try { response = await fetch(url, { ...options, headers }); }
  catch (error) { throw new ApiError(error.message, { code: 'network_error' }); }
  const text = await response.text();
  let payload = {};
  if (text) {
    try { payload = JSON.parse(text); }
    catch (_) { payload = { error: text }; }
  }
  if (!response.ok) {
    throw new ApiError(payload.error || '', {
      code: payload.error_code || '',
      params: payload.error_params || {},
      payload,
      status: response.status,
    });
  }
  return payload;
}

function jsonOptions(method, body) {
  return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
}

function formatLogsForClipboard(logs) {
  return logs.map(entry => [entry.t, entry.src, entry.msg]
    .map(value => String(value ?? '').trim())
    .filter(Boolean)
    .join('\t'))
    .filter(Boolean)
    .join('\n');
}

async function writeClipboard(text) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return;
    }
  } catch (_) {
    // Fall through for browsers that expose the API but block it by policy.
  }
  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.readOnly = true;
  textarea.style.position = 'fixed';
  textarea.style.inset = '-9999px auto auto -9999px';
  document.body.appendChild(textarea);
  let copied = false;
  try {
    textarea.select();
    copied = document.execCommand('copy');
  } finally {
    textarea.remove();
  }
  if (!copied) throw new Error('Clipboard write failed');
}

function Icon({ name, size = 18 }) {
  const common = { width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.8, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true };
  const paths = {
    upload: <><path d="M12 16V4"/><path d="m7 9 5-5 5 5"/><path d="M4 15v4h16v-4"/></>,
    shield: <><path d="m12 3 8 3v6c0 5-3.5 8.5-8 9-4.5-.5-8-4-8-9V6l8-3Z"/><path d="m9 12 2 2 4-4"/></>,
    refresh: <><path d="M20 7v5h-5"/><path d="M4 17v-5h5"/><path d="M6.1 8A7 7 0 0 1 18.7 6L20 9"/><path d="M17.9 16A7 7 0 0 1 5.3 18L4 15"/></>,
    settings: <><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1a1.7 1.7 0 0 0 1.9.3A1.7 1.7 0 0 0 10 3v-.2h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z"/></>,
    folder: <path d="M3 6a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>,
    plus: <><path d="M12 5v14"/><path d="M5 12h14"/></>,
    x: <><path d="m6 6 12 12"/><path d="M18 6 6 18"/></>,
    check: <path d="m5 12 4 4L19 6"/>,
    play: <path d="m8 5 11 7-11 7Z"/>,
    pause: <><path d="M8 5v14"/><path d="M16 5v14"/></>,
    terminal: <><path d="m4 6 5 6-5 6"/><path d="M12 18h8"/></>,
    clock: <><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>,
    image: <><rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8" cy="8" r="1.5"/><path d="m4 17 5-5 4 4 2-2 5 5"/></>,
    chevron: <path d="m9 18 6-6-6-6"/>,
    moon: <path d="M20.5 14.2A8 8 0 0 1 9.8 3.5 9 9 0 1 0 20.5 14.2Z"/>,
    sun: <><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></>,
    trash: <><path d="M4 7h16"/><path d="M9 7V4h6v3"/><path d="m6 7 1 14h10l1-14"/></>,
    link: <><path d="M10 13a5 5 0 0 0 7.5.5l2-2a5 5 0 0 0-7-7l-1.1 1.1"/><path d="M14 11a5 5 0 0 0-7.5-.5l-2 2a5 5 0 0 0 7 7l1.1-1.1"/></>,
    wand: <><path d="m15 4 5 5L8 21H3v-5Z"/><path d="m13 6 5 5"/><path d="M5 3v4M3 5h4M19 16v4M17 18h4"/></>,
    queue: <><path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4" cy="6" r="1"/><circle cx="4" cy="12" r="1"/><circle cx="4" cy="18" r="1"/></>,
    activity: <path d="M3 12h4l2-7 4 14 2-7h6"/>,
    menu: <><path d="M4 7h16M4 12h16M4 17h16"/></>,
    alert: <><path d="M12 3 2.8 20h18.4Z"/><path d="M12 9v5M12 17h.01"/></>,
    logout: <><path d="M9 5H4v14h5"/><path d="M13 8l4 4-4 4M17 12H8"/></>,
    copy: <><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M15 9V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h3"/></>,
  };
  return <svg {...common}>{paths[name] || null}</svg>;
}

function Button({ children, icon, variant = 'secondary', className = '', type = 'button', ...props }) {
  return <button type={type} className={`flow-button ${variant} ${className}`} {...props}>{icon && <Icon name={icon} size={17}/>}<span>{children}</span></button>;
}

function IconButton({ icon, label, className = '', type = 'button', ...props }) {
  return <button type={type} className={`flow-icon-button ${className}`} aria-label={label} title={label} {...props}><Icon name={icon}/></button>;
}

function Toggle({ checked, onChange, label, description, disabled = false }) {
  return (
    <label className={`flow-toggle-row ${disabled ? 'disabled' : ''}`}>
      <span><strong>{label}</strong>{description && <small>{description}</small>}</span>
      <input type="checkbox" checked={checked} onChange={event => onChange(event.target.checked)} disabled={disabled}/>
      <i aria-hidden="true"/>
    </label>
  );
}

function Modal({ title, children, onClose, wide = false, footer, className = '' }) {
  const { t } = useI18n();
  const modalRef = useRef(null);
  const closeRef = useRef(onClose);
  useEffect(() => { closeRef.current = onClose; }, [onClose]);
  useEffect(() => {
    const previousFocus = document.activeElement;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const focusModal = () => {
      const target = modalRef.current?.querySelector('button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])');
      (target || modalRef.current)?.focus();
    };
    const frame = requestAnimationFrame(focusModal);
    const handleKey = event => {
      const layers = document.querySelectorAll('.flow-modal-layer');
      const topModal = layers[layers.length - 1]?.querySelector('.flow-modal');
      if (topModal !== modalRef.current) return;
      if (event.key === 'Escape') { event.preventDefault(); closeRef.current(); return; }
      if (event.key !== 'Tab') return;
      const focusable = [...modalRef.current.querySelectorAll('button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])')].filter(element => element.getClientRects().length);
      if (!focusable.length) { event.preventDefault(); modalRef.current.focus(); return; }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', handleKey);
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener('keydown', handleKey);
      document.body.style.overflow = previousOverflow;
      if (previousFocus instanceof HTMLElement && document.contains(previousFocus)) previousFocus.focus();
    };
  }, []);
  return (
    <div className="flow-modal-layer" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
      <section ref={modalRef} tabIndex={-1} className={`flow-modal ${wide ? 'wide' : ''} ${className}`} role="dialog" aria-modal="true" aria-label={title}>
        <header className="flow-modal-header"><h2>{title}</h2><IconButton icon="x" label={t('common.close')} onClick={onClose}/></header>
        <div className="flow-modal-body">{children}</div>
        {footer && <footer className="flow-modal-footer">{footer}</footer>}
      </section>
    </div>
  );
}

function ToastStack({ toasts, dismiss }) {
  const { t } = useI18n();
  return <div className="flow-toasts" aria-live="polite">{toasts.map(toast => (
    <div className={`flow-toast ${toast.type || 'info'}`} key={toast.id}>
      <Icon name={toast.type === 'error' ? 'alert' : 'check'} size={17}/><span>{toast.message}</span>
      <IconButton icon="x" label={t('common.close')} onClick={() => dismiss(toast.id)}/>
    </div>
  ))}</div>;
}

function PlatformBadge({ id, connected }) {
  const meta = PLATFORM_META[id];
  return <span className={`flow-platform-badge ${meta.tone}`}><b>{meta.short}</b><span>{meta.label}</span><i className={connected ? 'online' : ''}/></span>;
}

function PublishDialog({ images, status, defaults, llmConfig, onReloadImages, onClose, onRun, notify }) {
  const { locale, t } = useI18n();
  const defaultTargets = String(defaults.targets || 'civitai,pixiv').split(',').filter(id => PLATFORM_META[id]);
  const [targets, setTargets] = useState(() => new Set(defaultTargets.length ? defaultTargets : ['civitai', 'pixiv']));
  const [selected, setSelected] = useState(() => new Set(images.map(image => image.name)));
  const [knownNames, setKnownNames] = useState(() => new Set(images.map(image => image.name)));
  const [sort, setSort] = useState(defaults.sort || defaults.sort_mode || 'time_desc');
  const [manualOrder, setManualOrder] = useState(images.map(image => image.name));
  const [randomOrder, setRandomOrder] = useState(() => shuffled(images.map(image => image.name)));
  const [page, setPage] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [dragName, setDragName] = useState('');
  const [removingNames, setRemovingNames] = useState(() => new Set());
  const [saving, setSaving] = useState(false);
  const [llmEnabled, setLlmEnabled] = useState(Boolean(defaults.llm_reverse && llmConfig.enabled));
  const [persona, setPersona] = useState(defaults.llm_persona || llmConfig.default_persona_id || '');
  const [contentMode, setContentMode] = useState(defaults.llm_content_mode || 'sfw');
  const [pixivAiTag, setPixivAiTag] = useState((defaults.ai_tags_by_platform || {}).pixiv !== false);
  const fileInput = useRef(null);
  const pageSize = 30;

  useEffect(() => {
    const incoming = images.map(image => image.name);
    setManualOrder(previous => [...previous.filter(name => incoming.includes(name)), ...incoming.filter(name => !previous.includes(name))]);
    setSelected(previous => {
      const next = new Set([...previous].filter(name => incoming.includes(name)));
      incoming.forEach(name => { if (!knownNames.has(name)) next.add(name); });
      return next;
    });
    setKnownNames(new Set(incoming));
  }, [images]);

  useEffect(() => { setPage(0); }, [sort]);
  useEffect(() => {
    if (sort === 'random') setRandomOrder(shuffled(images.map(image => image.name)));
  }, [sort, images.length]);

  const imageByName = useMemo(() => new Map(images.map(image => [image.name, image])), [images]);
  const ordered = useMemo(() => {
    const list = [...images];
    if (sort === 'manual') return manualOrder.map(name => imageByName.get(name)).filter(Boolean);
    if (sort === 'random') return randomOrder.map(name => imageByName.get(name)).filter(Boolean);
    return list.sort((a, b) => {
      if (sort === 'name_asc') return a.name.localeCompare(b.name, locale);
      if (sort === 'name_desc') return b.name.localeCompare(a.name, locale);
      if (sort === 'time_asc') return a.mtime - b.mtime;
      return b.mtime - a.mtime;
    });
  }, [images, sort, manualOrder, randomOrder, imageByName, locale]);
  const pageCount = Math.max(1, Math.ceil(ordered.length / pageSize));
  useEffect(() => { setPage(value => Math.min(value, pageCount - 1)); }, [pageCount]);
  const visible = ordered.slice(page * pageSize, (page + 1) * pageSize);
  const selectedOrdered = ordered.filter(image => selected.has(image.name));

  async function addFiles(fileList) {
    const accepted = [...fileList].filter(file => IMAGE_EXTENSIONS.some(ext => file.name.toLowerCase().endsWith(ext)));
    if (!accepted.length) { notify(t('publish.unsupportedFiles'), 'error'); return; }
    const form = new FormData();
    accepted.forEach(file => form.append('files', file));
    setUploading(true);
    try {
      await api('/api/add-upload-files', { method: 'POST', body: form });
      await onReloadImages();
      notify(t('publish.filesAdded', { count: accepted.length }));
    } catch (error) { notify(localizedError(error, t), 'error'); }
    finally { setUploading(false); setDragOver(false); }
  }

  async function removeImages(fileNames) {
    if (removingNames.size) return;
    const names = [...new Set(fileNames)].filter(name => imageByName.has(name));
    if (!names.length) return;
    const confirmed = window.confirm(names.length === 1
      ? t('publish.deleteOneConfirm', { name: names[0] })
      : t('publish.deleteManyConfirm', { count: names.length }));
    if (!confirmed) return;
    setRemovingNames(new Set(names));
    try {
      await api('/api/images', jsonOptions('DELETE', { files: names }));
      notify(t('publish.filesRemoved', { count: names.length }));
    } catch (error) {
      notify(localizedError(error, t), 'error');
    } finally {
      await onReloadImages().catch(() => {});
      setRemovingNames(new Set());
    }
  }

  function toggleTarget(id) {
    setTargets(previous => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  function reorder(overName) {
    if (!dragName || dragName === overName) return;
    setManualOrder(previous => {
      const next = [...previous];
      const from = next.indexOf(dragName);
      const to = next.indexOf(overName);
      if (from < 0 || to < 0) return previous;
      next.splice(from, 1);
      next.splice(to, 0, dragName);
      return next;
    });
  }

  async function confirmPublish() {
    if (!targets.size) { notify(t('publish.platformRequired'), 'error'); return; }
    if (!selectedOrdered.length) { notify(t('publish.imageRequired'), 'error'); return; }
    setSaving(true);
    const targetList = [...targets];
    const payload = {
      targets: targetList.join(','),
      files: selectedOrdered.map(image => image.name),
      count: selectedOrdered.length,
      sort,
      llm_reverse: targets.has('pixiv') && llmEnabled,
      llm_persona: persona,
      llm_content_mode: contentMode,
      ai_tags_by_platform: { pixiv: pixivAiTag },
    };
    try {
      await api('/api/upload-defaults', jsonOptions('POST', {
        targets: payload.targets,
        sort,
        llm_reverse: payload.llm_reverse,
        llm_persona: persona,
        llm_content_mode: contentMode,
        ai_tags_by_platform: payload.ai_tags_by_platform,
      }));
      const command = targetList.length === 1 && targetList[0] === 'pixiv' ? 3 : 2;
      await onRun(command, payload);
      onClose();
    } catch (error) { notify(localizedError(error, t), 'error'); }
    finally { setSaving(false); }
  }

  const busy = saving || uploading || removingNames.size > 0;
  const closeDialog = () => { if (!busy) onClose(); };

  return (
    <Modal title={t('publish.title')} onClose={closeDialog} wide className="publish-modal" footer={<><span className="flow-footer-summary">{t('common.selectedImages', { count: selectedOrdered.length })} · {[...targets].map(id => PLATFORM_META[id].label).join(' + ') || t('common.noPlatform')}</span><Button onClick={onClose} disabled={busy}>{t('common.cancel')}</Button><Button variant="primary" icon="upload" disabled={busy || !selectedOrdered.length || !targets.size} onClick={confirmPublish}>{saving ? t('publish.creating') : t('publish.start')}</Button></>}>
      <div className="publish-layout">
        <div className="publish-library">
          <div className={`flow-dropzone ${dragOver ? 'active' : ''}`} role="button" tabIndex={busy ? -1 : 0} aria-busy={uploading} aria-disabled={busy} onKeyDown={event => { if (!busy && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); fileInput.current?.click(); } }} onDragEnter={event => { if (!busy) { event.preventDefault(); setDragOver(true); } }} onDragOver={event => { if (!busy) event.preventDefault(); }} onDragLeave={event => { if (!event.currentTarget.contains(event.relatedTarget)) setDragOver(false); }} onDrop={event => { event.preventDefault(); if (!busy) addFiles(event.dataTransfer.files); }} onClick={() => { if (!busy) fileInput.current?.click(); }}>
            <input ref={fileInput} type="file" accept="image/png,image/jpeg,image/webp" multiple hidden disabled={busy} onChange={event => { addFiles(event.target.files); event.target.value = ''; }}/>
            <Icon name="plus"/><span>{uploading ? t('publish.importing') : t('publish.dropHint')}</span>
          </div>
          <div className="publish-library-toolbar">
            <div className="flow-segmented" aria-label={t('publish.sortLabel')}>
              {[['time_desc','publish.sort.latest'],['name_asc','publish.sort.name'],['random','publish.sort.random'],['manual','publish.sort.manual']].map(([value, key]) => <button key={value} aria-pressed={sort === value} className={sort === value ? 'active' : ''} onClick={() => setSort(value)}>{t(key)}</button>)}
            </div>
            <div className="publish-library-actions">
              <button className="flow-text-button danger" disabled={!selected.size || removingNames.size > 0} onClick={() => removeImages([...selected])}><Icon name="trash" size={14}/><span>{removingNames.size ? t('publish.deleting') : t('publish.deleteSelected')}</span></button>
              <button className="flow-text-button" disabled={removingNames.size > 0} onClick={() => setSelected(selected.size === images.length ? new Set() : new Set(images.map(image => image.name)))}>{selected.size === images.length && images.length ? t('publish.clearSelection') : t('publish.selectAll')}</button>
            </div>
          </div>
          {images.length ? (
            <div className="publish-image-grid">
              {visible.map(image => {
                const checked = selected.has(image.name);
                const removing = removingNames.has(image.name);
                return <article key={image.name} draggable={sort === 'manual' && !removing} onDragStart={() => setDragName(image.name)} onDragOver={event => { if (sort === 'manual' && !removing) { event.preventDefault(); reorder(image.name); } }} onDragEnd={() => setDragName('')} className={`publish-thumb ${checked ? 'selected' : ''} ${dragName === image.name ? 'dragging' : ''} ${removing ? 'removing' : ''}`} title={image.name}>
                  <button className="publish-thumb-select" aria-label={`${t(checked ? 'publish.deselectImage' : 'publish.selectImage')}: ${image.name}`} aria-pressed={checked} disabled={removing} onClick={() => setSelected(previous => { const next = new Set(previous); if (next.has(image.name)) next.delete(image.name); else next.add(image.name); return next; })}>
                    <img src={`/upload/${encodeURIComponent(image.name)}`} alt="" loading="lazy"/>
                    <span className="publish-thumb-check"><Icon name="check" size={13}/></span>
                    <small>{image.name}</small>
                  </button>
                  <button className="publish-thumb-delete" aria-label={`${t('publish.deleteImage')}: ${image.name}`} title={t('publish.deleteImage')} disabled={removingNames.size > 0} onClick={() => removeImages([image.name])}><Icon name="trash" size={14}/></button>
                </article>;
              })}
            </div>
          ) : <div className="publish-empty"><Icon name="image" size={28}/><strong>{t('publish.emptyTitle')}</strong><span>{t('publish.emptyHint')}</span></div>}
          {pageCount > 1 && <div className="publish-pagination"><button disabled={page === 0} onClick={() => setPage(value => value - 1)}>{t('publish.previous')}</button><span>{t('common.page', { current: page + 1, total: pageCount })}</span><button disabled={page >= pageCount - 1} onClick={() => setPage(value => value + 1)}>{t('publish.next')}</button></div>}
        </div>
        <aside className="publish-options">
          <section><h3>{t('publish.destinations')}</h3><div className="publish-platforms">{Object.keys(PLATFORM_META).map(id => <button key={id} aria-pressed={targets.has(id)} className={targets.has(id) ? 'selected' : ''} onClick={() => toggleTarget(id)}><span className={`platform-mark ${PLATFORM_META[id].tone}`}>{PLATFORM_META[id].short}</span><span><strong>{PLATFORM_META[id].label}</strong><small>{status[`${id}_logged_in`] ? t('publish.profileReady') : t('publish.loginRequired')}</small></span><i>{targets.has(id) && <Icon name="check" size={14}/>}</i></button>)}</div></section>
          {targets.has('pixiv') && <section><h3>{t('publish.pixivProcessing')}</h3><Toggle checked={pixivAiTag} onChange={setPixivAiTag} label={t('publish.aiTags')}/><Toggle checked={llmEnabled} onChange={setLlmEnabled} disabled={!llmConfig.enabled} label={t('publish.generateCopy')} description={!llmConfig.enabled ? t('publish.llmRequired') : ''}/>{llmEnabled && llmConfig.enabled && <div className="publish-llm-options"><label>{t('publish.persona')}<select value={persona} onChange={event => setPersona(event.target.value)}>{(llmConfig.personas || []).map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label><label>{t('publish.rating')}<select value={contentMode} onChange={event => setContentMode(event.target.value)}><option value="sfw">SFW</option><option value="nsfw">NSFW</option></select></label></div>}</section>}
          <section className="publish-order-note"><Icon name={sort === 'manual' ? 'queue' : 'refresh'} size={16}/><span>{sort === 'manual' ? t('publish.manualOrderHint') : t('publish.currentOrderHint')}</span></section>
        </aside>
      </div>
    </Modal>
  );
}

function shuffled(values) {
  const result = [...values];
  for (let index = result.length - 1; index > 0; index -= 1) {
    const target = Math.floor(Math.random() * (index + 1));
    [result[index], result[target]] = [result[target], result[index]];
  }
  return result;
}

function InputRequiredDialog({ request, onSubmit }) {
  const { t } = useI18n();
  const [value, setValue] = useState('');
  return <Modal title={t('prompt.waitingTitle')} onClose={() => onSubmit('')} footer={<Button variant="primary" onClick={() => onSubmit(value || '\n')}>{t('prompt.continueTask')}</Button>}><p className="flow-prompt-text">{request.prompt || t('prompt.default')}</p><label className="flow-field"><span>{t('prompt.input')}</span><input autoFocus value={value} onChange={event => setValue(event.target.value)} placeholder={t('prompt.optional')}/></label></Modal>;
}

function taskStageLabel(task, t) {
  const stageKey = `task.stage.${task?.stage || task?.status || 'queued'}`;
  const translated = t(stageKey);
  return translated === stageKey ? (task?.stage_label || t(`task.status.${task?.status || 'queued'}`)) : translated;
}

function taskRemainingLabel(rawSeconds, t, formatNumber) {
  const total = Math.max(0, Math.ceil(Number(rawSeconds || 0)));
  if (total < 60) return t('task.remaining.seconds', { seconds: formatNumber(total) });
  return t('task.remaining.minutesSeconds', {
    minutes: formatNumber(Math.floor(total / 60)),
    seconds: String(total % 60).padStart(2, '0'),
  });
}

function taskActivityLabel(task, t, formatNumber) {
  const activity = task?.activity;
  if (!activity?.kind) return '';
  if (activity.kind === 'pixiv_interaction') {
    const key = {
      pixiv_login: 'task.pixivInteraction.login',
      pixiv_profile_wait: 'task.pixivInteraction.profileWait',
      pixiv_captcha_before_submit: 'task.pixivInteraction.captchaBefore',
      pixiv_captcha_after_submit: 'task.pixivInteraction.captchaAfter',
    }[activity.interaction_type];
    if (!key) return '';
    return activity.interaction_type === 'pixiv_profile_wait'
      ? t(key)
      : t(key, { remaining: taskRemainingLabel(activity.remaining_seconds, t, formatNumber) });
  }
  if (activity.kind === 'pixiv_cooldown') {
    const key = activity.reason === 'baseline' ? 'task.pixivCooldown.baseline' : activity.reason === 'http_429' ? 'task.pixivCooldown.rateLimit' : 'task.pixivCooldown.risk';
    return t(key, {
      level: formatNumber(Math.max(0, Number(activity.risk_level || 0))),
      remaining: taskRemainingLabel(activity.remaining_seconds, t, formatNumber),
    });
  }
  if (activity.kind !== 'llm_retry') return '';
  const values = {
    attempt: formatNumber(Math.max(0, Number(activity.attempt || 0))),
    maximum: formatNumber(Math.max(1, Number(activity.max_attempts || 1))),
    current: formatNumber(Math.max(0, Number(activity.repair_attempt || activity.model_index || 0))),
    total: formatNumber(Math.max(1, Number(activity.repair_attempts || activity.model_count || 1))),
    seconds: Number(activity.delay_seconds || 0).toFixed(1),
    model: activity.model || '—',
  };
  const key = {
    attempt_started: 'task.llmRetry.attempt',
    attempt_failed: 'task.llmRetry.assessing',
    retry_scheduled: 'task.llmRetry.waiting',
    repair_started: 'task.llmRetry.repair',
    fallback_started: 'task.llmRetry.fallback',
    succeeded: 'task.llmRetry.recovered',
    failed: 'task.llmRetry.exhausted',
  }[activity.event];
  return key ? t(key, values) : '';
}

function taskReasonLabel(reasonCode, t) {
  if (!reasonCode) return '';
  const key = `task.reason.${reasonCode}`;
  const localized = t(key);
  return localized === key ? t('task.reason.withCode', { code: reasonCode }) : `${localized} · ${reasonCode}`;
}

function TaskTargetBadge({ platform, detail, expanded }) {
  const { t } = useI18n();
  const meta = TARGET_STATUS_META[detail.status] || TARGET_STATUS_META.failed;
  const platformLabel = PLATFORM_META[platform]?.label || platform;
  const content = <><b>{platformLabel}</b><span>{t(meta.labelKey)}</span>{detail.postUrl && <Icon name="link" size={12}/>}</>;
  return detail.postUrl
    ? <a className={`flow-task-target ${meta.tone}`} href={detail.postUrl} target="_blank" rel="noopener noreferrer" tabIndex={expanded ? 0 : -1} aria-label={t('task.openPost', { platform: platformLabel })}>{content}</a>
    : <span className={`flow-task-target ${meta.tone}`}>{content}</span>;
}

function TaskItemDetails({ task, viewModel, expanded, activityLabel }) {
  const { formatNumber, t } = useI18n();
  const regionId = `task-items-${task.id}`;
  return <div id={regionId} className={`flow-task-details ${expanded ? 'expanded' : ''}`} aria-hidden={!expanded}>
    <div className="flow-task-details-inner">
      <div className="flow-task-item-list">
        {viewModel.items.map(item => {
          const meta = ITEM_STATUS_META[item.status];
          const runningText = item.status === 'running'
            ? activityLabel || `${taskStageLabel({ stage: item.stage }, t)} · ${formatNumber(Math.round(item.stageProgress * 100))}%`
            : t(`task.itemStatus.${item.status}`);
          const reason = ['failed', 'partial', 'uncertain', 'canceled', 'unprocessed'].includes(item.status)
            ? taskReasonLabel(item.reasonCode, t)
            : '';
          return <div key={`${item.index}-${item.name}`} className={`flow-task-item ${meta.tone} ${item.status === 'running' ? 'current' : ''}`}>
            <span className="flow-task-item-index"><i aria-hidden="true">{meta.icon}</i>{formatNumber(item.index)}</span>
            <div className="flow-task-item-copy"><strong title={item.name}>{item.name}</strong><span>{runningText}{reason && ` · ${reason}`}</span></div>
            <div className="flow-task-targets">{Object.entries(item.targets).map(([platform, detail]) => <TaskTargetBadge key={platform} platform={platform} detail={detail} expanded={expanded}/>)}</div>
          </div>;
        })}
      </div>
    </div>
  </div>;
}

function TaskBatchSummary({ task, viewModel }) {
  const { formatNumber, t } = useI18n();
  const progressNumber = Number(task.progress || 0);
  const rawProgress = Number.isFinite(progressNumber) ? Math.max(0, Math.min(1, progressNumber)) : 0;
  const visualProgress = task.status === 'done' ? 100 : Math.min(99, rawProgress * 100);
  const displayProgress = task.status === 'done' ? 100 : Math.min(99, Math.floor(rawProgress * 100));
  const stageLabel = taskStageLabel(task, t);
  const activityLabel = taskActivityLabel(task, t, formatNumber);
  const { total, processed, succeeded, failed, canceled } = viewModel.summary;
  const itemIndexNumber = Number(task.item_index || viewModel.activeItem?.index || 0);
  const itemIndex = Number.isFinite(itemIndexNumber) ? Math.max(0, Math.min(total || Number.MAX_SAFE_INTEGER, itemIndexNumber)) : 0;
  const itemName = task.item_name || viewModel.activeItem?.name || '';
  const active = ACTIVE_TASK_STATUSES.has(task.status) && itemIndex > 0 && total > 0;
  const stageProgress = Number(task.stage_progress || 0);
  const stagePercent = Math.round((Number.isFinite(stageProgress) ? Math.max(0, Math.min(1, stageProgress)) : 0) * 100);
  const currentLabel = active
    ? t('task.progress.current', { current: formatNumber(itemIndex), total: formatNumber(total), name: itemName, stage: stageLabel, percent: formatNumber(stagePercent) })
    : stageLabel;
  const summaryLabel = [
    total ? t('task.progress.completed', { current: formatNumber(processed), total: formatNumber(total) }) : t('task.progress.preparing'),
    t('task.progress.succeeded', { count: formatNumber(succeeded) }),
    t('task.progress.failed', { count: formatNumber(failed) }),
    t('task.progress.canceled', { count: formatNumber(canceled) }),
  ].join(' · ');
  const overallLabel = t('task.progress.overall', { percent: formatNumber(displayProgress) });
  return <div className="flow-task-progress">
    <div className="flow-task-stage"><strong title={currentLabel}>{currentLabel}</strong><span>{overallLabel}</span></div>
    <div className={`flow-task-meter ${task.status === 'running' ? 'active' : ''}`} role="progressbar" aria-label={t('task.progress.batchAria')} aria-valuemin="0" aria-valuemax="100" aria-valuenow={displayProgress} aria-valuetext={`${overallLabel} · ${summaryLabel}`}><i style={{ width: `${visualProgress}%` }}/></div>
    {activityLabel && <div className="flow-task-activity" role="status">{activityLabel}</div>}
    <div className="flow-task-outcomes">{summaryLabel}</div>
  </div>;
}

function TaskRow({ task, expanded, onToggle, onCancel, onRemove, onRetry }) {
  const { formatNumber, t } = useI18n();
  const viewModel = useMemo(() => taskViewModel(task), [task]);
  const statusTone = { queued: 'idle', running: 'running', done: 'done', failed: 'failed', canceled: 'canceled', waiting_input: 'waiting' }[task.status] || 'idle';
  const commandKey = `task.command.${task.cmd}`;
  const title = [2, 3].includes(Number(task.cmd)) && viewModel.summary.total
    ? t(`${commandKey}.count`, { count: viewModel.summary.total })
    : t(commandKey);
  const targetIds = String(task.params?.targets || (task.cmd === 3 ? 'pixiv' : '')).split(',').filter(id => PLATFORM_META[id]);
  const target = targetIds.length ? targetIds.map(id => PLATFORM_META[id].label).join(' + ') : t('task.target.local');
  const retryable = viewModel.hasStructuredItems ? viewModel.retryableFiles.length > 0 : task.status === 'failed';
  const activityLabel = taskActivityLabel(task, t, formatNumber);
  const handleRowClick = event => {
    if (!viewModel.canExpand || event.target.closest('.flow-task-controls, .flow-task-details')) return;
    onToggle();
  };
  return <article className={`flow-task-row ${statusTone} ${viewModel.canExpand ? 'expandable' : ''} ${expanded ? 'expanded' : ''}`} onClick={handleRowClick}>
    <div className={`flow-task-state ${statusTone}`}><i/>{t(`task.status.${task.status}`)}</div>
    <div className="flow-task-copy"><strong>{title}</strong><span>{target} · {task.created_at || t('common.justNow')}</span></div>
    <TaskBatchSummary task={task} viewModel={viewModel}/>
    <div className="flow-task-controls">
      {viewModel.canExpand && <IconButton icon="chevron" className={`flow-task-expand ${expanded ? 'expanded' : ''}`} label={t(expanded ? 'task.collapseItems' : 'task.expandItems')} aria-expanded={expanded} aria-controls={`task-items-${task.id}`} onClick={onToggle}/>}
      {!ACTIVE_TASK_STATUSES.has(task.status) && retryable && <IconButton icon="refresh" label={viewModel.hasStructuredItems ? t('task.retryUnsuccessful') : t('task.retry')} onClick={() => onRetry(task)}/>}
      {ACTIVE_TASK_STATUSES.has(task.status) ? <IconButton icon="pause" label={t('task.cancel')} onClick={() => onCancel(task.id)}/> : <IconButton icon="x" label={t('task.remove')} onClick={() => onRemove(task.id)}/>}
    </div>
    {viewModel.canExpand && <TaskItemDetails task={task} viewModel={viewModel} expanded={expanded} activityLabel={activityLabel}/>}
  </article>;
}

function WorkspaceTaskSection({ tasks, onCancel, onRemove, onRetry }) {
  const { formatNumber, t } = useI18n();
  const [filter, setFilter] = useState('all');
  const [expandedTasks, setExpandedTasks] = useState(() => new Set());
  const workflowTasks = useMemo(() => tasks.filter(task => !isMaintenanceTask(task)), [tasks]);
  useEffect(() => {
    const ids = new Set(workflowTasks.map(task => task.id));
    setExpandedTasks(previous => {
      const next = new Set([...previous].filter(id => ids.has(id)));
      const changed = next.size !== previous.size || [...next].some(id => !previous.has(id));
      return changed ? next : previous;
    });
  }, [workflowTasks]);
  const sortedTasks = useMemo(() => [...workflowTasks].reverse(), [workflowTasks]);
  const visibleTasks = sortedTasks.filter(task => filter === 'all' || (filter === 'active' ? ACTIVE_TASK_STATUSES.has(task.status) : task.status === filter));
  const activeCount = workflowTasks.filter(task => ACTIVE_TASK_STATUSES.has(task.status)).length;
  const toggleTask = id => setExpandedTasks(previous => {
    const next = new Set(previous);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  return <section className="flow-workbench workspace-task-section">
    <header className="flow-page-toolbar"><div className="flow-page-summary"><Icon name="queue" size={17}/><span>{t('task.centerSummary', { total: formatNumber(workflowTasks.length), active: formatNumber(activeCount) })}</span></div><div className="flow-segmented task-filters" role="group" aria-label={t('task.filterLabel')}>{['all','active','failed'].map(id => <button key={id} aria-pressed={filter === id} className={filter === id ? 'active' : ''} onClick={() => setFilter(id)}>{t(`task.filter.${id}`)}</button>)}</div></header>
    <div className="flow-task-list">{visibleTasks.length ? visibleTasks.map(task => <TaskRow key={task.id} task={task} expanded={expandedTasks.has(task.id)} onToggle={() => toggleTask(task.id)} onCancel={onCancel} onRemove={onRemove} onRetry={onRetry}/>) : <div className="flow-workbench-empty"><Icon name="queue" size={25}/><strong>{workflowTasks.length ? t('task.filterEmpty') : t('task.emptyTitle')}</strong><span>{workflowTasks.length ? t('task.filterEmptyHint') : t('task.emptyHint')}</span></div>}</div>
  </section>;
}

function ActivityLog({ logs, clearLogs, notify }) {
  const { formatNumber, t } = useI18n();
  const logView = useRef(null);
  const followTail = useRef(true);
  useEffect(() => {
    if (followTail.current && logView.current) logView.current.scrollTop = logView.current.scrollHeight;
  }, [logs.length]);
  async function copyLogs() {
    try {
      await writeClipboard(formatLogsForClipboard(logs));
      notify(t('task.logsCopied', { count: logs.length }));
    } catch (_) {
      notify(t('task.copyLogsFailed'), 'error');
    }
  }
  function handleScroll(event) {
    const element = event.currentTarget;
    followTail.current = element.scrollHeight - element.scrollTop - element.clientHeight < 72;
  }
  return <section className="flow-workbench flow-page-panel">
    <header className="flow-page-toolbar"><div className="flow-page-summary"><Icon name="terminal" size={17}/><span>{t('task.logSummary', { count: formatNumber(logs.length) })}</span></div>{logs.length > 0 && <div className="flow-log-actions"><button className="flow-text-button" onClick={copyLogs}><Icon name="copy" size={14}/><span>{t('task.copyLogs')}</span></button><button className="flow-text-button" onClick={clearLogs}>{t('task.clearLogs')}</button></div>}</header>
    <div ref={logView} className="flow-log-view" onScroll={handleScroll}>{logs.length ? logs.map((entry, index) => <div className={`flow-log-line ${String(entry.lvl || '').toLowerCase()}`} key={`${entry.t}-${index}`}><time>{entry.t}</time><b>{entry.src}</b><span>{entry.msg}</span></div>) : <div className="flow-workbench-empty"><Icon name="terminal" size={25}/><strong>{t('task.emptyLogs')}</strong><span>{t('task.emptyLogsHint')}</span></div>}</div>
  </section>;
}

function GeneralSettings({ status, theme, setTheme, notify, reloadStatus }) {
  const { formatDateTime, locale, locales, setLocale, t } = useI18n();
  const [apiKey, setApiKey] = useState('');
  const [busy, setBusy] = useState('');
  const pixivSession = status.pixiv_session || { state: status.pixiv_logged_in ? 'authenticated' : 'missing' };
  async function saveKey() {
    if (!apiKey.trim()) return;
    setBusy('key');
    try { await api('/api/settings', jsonOptions('POST', { api_key: apiKey.trim() })); setApiKey(''); await reloadStatus(); notify(t('settings.apiKeySaved')); }
    catch (error) { notify(localizedError(error, t), 'error'); }
    finally { setBusy(''); }
  }
  async function accountAction(platform, action) {
    setBusy(`${platform}-${action}`);
    try {
      const endpoint = action === 'login' ? 'open-login' : action === 'cancel' ? 'login-cancel' : 'logout';
      await api(`/api/${platform}-${endpoint}`, { method: 'POST' });
      await reloadStatus();
      if (action === 'login') notify(t(platform === 'pixiv' ? 'settings.pixivLoginStarted' : 'settings.loginOpened', { platform: PLATFORM_META[platform].label }));
      if (action === 'cancel') notify(t('settings.pixivLoginCanceled'));
      if (action === 'logout') notify(t('settings.profileCleared'));
    } catch (error) { notify(localizedError(error, t), 'error'); }
    finally { setBusy(''); }
  }
  const pixivState = String(pixivSession.state || 'missing');
  const pixivChecking = pixivState === 'checking';
  const pixivInUse = pixivState === 'in_use';
  const pixivLogoutBusy = pixivState === 'in_use' && String(pixivSession.in_use_by || '').startsWith('logout');
  const pixivDetails = [];
  if (pixivSession.last_verified_at) pixivDetails.push(t('settings.pixivLastVerified', { time: formatDateTime(pixivSession.last_verified_at) }));
  if (pixivSession.last_error_code) pixivDetails.push(t('settings.pixivError', { reason: pixivSession.last_error || pixivSession.last_error_code }));
  if (Number(pixivSession.risk_level || 0) > 0) pixivDetails.push(t('settings.pixivRiskLevel', { level: pixivSession.risk_level }));
  if (pixivSession.cooldown_until) pixivDetails.push(t('settings.pixivCooldownUntil', { time: formatDateTime(pixivSession.cooldown_until) }));
  return <div className="settings-page">
    <section className="settings-section"><h3>{t('settings.appearance')}</h3><div className="settings-row"><div><strong>{t('settings.theme')}</strong><small>{t('settings.themeHint')}</small></div><div className="flow-segmented"><button aria-pressed={theme === 'dark'} className={theme === 'dark' ? 'active' : ''} onClick={() => setTheme('dark')}>{t('settings.theme.dark')}</button><button aria-pressed={theme === 'light'} className={theme === 'light' ? 'active' : ''} onClick={() => setTheme('light')}>{t('settings.theme.light')}</button></div></div><div className="settings-row"><div><strong>{t('settings.language')}</strong><small>{t('settings.languageHint')}</small></div><div className="flow-segmented locale-segmented">{locales.map(item => <button key={item.id} lang={item.id} aria-pressed={locale === item.id} className={locale === item.id ? 'active' : ''} onClick={() => setLocale(item.id)}>{item.label}</button>)}</div></div></section>
    <section className="settings-section"><h3>{t('settings.civitaiApi')}</h3><form className="settings-inline-field" onSubmit={event => { event.preventDefault(); saveKey(); }}><input className="credential-username" type="text" autoComplete="username" value="civitai-api" readOnly aria-hidden="true" tabIndex={-1}/><input type="password" autoComplete="new-password" value={apiKey} onChange={event => setApiKey(event.target.value)} placeholder={status.api_key_masked || t('settings.apiKeyPlaceholder')}/><Button type="submit" variant="primary" disabled={!apiKey.trim() || busy === 'key'}>{t('common.save')}</Button></form></section>
    <section className="settings-section"><h3>{t('settings.accounts')}</h3>
      <div className="account-row"><PlatformBadge id="civitai" connected={status.civitai_logged_in}/><div className="account-copy"><strong>{status.civitai_logged_in ? t('settings.profileCreated') : t('settings.profileMissing')}</strong></div><div className="account-actions"><Button icon="link" onClick={() => accountAction('civitai', 'login')} disabled={busy.startsWith('civitai')}>{t('common.login')}</Button>{status.civitai_logged_in && <Button variant="danger-ghost" icon="logout" onClick={() => accountAction('civitai', 'logout')} disabled={busy.startsWith('civitai')}>{t('common.clear')}</Button>}</div></div>
      <div className={`account-row pixiv-session ${pixivState}`}><PlatformBadge id="pixiv" connected={pixivState === 'authenticated'}/><div className="account-copy"><strong>{t(`settings.pixivSession.${pixivState}`)}</strong>{pixivDetails.length > 0 && <small>{pixivDetails.join(' · ')}</small>}</div><div className="account-actions">{pixivChecking ? <Button icon="x" onClick={() => accountAction('pixiv', 'cancel')} disabled={busy.startsWith('pixiv')}>{t('common.cancel')}</Button> : !pixivLogoutBusy && <Button icon="link" onClick={() => accountAction('pixiv', 'login')} disabled={busy.startsWith('pixiv') || pixivInUse}>{pixivState === 'authenticated' ? t('settings.verifyAgain') : t('common.login')}</Button>}{pixivSession.profile_exists && !pixivLogoutBusy && <Button variant="danger-ghost" icon="logout" onClick={() => accountAction('pixiv', 'logout')} disabled={busy.startsWith('pixiv') || pixivChecking || pixivInUse}>{t('common.clear')}</Button>}</div></div>
    </section>
  </div>;
}

function watermarkConfigForRenderer(current, renderer, images = []) {
  const style = current?.style || {};
  const common = {
    version: 1,
    renderer,
    enabled: Boolean(current?.enabled),
  };
  if (renderer === 'image') {
    return {
      ...common,
      image: { file_name: current?.image?.file_name || images[0] || '' },
      style: {
        position: style.position || 'bottom_right',
        size_ratio: current?.renderer === 'image' ? (style.size_ratio ?? 0.12) : 0.12,
        opacity: current?.renderer === 'image' ? (style.opacity ?? 0.85) : 0.85,
        margin_ratio: style.margin_ratio ?? 0.025,
      },
    };
  }
  return {
    ...common,
    text: current?.renderer === 'text' ? (current.text || '') : '',
    font: current?.renderer === 'text' ? (current.font || { file_name: '', face_index: 0 }) : { file_name: '', face_index: 0 },
    style: {
      position: style.position || 'bottom_right',
      font_size_ratio: current?.renderer === 'text' ? (style.font_size_ratio ?? 0.045) : 0.045,
      opacity: current?.renderer === 'text' ? (style.opacity ?? 0.72) : 0.72,
      color: current?.renderer === 'text' ? (style.color || '#FFFFFF') : '#FFFFFF',
      stroke_color: current?.renderer === 'text' ? (style.stroke_color || '#000000') : '#000000',
      margin_ratio: style.margin_ratio ?? 0.025,
    },
  };
}

function PixivSettings({ status, notify, reloadStatus }) {
  const { t } = useI18n();
  const [preset, setPreset] = useState(status.censor_preset || 'japan');
  const [mode, setMode] = useState(status.censor_mode || 'mosaic');
  const [confidence, setConfidence] = useState(status.censor_conf_threshold ?? 0.55);
  const [tagger, setTagger] = useState(null);
  const [taggerBusy, setTaggerBusy] = useState('');
  const [watermark, setWatermark] = useState(null);
  const [saving, setSaving] = useState('');

  useEffect(() => {
    Promise.all([api('/api/tagger-config'), api('/api/watermark-config')]).then(([taggerData, watermarkData]) => { setTagger(taggerData); setWatermark(watermarkData); }).catch(error => notify(localizedError(error, t), 'error'));
  }, []);

  async function saveCensor(next = {}) {
    setSaving('censor');
    try {
      if (next.preset) { await api('/api/censor-preset', jsonOptions('POST', { preset: next.preset })); setPreset(next.preset); }
      const config = { mode: next.mode || mode, conf_threshold: next.confidence ?? confidence };
      await api('/api/censor-config', jsonOptions('POST', config));
      if (next.mode) setMode(next.mode);
      if (next.confidence !== undefined) setConfidence(next.confidence);
      await reloadStatus();
      notify(t('pixiv.censorSaved'));
    } catch (error) { notify(localizedError(error, t), 'error'); }
    finally { setSaving(''); }
  }

  async function saveTagger() {
    setSaving('tagger');
    try { await api('/api/tagger-config', jsonOptions('POST', { haintag_root: tagger.haintag_root || '', model_dir: tagger.model_dir || '', pixai_model_dir: tagger.pixai_model_dir || '' })); setTagger(await api('/api/tagger-config')); notify(t('pixiv.taggerPathsSaved')); }
    catch (error) { notify(localizedError(error, t), 'error'); }
    finally { setSaving(''); }
  }

  async function installTagger(kind) {
    setTaggerBusy(kind);
    try {
      const endpoint = kind === 'pixai' ? '/api/install-pixai-tagger' : '/api/install-cl-tagger';
      const result = await api(endpoint, jsonOptions('POST', {}));
      const statusEndpoint = `${endpoint}-status/${result.task_id}`;
      for (;;) {
        await new Promise(resolve => setTimeout(resolve, 1200));
        const state = await api(statusEndpoint);
        if (state.status === 'done') break;
        if (state.status === 'error') throw new ApiError(state.error || t('pixiv.modelDownloadFailed'), { code: state.error_code, params: state.error_params });
      }
      setTagger(await api('/api/tagger-config'));
      notify(t('pixiv.modelsInstalled'));
    } catch (error) { notify(localizedError(error, t), 'error'); }
    finally { setTaggerBusy(''); }
  }

  async function saveWatermark() {
    setSaving('watermark');
    try { const result = await api('/api/watermark-config', jsonOptions('POST', watermark.config)); setWatermark(result); await reloadStatus(); notify(t('pixiv.watermarkSaved')); }
    catch (error) { notify(localizedError(error, t), 'error'); }
    finally { setSaving(''); }
  }

  async function importFont(file) {
    if (!file) return;
    const form = new FormData(); form.append('font', file);
    setSaving('font');
    try {
      const result = await api('/api/watermark-font', { method: 'POST', body: form });
      setWatermark(previous => ({
        ...result,
        config: {
          ...previous.config,
          font: { file_name: result.font.file_name, face_index: result.font.faces?.[0]?.index || 0 },
        },
      }));
      notify(t('pixiv.fontImported'));
    } catch (error) { notify(localizedError(error, t), 'error'); }
    finally { setSaving(''); }
  }

  async function deleteFont() {
    const fileName = watermark.config.font?.file_name;
    if (!fileName || !window.confirm(t('pixiv.deleteFontConfirm', { name: fileName }))) return;
    setSaving('font');
    try {
      setWatermark(await api(`/api/watermark-font/${encodeURIComponent(fileName)}`, { method: 'DELETE' }));
      notify(t('pixiv.fontDeleted'));
    } catch (error) { notify(localizedError(error, t), 'error'); }
    finally { setSaving(''); }
  }

  async function importWatermarkImage(file) {
    if (!file) return;
    const form = new FormData(); form.append('image', file);
    setSaving('image');
    try {
      const result = await api('/api/watermark-image', { method: 'POST', body: form });
      setWatermark(previous => ({
        ...result,
        config: { ...previous.config, image: { file_name: result.file_name } },
      }));
      notify(t('pixiv.imageImported'));
    } catch (error) { notify(localizedError(error, t), 'error'); }
    finally { setSaving(''); }
  }

  async function deleteWatermarkImage() {
    const fileName = watermark.config.image?.file_name;
    if (!fileName || !window.confirm(t('pixiv.deleteImageConfirm', { name: fileName }))) return;
    setSaving('image');
    try {
      const result = await api(`/api/watermark-image/${encodeURIComponent(fileName)}`, { method: 'DELETE' });
      setWatermark(result);
      await reloadStatus();
      notify(t('pixiv.imageDeleted'));
    } catch (error) { notify(localizedError(error, t), 'error'); }
    finally { setSaving(''); }
  }

  function setWatermarkRenderer(renderer) {
    setWatermark(previous => ({
      ...previous,
      config: watermarkConfigForRenderer(previous.config, renderer, previous.images),
    }));
  }

  function patchWatermark(path, value) {
    setWatermark(previous => {
      const config = structuredClone(previous.config);
      if (path.startsWith('style.')) config.style[path.slice(6)] = value;
      else if (path.startsWith('font.')) config.font[path.slice(5)] = value;
      else if (path.startsWith('image.')) config.image[path.slice(6)] = value;
      else config[path] = value;
      return { ...previous, config };
    });
  }

  const selectedFont = watermark?.fonts?.find(item => item.file_name === watermark.config.font?.file_name);
  const selectedImage = watermark?.config.image?.file_name || '';
  const watermarkReady = !watermark?.config.enabled
    || (watermark.config.renderer === 'text' ? Boolean(watermark.config.text?.trim()) : Boolean(selectedImage));

  return <div className="settings-page">
    <section className="settings-section"><h3>{t('pixiv.safety')}</h3><div className="settings-row"><div><strong>{t('pixiv.censorLevel')}</strong><small>{t('pixiv.censorHint')}</small></div><div className="flow-segmented">{['off','japan','strict'].map(value => <button key={value} aria-pressed={preset === value} className={preset === value ? 'active' : ''} disabled={saving === 'censor'} onClick={() => saveCensor({ preset: value })}>{t(`pixiv.preset.${value}`)}</button>)}</div></div><div className="settings-grid-two"><label className="flow-field"><span>{t('pixiv.method')}</span><select value={mode} onChange={event => saveCensor({ mode: event.target.value })}>{['mosaic','blur','bar','heart'].map(value => <option key={value} value={value}>{t(`pixiv.method.${value}`)}</option>)}</select></label><label className="flow-field"><span>{t('pixiv.confidence', { value: Number(confidence).toFixed(2) })}</span><input type="range" min="0.1" max="0.95" step="0.05" value={confidence} onChange={event => setConfidence(Number(event.target.value))} onMouseUp={() => saveCensor({ confidence })} onTouchEnd={() => saveCensor({ confidence })}/></label></div>{!status.mosaic_installed && <div className="settings-warning"><Icon name="alert"/><span>{t('pixiv.modelMissing')}</span></div>}</section>
    <section className="settings-section"><h3>{t('pixiv.tagger')}</h3>{tagger ? <><div className="settings-grid-two"><label className="flow-field"><span>{t('pixiv.pixaiDirectory')}</span><input value={tagger.pixai_model_dir || ''} onChange={event => setTagger({ ...tagger, pixai_model_dir: event.target.value })} placeholder="models/pixai_tagger"/></label><label className="flow-field"><span>{t('pixiv.wdDirectory')}</span><input value={tagger.model_dir || ''} onChange={event => setTagger({ ...tagger, model_dir: event.target.value })} placeholder="models/cl_tagger"/></label></div><div className="settings-actions"><Button onClick={saveTagger} disabled={saving === 'tagger'}>{t('pixiv.savePaths')}</Button><Button onClick={() => installTagger('pixai')} disabled={Boolean(taggerBusy)}>{taggerBusy === 'pixai' ? t('common.downloading') : t('pixiv.installPixai')}</Button><Button onClick={() => installTagger('cl')} disabled={Boolean(taggerBusy)}>{taggerBusy === 'cl' ? t('common.downloading') : t('pixiv.installWd')}</Button></div></> : <div className="settings-loading">{t('pixiv.readingTagger')}</div>}</section>
    <section className="settings-section watermark-settings">
      <h3>{t('pixiv.watermark')}</h3>
      {watermark ? <>
        <Toggle checked={Boolean(watermark.config.enabled)} onChange={value => patchWatermark('enabled', value)} label={t('pixiv.watermarkEnabled')} description={t('pixiv.watermarkHint')}/>
        <div className="settings-row watermark-renderer-row">
          <div><strong>{t('pixiv.watermarkType')}</strong><small>{t('pixiv.watermarkTypeHint')}</small></div>
          <div className="flow-segmented">
            {['text', 'image'].map(renderer => <button key={renderer} type="button" aria-pressed={watermark.config.renderer === renderer} className={watermark.config.renderer === renderer ? 'active' : ''} disabled={Boolean(saving)} onClick={() => setWatermarkRenderer(renderer)}>{t(renderer === 'text' ? 'pixiv.renderer.text' : 'pixiv.renderer.image')}</button>)}
          </div>
        </div>

        {watermark.config.renderer === 'text' ? <>
          <div className="settings-grid-two watermark-fields">
            <label className="flow-field"><span>{t('pixiv.watermarkText')}</span><input value={watermark.config.text || ''} maxLength="512" onChange={event => patchWatermark('text', event.target.value)} placeholder="@your_name"/></label>
            <label className="flow-field"><span>{t('pixiv.font')}</span><select value={watermark.config.font?.file_name || ''} onChange={event => patchWatermark('font.file_name', event.target.value)}><option value="">{t('common.systemFont')}</option>{(watermark.fonts || []).map(font => <option key={font.file_name} value={font.file_name}>{font.file_name}</option>)}</select></label>
            {selectedFont?.faces?.length > 1 && <label className="flow-field"><span>{t('pixiv.fontFace')}</span><select value={watermark.config.font?.face_index || 0} onChange={event => patchWatermark('font.face_index', Number(event.target.value))}>{selectedFont.faces.map(face => <option key={face.index} value={face.index}>{face.family} · {face.style}</option>)}</select></label>}
            <label className="flow-file-button"><span>{saving === 'font' ? t('pixiv.importing') : t('pixiv.importFont')}</span><input type="file" accept={(watermark.supported_font_formats || ['.ttf','.otf','.ttc','.otc']).join(',')} onChange={event => { const file = event.target.files[0]; event.target.value = ''; importFont(file); }}/></label>
            <label className="flow-field"><span>{t('pixiv.textColor')}</span><input type="color" value={watermark.config.style.color || '#FFFFFF'} onChange={event => patchWatermark('style.color', event.target.value.toUpperCase())}/></label>
            <label className="flow-field"><span>{t('pixiv.strokeColor')}</span><input type="color" value={watermark.config.style.stroke_color || '#000000'} onChange={event => patchWatermark('style.stroke_color', event.target.value.toUpperCase())}/></label>
          </div>
          {watermark.config.font?.file_name && <div className="settings-actions"><Button variant="danger-ghost" icon="trash" onClick={deleteFont} disabled={Boolean(saving)}>{t('pixiv.deleteFont')}</Button></div>}
        </> : <>
          <div className="settings-grid-two watermark-fields">
            <label className="flow-field"><span>{t('pixiv.watermarkImage')}</span><select value={selectedImage} onChange={event => patchWatermark('image.file_name', event.target.value)}><option value="">{t('pixiv.noImageSelected')}</option>{(watermark.images || []).map(name => <option key={name} value={name}>{name}</option>)}</select></label>
            <label className="flow-file-button"><span>{saving === 'image' ? t('pixiv.importing') : t('pixiv.importImage')}</span><input type="file" accept={(watermark.supported_image_formats || IMAGE_EXTENSIONS).join(',')} onChange={event => { const file = event.target.files[0]; event.target.value = ''; importWatermarkImage(file); }}/></label>
          </div>
          {selectedImage && <div className="watermark-image-preview"><figure><img src={`/api/watermark-image/${encodeURIComponent(selectedImage)}`} alt={t('pixiv.imagePreviewAlt')}/></figure><div><strong>{selectedImage}</strong><small>{t('pixiv.imagePreviewHint')}</small><Button variant="danger-ghost" icon="trash" onClick={deleteWatermarkImage} disabled={Boolean(saving)}>{t('pixiv.deleteImage')}</Button></div></div>}
        </>}

        <div className="settings-grid-two watermark-fields">
          <label className="flow-field"><span>{t('pixiv.position')}</span><select value={watermark.config.style.position} onChange={event => patchWatermark('style.position', event.target.value)}>{['bottom_right','bottom_left','top_right','top_left','center'].map(value => <option key={value} value={value}>{t(`pixiv.position.${value}`)}</option>)}</select></label>
          <label className="flow-field"><span>{t(watermark.config.renderer === 'text' ? 'pixiv.fontSize' : 'pixiv.imageSize', { value: Math.round((watermark.config.renderer === 'text' ? watermark.config.style.font_size_ratio : watermark.config.style.size_ratio) * 1000) / 10 })}</span><input type="range" min="0.01" max={watermark.config.renderer === 'text' ? '0.16' : '0.6'} step="0.005" value={watermark.config.renderer === 'text' ? watermark.config.style.font_size_ratio : watermark.config.style.size_ratio} onChange={event => patchWatermark(watermark.config.renderer === 'text' ? 'style.font_size_ratio' : 'style.size_ratio', Number(event.target.value))}/></label>
          <label className="flow-field"><span>{t('pixiv.opacity', { value: Math.round(watermark.config.style.opacity * 100) })}</span><input type="range" min="0.05" max="1" step="0.05" value={watermark.config.style.opacity} onChange={event => patchWatermark('style.opacity', Number(event.target.value))}/></label>
          <label className="flow-field"><span>{t('pixiv.margin', { value: Math.round(watermark.config.style.margin_ratio * 1000) / 10 })}</span><input type="range" min="0" max="0.15" step="0.005" value={watermark.config.style.margin_ratio} onChange={event => patchWatermark('style.margin_ratio', Number(event.target.value))}/></label>
        </div>
        {watermark.config_error && <div className="settings-warning"><Icon name="alert"/><span>{t('pixiv.invalidWatermarkConfig')}</span></div>}
        {watermark.config.enabled && !watermarkReady && <div className="settings-warning"><Icon name="alert"/><span>{t(watermark.config.renderer === 'text' ? 'pixiv.textRequired' : 'pixiv.imageRequired')}</span></div>}
        <div className="settings-actions"><Button variant="primary" onClick={saveWatermark} disabled={Boolean(saving) || !watermarkReady}>{saving === 'watermark' ? t('common.saving') : t('pixiv.saveWatermark')}</Button></div>
      </> : <div className="settings-loading">{t('pixiv.readingWatermark')}</div>}
    </section>
  </div>;
}

function llmSampleFieldSpecs(persona, platformSpecs) {
  const platformIds = Array.isArray(persona?.platform) ? persona.platform : [persona?.platform || 'pixiv'];
  const seen = new Set();
  const fields = [];
  platformIds.forEach(platformId => {
    const spec = platformSpecs?.[platformId];
    [...(spec?.fields || []), ...(spec?.extra_fields || [])].forEach(field => {
      if (field?.key && !seen.has(field.key)) { seen.add(field.key); fields.push(field); }
    });
  });
  return fields;
}

function llmSampleIsComplete(sample, fieldSpecs) {
  return fieldSpecs.filter(field => field.required).every(field => {
    const value = sample?.fields?.[field.key];
    if (field.kind !== 'tags') return Boolean(String(value || '').trim());
    const forbidden = new Set((field.forbidden_values || []).map(item => String(item).toLocaleLowerCase()));
    const forbiddenPrefixes = (field.forbidden_prefixes || []).map(item => String(item).toLocaleLowerCase());
    const tags = Array.isArray(value) ? value.map(item => String(item).trim()).filter(Boolean) : [];
    const usable = tags.map(item => item.toLocaleLowerCase()).filter(item => (
      !forbidden.has(item) && !forbiddenPrefixes.some(prefix => item.startsWith(prefix))
    ));
    return new Set(usable).size >= Number(field.min_count || 1);
  });
}

function LlmSettings({ initialConfig, platformSpecs, onSaved, notify }) {
  const { t } = useI18n();
  const [config, setConfig] = useState(() => structuredClone(initialConfig));
  const [selectedId, setSelectedId] = useState(initialConfig.default_persona_id || initialConfig.personas?.[0]?.id || '');
  const [saving, setSaving] = useState(false);
  const [models, setModels] = useState([]);
  const [modelBusy, setModelBusy] = useState(false);
  const persona = (config.personas || []).find(item => item.id === selectedId) || config.personas?.[0];
  const sampleFieldSpecs = llmSampleFieldSpecs(persona, platformSpecs);
  const retryPolicy = {
    request_attempts: 3,
    repair_attempts: 1,
    base_delay_seconds: 0.8,
    max_delay_seconds: 10,
    total_timeout_seconds: 180,
    adaptive_image: true,
    fallback_models: [],
    ...(config.retry_policy || {}),
  };

  useEffect(() => {
    setConfig(structuredClone(initialConfig));
    setSelectedId(current => (initialConfig.personas || []).some(item => item.id === current)
      ? current
      : (initialConfig.default_persona_id || initialConfig.personas?.[0]?.id || ''));
  }, [initialConfig]);
  function patchConfig(key, value) { setConfig(previous => ({ ...previous, [key]: value })); }
  function patchRetryPolicy(key, value) { setConfig(previous => ({ ...previous, retry_policy: { ...retryPolicy, ...(previous.retry_policy || {}), [key]: value } })); }
  function patchPersona(key, value) { setConfig(previous => ({ ...previous, personas: previous.personas.map(item => item.id === selectedId ? { ...item, [key]: value } : item) })); }
  function addPersona() {
    const id = `pixiv_${Date.now().toString(36)}`;
    const next = { id, label: t('llm.newPersona'), platform: ['pixiv'], default_content_mode: 'sfw', voice: '', sfw_prompt: '', nsfw_prompt: '', extra_prompt: '', avoid: [], samples: [] };
    setConfig(previous => ({ ...previous, personas: [...(previous.personas || []), next] })); setSelectedId(id);
  }
  function deletePersona() {
    if ((config.personas || []).length <= 1) { notify(t('llm.keepOnePersona'), 'error'); return; }
    const remaining = config.personas.filter(item => item.id !== selectedId);
    setConfig(previous => ({ ...previous, personas: remaining, default_persona_id: previous.default_persona_id === selectedId ? remaining[0].id : previous.default_persona_id })); setSelectedId(remaining[0].id);
  }
  function addSample() {
    const fields = Object.fromEntries(sampleFieldSpecs.map(field => [field.key, field.kind === 'tags' ? [] : '']));
    patchPersona('samples', [...(persona.samples || []), { mode: 'sfw', note: '', fields }]);
  }
  function patchSample(index, key, value) { const samples = structuredClone(persona.samples || []); if (key.startsWith('fields.')) samples[index].fields[key.slice(7)] = value; else samples[index][key] = value; patchPersona('samples', samples); }
  function removeSample(index) { patchPersona('samples', (persona.samples || []).filter((_, sampleIndex) => sampleIndex !== index)); }
  function sampleFieldLabel(field) {
    if (!field.label_key) return field.label || field.key;
    const localized = t(field.label_key);
    return localized === field.label_key ? (field.label || field.key) : localized;
  }
  async function fetchModels() {
    setModelBusy(true);
    try { const query = new URLSearchParams({ provider: config.provider || '', base_url: config.base_url || '', api_key: config.api_key || '' }); const result = await api(`/api/llm-reverse-models?${query}`); setModels(result.models || []); notify(t('llm.modelsLoaded', { count: (result.models || []).length })); }
    catch (error) { notify(localizedError(error, t), 'error'); }
    finally { setModelBusy(false); }
  }
  async function save() {
    setSaving(true);
    try { const result = await api('/api/llm-reverse-config', jsonOptions('POST', config)); setConfig(structuredClone(result)); onSaved(result); notify(t('llm.saved')); }
    catch (error) { notify(localizedError(error, t), 'error'); }
    finally { setSaving(false); }
  }
  if (!persona) return null;
  return <div className="settings-page">
    <section className="settings-section">
      <h3>{t('llm.connection')}</h3>
      <Toggle checked={Boolean(config.enabled)} onChange={value => patchConfig('enabled', value)} label={t('llm.enabled')}/>
      <div className="settings-grid-two">
        <label className="flow-field"><span>{t('llm.provider')}</span><select value={config.provider || 'openai_compatible'} onChange={event => patchConfig('provider', event.target.value)}><option value="openai_compatible">{t('llm.openaiCompatible')}</option><option value="anthropic">Anthropic</option><option value="google_gemini">Google Gemini</option></select></label>
        <label className="flow-field"><span>{t('llm.baseUrl')}</span><input value={config.base_url || ''} onChange={event => patchConfig('base_url', event.target.value)} placeholder="https://api.openai.com/v1" disabled={config.provider === 'anthropic'}/></label>
        <form className="flow-field" onSubmit={event => { event.preventDefault(); fetchModels(); }}><input className="credential-username" type="text" autoComplete="username" value="llm-api" readOnly aria-hidden="true" tabIndex={-1}/><span>{t('llm.apiKey')}</span><input type="password" autoComplete="new-password" value={config.api_key || ''} onChange={event => patchConfig('api_key', event.target.value)} placeholder={config.api_key_masked || t('llm.apiKeyPlaceholder')}/></form>
        <label className="flow-field"><span>{t('llm.model')}</span><div className="settings-model-field"><input list="llm-models" value={config.model || ''} onChange={event => patchConfig('model', event.target.value)}/><button onClick={fetchModels} disabled={modelBusy}>{modelBusy ? t('common.loading') : t('llm.fetchModels')}</button><datalist id="llm-models">{models.map(model => <option value={model} key={model}/>)}</datalist></div></label>
      </div>
    </section>
    <section className="settings-section llm-retry-settings">
      <div className="settings-heading-copy"><h3>{t('llm.retryPolicy')}</h3><p>{t('llm.retryDescription')}</p></div>
      <div className="llm-retry-layers" aria-label={t('llm.retryPolicy')}>
        {[['01','transport'],['02','repair'],['03','fallback']].map(([number, layer]) => <div key={layer}><b>{number}</b><span><strong>{t(`llm.retryLayer.${layer}`)}</strong><small>{t(`llm.retryLayer.${layer}Hint`)}</small></span></div>)}
      </div>
      <div className="settings-grid-two llm-retry-fields">
        <label className="flow-field"><span>{t('llm.requestTimeout')}</span><input type="number" min="5" max="300" step="1" value={config.timeout_seconds ?? 45} onChange={event => patchConfig('timeout_seconds', Number(event.target.value))}/></label>
        <label className="flow-field"><span>{t('llm.requestAttempts')}</span><input type="number" min="1" max="6" step="1" value={retryPolicy.request_attempts} onChange={event => patchRetryPolicy('request_attempts', Number(event.target.value))}/></label>
        <label className="flow-field"><span>{t('llm.repairAttempts')}</span><input type="number" min="0" max="3" step="1" value={retryPolicy.repair_attempts} onChange={event => patchRetryPolicy('repair_attempts', Number(event.target.value))}/></label>
        <label className="flow-field"><span>{t('llm.totalRetryBudget')}</span><input type="number" min="15" max="900" step="5" value={retryPolicy.total_timeout_seconds} onChange={event => patchRetryPolicy('total_timeout_seconds', Number(event.target.value))}/></label>
        <label className="flow-field"><span>{t('llm.baseRetryDelay')}</span><input type="number" min="0.1" max="30" step="0.1" value={retryPolicy.base_delay_seconds} onChange={event => patchRetryPolicy('base_delay_seconds', Number(event.target.value))}/></label>
        <label className="flow-field"><span>{t('llm.maxRetryDelay')}</span><input type="number" min="0.1" max="120" step="0.5" value={retryPolicy.max_delay_seconds} onChange={event => patchRetryPolicy('max_delay_seconds', Number(event.target.value))}/></label>
      </div>
      <Toggle checked={Boolean(retryPolicy.adaptive_image)} onChange={value => patchRetryPolicy('adaptive_image', value)} label={t('llm.adaptiveImage')} description={t('llm.adaptiveImageHint')}/>
      <label className="flow-field llm-fallback-models"><span>{t('llm.fallbackModels')}</span><textarea rows="3" value={(retryPolicy.fallback_models || []).join('\n')} onChange={event => patchRetryPolicy('fallback_models', event.target.value.split(/[\r\n,]+/).map(value => value.trim()).filter(Boolean).slice(0, 3))} placeholder={t('llm.fallbackModelsPlaceholder')}/><small>{t('llm.fallbackModelsHint')}</small></label>
    </section>
    <section className="settings-section"><div className="settings-heading-row"><h3>{t('llm.personas')}</h3><Button icon="plus" onClick={addPersona}>{t('common.new')}</Button></div><div className="persona-layout"><nav>{config.personas.map(item => <button aria-pressed={item.id === selectedId} className={item.id === selectedId ? 'active' : ''} key={item.id} onClick={() => setSelectedId(item.id)}><strong>{item.label}</strong><small>{item.default_content_mode?.toUpperCase()}</small></button>)}</nav><div className="persona-editor"><div className="settings-grid-two"><label className="flow-field"><span>{t('llm.name')}</span><input value={persona.label} onChange={event => patchPersona('label', event.target.value)}/></label><label className="flow-field"><span>{t('llm.defaultRating')}</span><select value={persona.default_content_mode || 'sfw'} onChange={event => patchPersona('default_content_mode', event.target.value)}><option value="sfw">SFW</option><option value="nsfw">NSFW</option></select></label></div><label className="flow-field"><span>{t('llm.voice')}</span><textarea rows="3" value={persona.voice || ''} onChange={event => patchPersona('voice', event.target.value)}/></label><label className="flow-field"><span>{t('llm.sfwPrompt')}</span><textarea rows="3" value={persona.sfw_prompt || ''} onChange={event => patchPersona('sfw_prompt', event.target.value)}/></label><label className="flow-field"><span>{t('llm.nsfwPrompt')}</span><textarea rows="3" value={persona.nsfw_prompt || ''} onChange={event => patchPersona('nsfw_prompt', event.target.value)}/></label><label className="flow-field"><span>{t('llm.extraConstraints')}</span><textarea rows="2" value={persona.extra_prompt || ''} onChange={event => patchPersona('extra_prompt', event.target.value)}/></label><label className="flow-field"><span>{t('llm.avoidWords')}</span><input value={(persona.avoid || []).join(', ')} onChange={event => patchPersona('avoid', event.target.value.split(',').map(value => value.trim()).filter(Boolean))}/></label><div className="sample-heading"><strong>{t('llm.samples')}</strong><button onClick={addSample}><Icon name="plus" size={14}/>{t('common.add')}</button></div>{(persona.samples || []).map((sample, index) => <div className={`persona-sample${llmSampleIsComplete(sample, sampleFieldSpecs) ? '' : ' incomplete'}`} key={index}><div><select value={sample.mode} onChange={event => patchSample(index, 'mode', event.target.value)}><option value="sfw">SFW</option><option value="nsfw">NSFW</option></select><input value={sample.note || ''} onChange={event => patchSample(index, 'note', event.target.value)} placeholder={t('llm.sampleNote')}/><IconButton icon="trash" label={t('llm.deleteSample')} onClick={() => removeSample(index)}/></div><div className="persona-sample-fields">{sampleFieldSpecs.map(field => <label className={`sample-field-${field.kind || 'multiline'}`} key={field.key}><span>{sampleFieldLabel(field)}{field.required && <b>*</b>}</span>{field.kind === 'text' ? <input maxLength={field.max} value={sample.fields?.[field.key] || ''} onChange={event => patchSample(index, `fields.${field.key}`, event.target.value)}/> : <textarea rows={field.kind === 'tags' ? 4 : 2} maxLength={field.kind === 'tags' ? undefined : field.max} value={field.kind === 'tags' ? (Array.isArray(sample.fields?.[field.key]) ? sample.fields[field.key].join('\n') : (sample.fields?.[field.key] || '')) : (sample.fields?.[field.key] || '')} onChange={event => patchSample(index, `fields.${field.key}`, field.kind === 'tags' ? event.target.value.split(/\r?\n/) : event.target.value)} placeholder={field.kind === 'tags' ? t('llm.tagsOnePerLine') : ''}/>}</label>)}{!llmSampleIsComplete(sample, sampleFieldSpecs) && <small className="persona-sample-hint">{t('llm.sampleRequiredHint')}</small>}</div></div>)}<div className="settings-actions spread"><Button variant="danger-ghost" icon="trash" onClick={deletePersona}>{t('llm.deletePersona')}</Button><label className="flow-radio"><input type="radio" checked={config.default_persona_id === selectedId} onChange={() => patchConfig('default_persona_id', selectedId)}/>{t('llm.setDefault')}</label></div></div></div></section>
    <div className="settings-sticky-save"><Button variant="primary" onClick={save} disabled={saving}>{saving ? t('common.saving') : t('llm.save')}</Button></div>
  </div>;
}

function SchedulerSettings({ scheduler, onChanged, llmConfig, notify }) {
  const { formatDateTime, t } = useI18n();
  const [draft, setDraft] = useState(() => structuredClone(scheduler));
  const [saving, setSaving] = useState(false);
  useEffect(() => setDraft(structuredClone(scheduler)), [scheduler]);
  const targets = new Set(String(draft.targets || 'civitai,pixiv').split(',').filter(Boolean));
  function patch(key, value) { setDraft(previous => ({ ...previous, [key]: value })); }
  function toggleTarget(id) { const next = new Set(targets); if (next.has(id)) next.delete(id); else next.add(id); if (!next.size) return; patch('targets', [...next].join(',')); }
  async function save() {
    setSaving(true);
    try { const result = await api('/api/scheduler', jsonOptions('POST', draft)); onChanged(result.scheduler); notify(t(result.scheduler.enabled ? 'scheduler.started' : 'scheduler.saved')); }
    catch (error) { notify(localizedError(error, t), 'error'); }
    finally { setSaving(false); }
  }
  return <div className="settings-page"><section className="settings-section"><h3>{t('scheduler.autoPublish')}</h3><Toggle checked={Boolean(draft.enabled)} onChange={value => patch('enabled', value)} label={t('scheduler.enabled')} description={draft.next_fire_at ? t('scheduler.nextRun', { time: formatDateTime(draft.next_fire_at) }) : t('scheduler.runningHint')}/><div className="scheduler-platforms">{Object.keys(PLATFORM_META).map(id => <button key={id} aria-pressed={targets.has(id)} className={targets.has(id) ? 'active' : ''} onClick={() => toggleTarget(id)}><span className={`platform-mark ${PLATFORM_META[id].tone}`}>{PLATFORM_META[id].short}</span>{PLATFORM_META[id].label}<i>{targets.has(id) && <Icon name="check" size={13}/>}</i></button>)}</div><div className="settings-grid-two"><label className="flow-field"><span>{t('scheduler.count')}</span><input type="number" min="1" max="100" value={draft.count || 1} onChange={event => patch('count', Number(event.target.value))}/></label><label className="flow-field"><span>{t('scheduler.order')}</span><select value={draft.sort || 'random'} onChange={event => patch('sort', event.target.value)}>{[['random','random'],['time_asc','oldest'],['time_desc','latest'],['name_asc','nameAsc'],['name_desc','nameDesc']].map(([value,key]) => <option key={value} value={value}>{t(`scheduler.order.${key}`)}</option>)}</select></label><label className="flow-field"><span>{t('scheduler.minHours')}</span><input type="number" min="0.001" step="0.1" value={draft.min_hours ?? 0.4} onChange={event => patch('min_hours', Number(event.target.value))}/></label><label className="flow-field"><span>{t('scheduler.maxHours')}</span><input type="number" min="0.001" step="0.1" value={draft.max_hours ?? 0.8} onChange={event => patch('max_hours', Number(event.target.value))}/></label></div></section><section className="settings-section"><h3>{t('scheduler.pixivProcessing')}</h3><Toggle checked={(draft.ai_tags_by_platform || {}).pixiv !== false} onChange={value => patch('ai_tags_by_platform', { pixiv: value })} label={t('publish.aiTags')}/><Toggle checked={Boolean(draft.llm_reverse)} onChange={value => patch('llm_reverse', value)} disabled={!llmConfig.enabled || !targets.has('pixiv')} label={t('publish.generateCopy')} description={!llmConfig.enabled ? t('scheduler.llmRequired') : ''}/>{draft.llm_reverse && <div className="settings-grid-two"><label className="flow-field"><span>{t('publish.persona')}</span><select value={draft.llm_persona || ''} onChange={event => patch('llm_persona', event.target.value)}>{(llmConfig.personas || []).map(item => <option value={item.id} key={item.id}>{item.label}</option>)}</select></label><label className="flow-field"><span>{t('publish.rating')}</span><select value={draft.llm_content_mode || 'sfw'} onChange={event => patch('llm_content_mode', event.target.value)}><option value="sfw">SFW</option><option value="nsfw">NSFW</option></select></label></div>}</section><div className="settings-sticky-save"><Button variant="primary" onClick={save} disabled={saving}>{saving ? t('common.saving') : t('scheduler.save')}</Button></div></div>;
}

function MaintenanceAction({ command, icon, title, description, actionLabel, task, onStart, onCancel, onViewLogs }) {
  const { t } = useI18n();
  const active = task && ACTIVE_TASK_STATUSES.has(task.status);
  const progress = task?.status === 'done' ? 100 : Math.min(99, Math.max(0, Math.floor(Number(task?.progress || 0) * 100)));
  const tone = task?.status === 'failed' ? 'failed' : task?.status === 'done' ? 'done' : task?.status === 'canceled' ? 'canceled' : active ? 'running' : 'idle';
  const statusLabel = task ? t(`task.status.${task.status}`) : t('maintenance.ready');
  return <article className={`maintenance-action ${tone}`}>
    <div className="maintenance-action-icon"><Icon name={icon} size={21}/></div>
    <div className="maintenance-action-body"><div className="maintenance-action-heading"><h3>{title}</h3><span className={`maintenance-status ${tone}`}><i/>{statusLabel}</span></div><p>{description}</p>{task && <div className="maintenance-progress"><div><span>{taskStageLabel(task, t)}</span><b>{progress}%</b></div><div className={`flow-task-meter ${active ? 'active' : ''}`} role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow={progress}><i style={{ width: `${progress}%` }}/></div></div>}<div className="maintenance-action-footer"><span>{task ? t('maintenance.lastRun', { time: task.created_at || t('common.justNow') }) : t('maintenance.notRun')}</span><div>{task && <button className="flow-text-button" onClick={onViewLogs}>{t('maintenance.viewLogs')}</button>}<Button icon={active ? 'pause' : icon} onClick={() => active ? onCancel(task.id) : onStart(command)}>{active ? t('task.cancel') : actionLabel}</Button></div></div></div>
  </article>;
}

function SystemSettings({ status, tasks, connected, onStart, onCancel, onNavigate, reloadStatus }) {
  const { t } = useI18n();
  const maintenanceTasks = useMemo(() => tasks.filter(isMaintenanceTask), [tasks]);
  const latest = command => [...maintenanceTasks].reverse().find(task => Number(task.cmd) === command);
  const censorTask = latest(4);
  useEffect(() => { if (censorTask?.status === 'done') reloadStatus().catch(() => {}); }, [censorTask?.id, censorTask?.status]);
  return <div className="settings-page system-settings">
    <section className="settings-section"><h3>{t('maintenance.environment')}</h3><div className="system-facts"><div><span>{t('maintenance.version')}</span><strong>{status.version || '—'}</strong></div><div><span>{t('maintenance.service')}</span><strong className={connected ? 'online' : 'offline'}>{connected ? t('app.connected') : t('app.reconnecting')}</strong></div><div><span>{t('maintenance.censorState')}</span><strong className={status.mosaic_installed ? 'online' : ''}>{status.mosaic_installed ? t('maintenance.installed') : t('maintenance.notInstalled')}</strong></div></div></section>
    <section className="settings-section"><h3>{t('maintenance.tools')}</h3><div className="maintenance-list"><MaintenanceAction command={4} icon="shield" title={t('maintenance.censorTitle')} description={t('maintenance.censorDescription')} actionLabel={status.mosaic_installed ? t('maintenance.verifyCensor') : t('maintenance.installCensor')} task={censorTask} onStart={onStart} onCancel={onCancel} onViewLogs={() => onNavigate('logs')}/><MaintenanceAction command={5} icon="refresh" title={t('maintenance.updateTitle')} description={t('maintenance.updateDescription')} actionLabel={t('maintenance.checkUpdates')} task={latest(5)} onStart={onStart} onCancel={onCancel} onViewLogs={() => onNavigate('logs')}/></div><div className="settings-warning"><Icon name="info" size={16}/><span>{t('maintenance.restartHint')}</span></div></section>
  </div>;
}

function SettingsPage({ tab, onTabChange, status, scheduler, llmConfig, llmPlatformSpecs, theme, setTheme, reloadStatus, setScheduler, setLlmConfig, notify, tasks, connected, onMaintenance, onCancel, onNavigate }) {
  const { t } = useI18n();
  const shellRef = useRef(null);
  const navRef = useRef(null);
  const mainRef = useRef(null);
  useEffect(() => {
    mainRef.current?.scrollTo({ top: 0, behavior: 'auto' });
    const activeTab = navRef.current?.querySelector('[aria-current="page"]');
    if (activeTab && navRef.current.scrollWidth > navRef.current.clientWidth) {
      const left = activeTab.offsetLeft - (navRef.current.clientWidth - activeTab.offsetWidth) / 2;
      navRef.current.scrollTo({ left: Math.max(0, left), behavior: 'auto' });
    }
    if (window.matchMedia('(max-width: 700px)').matches && shellRef.current) {
      const shellTop = shellRef.current.getBoundingClientRect().top + window.scrollY;
      window.scrollTo({ top: Math.max(0, shellTop - 68), behavior: 'auto' });
    }
  }, [tab]);
  return <section ref={shellRef} className="settings-shell"><div className="settings-layout"><nav ref={navRef} aria-label={t('settings.sections')}><div>{SETTINGS_NAV.map(([id,icon]) => <button aria-current={tab === id ? 'page' : undefined} aria-pressed={tab === id} className={tab === id ? 'active' : ''} key={id} onClick={() => onTabChange(id)}><Icon name={icon}/><span>{t(`settings.tab.${id}`)}</span></button>)}</div></nav><div ref={mainRef} className="settings-main"><div key={tab} className="settings-page-transition">{tab === 'general' && <GeneralSettings status={status} theme={theme} setTheme={setTheme} notify={notify} reloadStatus={reloadStatus}/>} {tab === 'pixiv' && <PixivSettings status={status} notify={notify} reloadStatus={reloadStatus}/>} {tab === 'llm' && <LlmSettings initialConfig={llmConfig} platformSpecs={llmPlatformSpecs} onSaved={setLlmConfig} notify={notify}/>} {tab === 'scheduler' && <SchedulerSettings scheduler={scheduler} onChanged={setScheduler} llmConfig={llmConfig} notify={notify}/>} {tab === 'system' && <SystemSettings status={status} tasks={tasks} connected={connected} onStart={onMaintenance} onCancel={onCancel} onNavigate={onNavigate} reloadStatus={reloadStatus}/>}</div></div></div></section>;
}

function Sidebar({ status, page, activeTaskCount, onNavigate, mobileOpen, setMobileOpen }) {
  const { t } = useI18n();
  function navigate(target) { onNavigate(target); setMobileOpen(false); }
  return <aside className={`app-sidebar ${mobileOpen ? 'mobile-open' : ''}`}>
    <div className="app-brand"><span>PU</span><div><strong>{t('app.name')}</strong><small>{t('app.tagline')}</small></div></div>
    <IconButton className="mobile-menu-button" icon={mobileOpen ? 'x' : 'menu'} label={t('nav.menu')} onClick={() => setMobileOpen(!mobileOpen)}/>
    <div className="app-sidebar-content">
      <nav className="app-sidebar-nav" aria-label={t('nav.pages')}><small>{t('nav.workspaceGroup')}</small>{SIDEBAR_PAGES.map(([id,icon]) => <button key={id} aria-current={page === id ? 'page' : undefined} className={page === id ? 'active' : ''} onClick={() => navigate(id)}><Icon name={icon}/><span>{t(`nav.${id}`)}</span>{id === 'workspace' && activeTaskCount > 0 && <b>{activeTaskCount}</b>}</button>)}</nav>
      <div className="app-sidebar-bottom">
        <div className="sidebar-platforms"><PlatformBadge id="civitai" connected={status.civitai_logged_in}/><PlatformBadge id="pixiv" connected={status.pixiv_logged_in}/></div>
      </div>
    </div>
  </aside>;
}

function QuickPublish({ images, scheduler, onPublish, onOpenFolder, onOpenScheduler }) {
  const { formatNumber, formatTime, t } = useI18n();
  const previews = images.slice(0, 4);
  const schedulerLabel = scheduler.enabled
    ? (scheduler.next_fire_at ? t('quick.schedulerNext', { time: formatTime(scheduler.next_fire_at) }) : t('quick.waitingSchedule'))
    : t('quick.schedulerDisabled');
  return <section className="quick-publish">
    <div className="quick-preview">{previews.length ? previews.map(image => <img src={`/upload/${encodeURIComponent(image.name)}`} alt="" key={image.name}/>) : <div><Icon name="image" size={27}/></div>}</div>
    <div className="quick-copy"><span>{t('quick.pending')}</span><strong>{formatNumber(images.length)}<small> {t('common.imageUnit', { count: images.length })}</small></strong></div>
    <button className={`quick-scheduler ${scheduler.enabled ? 'enabled' : ''}`} onClick={onOpenScheduler}><Icon name="clock" size={16}/><span>{schedulerLabel}</span><Icon name="arrow" size={14}/></button>
    <div className="quick-actions"><Button icon="folder" onClick={onOpenFolder}>{t('quick.openFolder')}</Button><Button variant="primary" icon="upload" onClick={onPublish}>{t('nav.createPublish')}</Button></div>
  </section>;
}

function FlowConsoleApp() {
  const { t } = useI18n();
  const [route, setRoute] = useState(() => readAppRoute(window.location.hash));
  const [theme, setThemeState] = useState(() => localStorage.getItem(THEME_KEY) === 'light' ? 'light' : 'dark');
  const [status, setStatus] = useState({});
  const [images, setImages] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [logs, setLogs] = useState([]);
  const [scheduler, setScheduler] = useState({ enabled: false, targets: 'civitai,pixiv', ai_tags_by_platform: { pixiv: true } });
  const [llmConfig, setLlmConfig] = useState({ enabled: false, personas: [] });
  const [llmPlatformSpecs, setLlmPlatformSpecs] = useState({});
  const [uploadDefaults, setUploadDefaults] = useState({});
  const [connected, setConnected] = useState(false);
  const [dialog, setDialog] = useState('');
  const [inputRequest, setInputRequest] = useState(null);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [toasts, setToasts] = useState([]);
  const toastTimers = useRef(new Map());
  const pageHeading = useRef(null);

  function setTheme(value) {
    setThemeState(value); localStorage.setItem(THEME_KEY, value);
    document.documentElement.dataset.flowTheme = value;
  }
  function navigate(page, settingsTab) {
    const nextTab = page === 'settings' ? (settingsTab || route.settingsTab || 'general') : 'general';
    const nextHash = routeHash(page, nextTab);
    if (window.location.hash === nextHash) setRoute({ page, settingsTab: nextTab });
    else window.location.hash = nextHash;
    setMobileOpen(false);
  }
  useEffect(() => { document.documentElement.dataset.flowTheme = theme; }, []);
  useEffect(() => {
    const syncRoute = () => {
      const nextRoute = readAppRoute(window.location.hash);
      const canonicalHash = routeHash(nextRoute.page, nextRoute.settingsTab);
      if (window.location.hash !== canonicalHash) window.history.replaceState(null, '', canonicalHash);
      setRoute(nextRoute);
    };
    syncRoute();
    window.addEventListener('hashchange', syncRoute);
    return () => window.removeEventListener('hashchange', syncRoute);
  }, []);
  useEffect(() => {
    const behavior = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth';
    window.scrollTo({ top: 0, behavior });
  }, [route.page]);
  useEffect(() => {
    pageHeading.current?.focus({ preventScroll: true });
  }, [route.page, route.settingsTab]);
  function dismissToast(id) { setToasts(previous => previous.filter(toast => toast.id !== id)); clearTimeout(toastTimers.current.get(id)); toastTimers.current.delete(id); }
  function notify(message, type = 'success') {
    const id = `${Date.now()}-${Math.random()}`;
    setToasts(previous => [...previous.slice(-3), { id, message, type }]);
    toastTimers.current.set(id, setTimeout(() => dismissToast(id), 4200));
  }

  async function reloadStatus() { const next = await api('/api/status'); setStatus(next); if (next.scheduler) setScheduler(next.scheduler); return next; }
  async function reloadImages() { const next = await api('/api/images'); setImages(next); return next; }
  async function reloadTasks() { const next = await api('/api/tasks'); setTasks(next); return next; }
  useEffect(() => {
    Promise.all([reloadStatus(), reloadImages(), reloadTasks(), api('/api/llm-reverse-config'), api('/api/llm-reverse-platforms'), api('/api/upload-defaults')]).then(([, , , llm, platforms, defaults]) => { setLlmConfig(llm); setLlmPlatformSpecs(platforms); setUploadDefaults(defaults); }).catch(error => notify(localizedError(error, t), 'error'));
    const source = new EventSource('/api/stream');
    const listen = (name, handler) => source.addEventListener(name, event => { try { handler(JSON.parse(event.data)); } catch (_) {} });
    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);
    listen('task_update', task => {
      setTasks(previous => upsertTask(previous, task));
      if (isMaintenanceTask(task) && task.status === 'done') reloadStatus().catch(() => {});
    });
    listen('task_remove', data => setTasks(previous => previous.filter(task => task.id !== data.id)));
    listen('log', entry => setLogs(previous => [...previous.slice(-499), entry]));
    listen('images_changed', () => { reloadImages(); reloadStatus(); });
    listen('scheduler_update', setScheduler);
    listen('status_update', update => setStatus(previous => ({ ...previous, ...update })));
    listen('input_required', request => setInputRequest(request));
    return () => source.close();
  }, []);

  async function runTask(command, params = {}, noticeKey = 'task.queuedNotice') {
    const result = await api(`/api/run/${command}`, jsonOptions('POST', params));
    if (result.task) setTasks(previous => upsertTask(previous, result.task));
    notify(t(noticeKey));
    return result;
  }
  async function cancelTask(id) { try { await api(`/api/tasks/${id}/cancel`, { method: 'POST' }); notify(t('task.cancelNotice')); } catch (error) { notify(localizedError(error, t), 'error'); } }
  async function removeTask(id) { try { await api(`/api/tasks/${id}/remove`, { method: 'POST' }); } catch (error) { notify(localizedError(error, t), 'error'); } }
  async function retryTask(task) {
    try {
      const viewModel = taskViewModel(task);
      if (viewModel.hasStructuredItems) {
        if (!viewModel.retryableFiles.length) {
          notify(t('task.noRetryableItems'), 'error');
          return;
        }
        await runTask(task.cmd, { ...(task.params || {}), files: viewModel.retryableFiles });
        return;
      }
      await runTask(task.cmd, task.params || {});
    } catch (error) { notify(localizedError(error, t), 'error'); }
  }
  async function openFolder() { try { await api('/api/open-folder'); } catch (error) { notify(localizedError(error, t), 'error'); } }
  async function submitInput(answer) { try { await api(`/api/tasks/${inputRequest.task_id}/resume`, jsonOptions('POST', { answer })); setInputRequest(null); } catch (error) { notify(localizedError(error, t), 'error'); } }
  async function startMaintenance(command) {
    try { await runTask(command, {}, 'maintenance.started'); }
    catch (error) { notify(localizedError(error, t), 'error'); }
  }
  async function startPublish(command, params) {
    return runTask(command, params);
  }
  const activeTaskCount = tasks.filter(task => !isMaintenanceTask(task) && ACTIVE_TASK_STATUSES.has(task.status)).length;

  return <div className="flow-application">
    <Sidebar status={status} page={route.page} activeTaskCount={activeTaskCount} onNavigate={navigate} mobileOpen={mobileOpen} setMobileOpen={setMobileOpen}/>
    {mobileOpen && <button className="mobile-menu-scrim" aria-label={t('nav.closeMenu')} onClick={() => setMobileOpen(false)}/>}
    <main className="app-main">
      <header className="app-topbar"><div><h1 ref={pageHeading} tabIndex={-1}>{t(`page.${route.page}`)}</h1><span className={connected ? 'connected' : ''}><i/>{connected ? t('app.connected') : t('app.reconnecting')}</span></div><span className="app-version">v{status.version || t('common.notAvailable')}</span></header>
      <div className={`app-content ${route.page === 'settings' ? 'settings-content' : ''}`}><div key={route.page} className="app-page">
        {route.page === 'workspace' && <><QuickPublish images={images} scheduler={scheduler} onPublish={() => setDialog('publish')} onOpenFolder={openFolder} onOpenScheduler={() => navigate('settings', 'scheduler')}/><WorkspaceTaskSection tasks={tasks} onCancel={cancelTask} onRemove={removeTask} onRetry={retryTask}/></>}
        {route.page === 'logs' && <ActivityLog logs={logs} clearLogs={() => setLogs([])} notify={notify}/>}
        {route.page === 'settings' && <SettingsPage tab={route.settingsTab} onTabChange={tab => navigate('settings', tab)} status={status} scheduler={scheduler} llmConfig={llmConfig} llmPlatformSpecs={llmPlatformSpecs} theme={theme} setTheme={setTheme} reloadStatus={reloadStatus} setScheduler={setScheduler} setLlmConfig={setLlmConfig} notify={notify} tasks={tasks} connected={connected} onMaintenance={startMaintenance} onCancel={cancelTask} onNavigate={navigate}/>}
      </div></div>
    </main>
    {dialog === 'publish' && <PublishDialog images={images} status={status} defaults={uploadDefaults} llmConfig={llmConfig} onReloadImages={reloadImages} onClose={() => setDialog('')} onRun={startPublish} notify={notify}/>}
    {inputRequest && <InputRequiredDialog request={inputRequest} onSubmit={submitInput}/>}
    <ToastStack toasts={toasts} dismiss={dismissToast}/>
  </div>;
}

createRoot(document.getElementById('root')).render(<I18nProvider><FlowConsoleApp/></I18nProvider>);
