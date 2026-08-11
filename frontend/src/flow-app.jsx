import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { I18nProvider, localizedError, requestLocale, useI18n } from './i18n.jsx';

const THEME_KEY = 'flow-theme-v2';
const IMAGE_EXTENSIONS = ['.png', '.jpg', '.jpeg', '.webp'];
const PLATFORM_META = {
  civitai: { label: 'Civitai', short: 'C', tone: 'blue' },
  pixiv: { label: 'Pixiv', short: 'P', tone: 'cyan' },
};

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

function Icon({ name, size = 18 }) {
  const common = { width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.8, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true };
  const paths = {
    upload: <><path d="M12 16V4"/><path d="m7 9 5-5 5 5"/><path d="M4 15v4h16v-4"/></>,
    split: <><path d="M6 4v5a3 3 0 0 0 3 3h6a3 3 0 0 1 3 3v5"/><path d="m3 7 3-3 3 3"/><path d="m15 17 3 3 3-3"/></>,
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

  return (
    <Modal title={t('publish.title')} onClose={onClose} wide className="publish-modal" footer={<><span className="flow-footer-summary">{t('common.selectedImages', { count: selectedOrdered.length })} · {[...targets].map(id => PLATFORM_META[id].label).join(' + ') || t('common.noPlatform')}</span><Button onClick={onClose}>{t('common.cancel')}</Button><Button variant="primary" icon="upload" disabled={saving || !selectedOrdered.length || !targets.size} onClick={confirmPublish}>{saving ? t('publish.creating') : t('publish.start')}</Button></>}>
      <div className="publish-layout">
        <div className="publish-library">
          <div className={`flow-dropzone ${dragOver ? 'active' : ''}`} role="button" tabIndex={0} aria-busy={uploading} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); fileInput.current?.click(); } }} onDragEnter={event => { event.preventDefault(); setDragOver(true); }} onDragOver={event => event.preventDefault()} onDragLeave={event => { if (!event.currentTarget.contains(event.relatedTarget)) setDragOver(false); }} onDrop={event => { event.preventDefault(); addFiles(event.dataTransfer.files); }} onClick={() => fileInput.current?.click()}>
            <input ref={fileInput} type="file" accept="image/png,image/jpeg,image/webp" multiple hidden onChange={event => addFiles(event.target.files)}/>
            <Icon name="plus"/><span>{uploading ? t('publish.importing') : t('publish.dropHint')}</span>
          </div>
          <div className="publish-library-toolbar">
            <div className="flow-segmented" aria-label={t('publish.sortLabel')}>
              {[['time_desc','publish.sort.latest'],['name_asc','publish.sort.name'],['random','publish.sort.random'],['manual','publish.sort.manual']].map(([value, key]) => <button key={value} aria-pressed={sort === value} className={sort === value ? 'active' : ''} onClick={() => setSort(value)}>{t(key)}</button>)}
            </div>
            <button className="flow-text-button" onClick={() => setSelected(selected.size === images.length ? new Set() : new Set(images.map(image => image.name)))}>{selected.size === images.length && images.length ? t('publish.clearSelection') : t('publish.selectAll')}</button>
          </div>
          {images.length ? (
            <div className="publish-image-grid">
              {visible.map(image => {
                const checked = selected.has(image.name);
                return <button key={image.name} aria-pressed={checked} draggable={sort === 'manual'} onDragStart={() => setDragName(image.name)} onDragOver={event => { if (sort === 'manual') { event.preventDefault(); reorder(image.name); } }} onDragEnd={() => setDragName('')} className={`publish-thumb ${checked ? 'selected' : ''} ${dragName === image.name ? 'dragging' : ''}`} onClick={() => setSelected(previous => { const next = new Set(previous); if (next.has(image.name)) next.delete(image.name); else next.add(image.name); return next; })} title={image.name}>
                  <img src={`/upload/${encodeURIComponent(image.name)}`} alt="" loading="lazy"/>
                  <span className="publish-thumb-check"><Icon name="check" size={13}/></span>
                  <small>{image.name}</small>
                </button>;
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

function PromptDialog({ title, label, placeholder, onClose, onConfirm }) {
  const { t } = useI18n();
  const [value, setValue] = useState('');
  return <Modal title={title} onClose={onClose} footer={<><Button onClick={onClose}>{t('common.cancel')}</Button><Button variant="primary" disabled={!value.trim()} onClick={() => onConfirm(value)}>{t('common.continue')}</Button></>}><label className="flow-field"><span>{label}</span><textarea autoFocus rows="5" value={value} placeholder={placeholder} onChange={event => setValue(event.target.value)}/></label></Modal>;
}

function InputRequiredDialog({ request, onSubmit }) {
  const { t } = useI18n();
  const [value, setValue] = useState('');
  return <Modal title={t('prompt.waitingTitle')} onClose={() => onSubmit('')} footer={<Button variant="primary" onClick={() => onSubmit(value || '\n')}>{t('prompt.continueTask')}</Button>}><p className="flow-prompt-text">{request.prompt || t('prompt.default')}</p><label className="flow-field"><span>{t('prompt.input')}</span><input autoFocus value={value} onChange={event => setValue(event.target.value)} placeholder={t('prompt.optional')}/></label></Modal>;
}

function TaskRow({ task, onCancel, onRemove, onRetry }) {
  const { formatNumber, t } = useI18n();
  const statusTone = { queued: 'idle', running: 'running', done: 'done', failed: 'failed', canceled: 'idle', waiting_input: 'waiting' }[task.status] || 'idle';
  const progress = Math.max(0, Math.min(100, Number(task.progress || 0) * 100));
  const total = Number(task.total || task.params?.files?.length || 0);
  const current = Number.isFinite(Number(task.current)) ? Number(task.current) : Math.round(total * progress / 100);
  const title = task.cmd === 2 && total ? t('task.command.2.count', { count: total }) : t(`task.command.${task.cmd}`);
  const targetIds = String(task.params?.targets || (task.cmd === 3 ? 'pixiv' : '')).split(',').filter(id => PLATFORM_META[id]);
  const target = targetIds.length ? targetIds.map(id => PLATFORM_META[id].label).join(' + ') : t('task.target.local');
  return <article className="flow-task-row">
    <div className={`flow-task-state ${statusTone}`}><i/>{t(`task.status.${task.status}`)}</div>
    <div className="flow-task-copy"><strong>{title}</strong><span>{target} · {task.created_at || t('common.justNow')}</span></div>
    <div className="flow-task-meter"><i style={{ width: `${progress}%` }}/></div>
    <div className="flow-task-count">{total ? `${formatNumber(current)} / ${formatNumber(total)}` : t('common.notAvailable')}</div>
    <div className="flow-task-controls">{task.status === 'failed' && <IconButton icon="refresh" label={t('task.retry')} onClick={() => onRetry(task)}/>} {(task.status === 'running' || task.status === 'queued' || task.status === 'waiting_input') ? <IconButton icon="pause" label={t('task.cancel')} onClick={() => onCancel(task.id)}/> : <IconButton icon="x" label={t('task.remove')} onClick={() => onRemove(task.id)}/>}</div>
  </article>;
}

function Workbench({ tasks, logs, view, onViewChange, onCancel, onRemove, onRetry, clearLogs }) {
  const { t } = useI18n();
  const logEnd = useRef(null);
  const sortedTasks = useMemo(() => [...tasks].sort((a, b) => {
    const rank = { running: 0, waiting_input: 1, queued: 2, failed: 3, done: 4, canceled: 5 };
    return (rank[a.status] ?? 9) - (rank[b.status] ?? 9) || String(b.created_at || '').localeCompare(String(a.created_at || ''));
  }), [tasks]);
  useEffect(() => { if (view === 'logs') logEnd.current?.scrollIntoView({ block: 'nearest' }); }, [logs.length, view]);
  return <section className="flow-workbench" id="workbench">
    <header><div className="flow-workbench-tabs" role="tablist"><button role="tab" aria-selected={view === 'tasks'} className={view === 'tasks' ? 'active' : ''} onClick={() => onViewChange('tasks')}>{t('nav.tasks')} <span>{tasks.filter(task => ['running','queued','waiting_input'].includes(task.status)).length}</span></button><button role="tab" aria-selected={view === 'logs'} className={view === 'logs' ? 'active' : ''} onClick={() => onViewChange('logs')}>{t('nav.logs')}</button></div>{view === 'logs' && logs.length > 0 && <button className="flow-text-button" onClick={clearLogs}>{t('task.clearLogs')}</button>}</header>
    {view === 'tasks' ? <div className="flow-task-list">{sortedTasks.length ? sortedTasks.map(task => <TaskRow key={task.id} task={task} onCancel={onCancel} onRemove={onRemove} onRetry={onRetry}/>) : <div className="flow-workbench-empty"><Icon name="queue" size={25}/><strong>{t('task.emptyTitle')}</strong><span>{t('task.emptyHint')}</span></div>}</div> : <div className="flow-log-view">{logs.length ? logs.map((entry, index) => <div className={`flow-log-line ${String(entry.lvl || '').toLowerCase()}`} key={`${entry.t}-${index}`}><time>{entry.t}</time><b>{entry.src}</b><span>{entry.msg}</span></div>) : <div className="flow-workbench-empty"><Icon name="terminal" size={25}/><strong>{t('task.emptyLogs')}</strong></div>}<div ref={logEnd}/></div>}
  </section>;
}

function GeneralSettings({ status, theme, setTheme, notify, reloadStatus }) {
  const { locale, locales, setLocale, t } = useI18n();
  const [apiKey, setApiKey] = useState('');
  const [busy, setBusy] = useState('');
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
      await api(`/api/${platform}-${action === 'login' ? 'open-login' : 'logout'}`, { method: 'POST' });
      if (action === 'login') { await reloadStatus(); notify(t('settings.loginOpened', { platform: PLATFORM_META[platform].label })); } else { await reloadStatus(); notify(t('settings.profileCleared')); }
    } catch (error) { notify(localizedError(error, t), 'error'); }
    finally { setBusy(''); }
  }
  return <div className="settings-page">
    <section className="settings-section"><h3>{t('settings.appearance')}</h3><div className="settings-row"><div><strong>{t('settings.theme')}</strong><small>{t('settings.themeHint')}</small></div><div className="flow-segmented"><button aria-pressed={theme === 'dark'} className={theme === 'dark' ? 'active' : ''} onClick={() => setTheme('dark')}>{t('settings.theme.dark')}</button><button aria-pressed={theme === 'light'} className={theme === 'light' ? 'active' : ''} onClick={() => setTheme('light')}>{t('settings.theme.light')}</button></div></div><div className="settings-row"><div><strong>{t('settings.language')}</strong><small>{t('settings.languageHint')}</small></div><div className="flow-segmented locale-segmented">{locales.map(item => <button key={item.id} lang={item.id} aria-pressed={locale === item.id} className={locale === item.id ? 'active' : ''} onClick={() => setLocale(item.id)}>{item.label}</button>)}</div></div></section>
    <section className="settings-section"><h3>{t('settings.civitaiApi')}</h3><form className="settings-inline-field" onSubmit={event => { event.preventDefault(); saveKey(); }}><input className="credential-username" type="text" autoComplete="username" value="civitai-api" readOnly aria-hidden="true" tabIndex={-1}/><input type="password" autoComplete="new-password" value={apiKey} onChange={event => setApiKey(event.target.value)} placeholder={status.api_key_masked || t('settings.apiKeyPlaceholder')}/><Button type="submit" variant="primary" disabled={!apiKey.trim() || busy === 'key'}>{t('common.save')}</Button></form></section>
    <section className="settings-section"><h3>{t('settings.accounts')}</h3>{['civitai','pixiv'].map(id => <div className="account-row" key={id}><PlatformBadge id={id} connected={status[`${id}_logged_in`]}/><span>{status[`${id}_logged_in`] ? t('settings.profileCreated') : t('settings.profileMissing')}</span><div><Button icon="link" onClick={() => accountAction(id, 'login')} disabled={busy.startsWith(id)}>{t('common.login')}</Button>{status[`${id}_logged_in`] && <Button variant="danger-ghost" icon="logout" onClick={() => accountAction(id, 'logout')} disabled={busy.startsWith(id)}>{t('common.clear')}</Button>}</div></div>)}</section>
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

function LlmSettings({ initialConfig, onSaved, notify }) {
  const { t } = useI18n();
  const [config, setConfig] = useState(() => structuredClone(initialConfig));
  const [selectedId, setSelectedId] = useState(initialConfig.default_persona_id || initialConfig.personas?.[0]?.id || '');
  const [saving, setSaving] = useState(false);
  const [models, setModels] = useState([]);
  const [modelBusy, setModelBusy] = useState(false);
  const persona = (config.personas || []).find(item => item.id === selectedId) || config.personas?.[0];

  useEffect(() => { setConfig(structuredClone(initialConfig)); }, [initialConfig]);
  function patchConfig(key, value) { setConfig(previous => ({ ...previous, [key]: value })); }
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
  function addSample() { patchPersona('samples', [...(persona.samples || []), { mode: 'sfw', note: '', fields: { title_ja: '', title_zh: '', caption_ja: '', caption_zh: '' } }]); }
  function patchSample(index, key, value) { const samples = structuredClone(persona.samples || []); if (key.startsWith('fields.')) samples[index].fields[key.slice(7)] = value; else samples[index][key] = value; patchPersona('samples', samples); }
  function removeSample(index) { patchPersona('samples', (persona.samples || []).filter((_, sampleIndex) => sampleIndex !== index)); }
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
    <section className="settings-section"><h3>{t('llm.connection')}</h3><Toggle checked={Boolean(config.enabled)} onChange={value => patchConfig('enabled', value)} label={t('llm.enabled')}/><div className="settings-grid-two"><label className="flow-field"><span>{t('llm.provider')}</span><select value={config.provider || 'openai_compatible'} onChange={event => patchConfig('provider', event.target.value)}><option value="openai_compatible">{t('llm.openaiCompatible')}</option><option value="anthropic">Anthropic</option><option value="google_gemini">Google Gemini</option></select></label><label className="flow-field"><span>{t('llm.baseUrl')}</span><input value={config.base_url || ''} onChange={event => patchConfig('base_url', event.target.value)} placeholder="https://api.openai.com/v1" disabled={config.provider === 'anthropic'}/></label><form className="flow-field" onSubmit={event => { event.preventDefault(); fetchModels(); }}><input className="credential-username" type="text" autoComplete="username" value="llm-api" readOnly aria-hidden="true" tabIndex={-1}/><span>{t('llm.apiKey')}</span><input type="password" autoComplete="new-password" value={config.api_key || ''} onChange={event => patchConfig('api_key', event.target.value)} placeholder={config.api_key_masked || t('llm.apiKeyPlaceholder')}/></form><label className="flow-field"><span>{t('llm.model')}</span><div className="settings-model-field"><input list="llm-models" value={config.model || ''} onChange={event => patchConfig('model', event.target.value)}/><button onClick={fetchModels} disabled={modelBusy}>{modelBusy ? t('common.loading') : t('llm.fetchModels')}</button><datalist id="llm-models">{models.map(model => <option value={model} key={model}/>)}</datalist></div></label></div></section>
    <section className="settings-section"><div className="settings-heading-row"><h3>{t('llm.personas')}</h3><Button icon="plus" onClick={addPersona}>{t('common.new')}</Button></div><div className="persona-layout"><nav>{config.personas.map(item => <button aria-pressed={item.id === selectedId} className={item.id === selectedId ? 'active' : ''} key={item.id} onClick={() => setSelectedId(item.id)}><strong>{item.label}</strong><small>{item.default_content_mode?.toUpperCase()}</small></button>)}</nav><div className="persona-editor"><div className="settings-grid-two"><label className="flow-field"><span>{t('llm.name')}</span><input value={persona.label} onChange={event => patchPersona('label', event.target.value)}/></label><label className="flow-field"><span>{t('llm.defaultRating')}</span><select value={persona.default_content_mode || 'sfw'} onChange={event => patchPersona('default_content_mode', event.target.value)}><option value="sfw">SFW</option><option value="nsfw">NSFW</option></select></label></div><label className="flow-field"><span>{t('llm.voice')}</span><textarea rows="3" value={persona.voice || ''} onChange={event => patchPersona('voice', event.target.value)}/></label><label className="flow-field"><span>{t('llm.sfwPrompt')}</span><textarea rows="3" value={persona.sfw_prompt || ''} onChange={event => patchPersona('sfw_prompt', event.target.value)}/></label><label className="flow-field"><span>{t('llm.nsfwPrompt')}</span><textarea rows="3" value={persona.nsfw_prompt || ''} onChange={event => patchPersona('nsfw_prompt', event.target.value)}/></label><label className="flow-field"><span>{t('llm.extraConstraints')}</span><textarea rows="2" value={persona.extra_prompt || ''} onChange={event => patchPersona('extra_prompt', event.target.value)}/></label><label className="flow-field"><span>{t('llm.avoidWords')}</span><input value={(persona.avoid || []).join(', ')} onChange={event => patchPersona('avoid', event.target.value.split(',').map(value => value.trim()).filter(Boolean))}/></label><div className="sample-heading"><strong>{t('llm.samples')}</strong><button onClick={addSample}><Icon name="plus" size={14}/>{t('common.add')}</button></div>{(persona.samples || []).map((sample, index) => <div className="persona-sample" key={index}><div><select value={sample.mode} onChange={event => patchSample(index, 'mode', event.target.value)}><option value="sfw">SFW</option><option value="nsfw">NSFW</option></select><input value={sample.note || ''} onChange={event => patchSample(index, 'note', event.target.value)} placeholder={t('llm.sampleNote')}/><IconButton icon="trash" label={t('llm.deleteSample')} onClick={() => removeSample(index)}/></div><input value={sample.fields?.title_ja || ''} onChange={event => patchSample(index, 'fields.title_ja', event.target.value)} placeholder={t('llm.japaneseTitle')}/><textarea rows="2" value={sample.fields?.caption_ja || ''} onChange={event => patchSample(index, 'fields.caption_ja', event.target.value)} placeholder={t('llm.japaneseCaption')}/></div>)}<div className="settings-actions spread"><Button variant="danger-ghost" icon="trash" onClick={deletePersona}>{t('llm.deletePersona')}</Button><label className="flow-radio"><input type="radio" checked={config.default_persona_id === selectedId} onChange={() => patchConfig('default_persona_id', selectedId)}/>{t('llm.setDefault')}</label></div></div></div></section>
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

function SettingsDialog({ status, scheduler, llmConfig, theme, setTheme, onClose, reloadStatus, setScheduler, setLlmConfig, notify }) {
  const { t } = useI18n();
  const [tab, setTab] = useState('general');
  const tabs = [['general','settings'],['pixiv','shield'],['llm','wand'],['scheduler','clock']];
  return <Modal title={t('settings.title')} onClose={onClose} wide className="settings-modal"><div className="settings-layout"><nav>{tabs.map(([id,icon]) => <button aria-pressed={tab === id} className={tab === id ? 'active' : ''} key={id} onClick={() => setTab(id)}><Icon name={icon}/><span>{t(`settings.tab.${id}`)}</span></button>)}</nav><main>{tab === 'general' && <GeneralSettings status={status} theme={theme} setTheme={setTheme} notify={notify} reloadStatus={reloadStatus}/>} {tab === 'pixiv' && <PixivSettings status={status} notify={notify} reloadStatus={reloadStatus}/>} {tab === 'llm' && <LlmSettings initialConfig={llmConfig} onSaved={setLlmConfig} notify={notify}/>} {tab === 'scheduler' && <SchedulerSettings scheduler={scheduler} onChanged={setScheduler} llmConfig={llmConfig} notify={notify}/>}</main></div></Modal>;
}

function Sidebar({ status, theme, setTheme, onSplit, onSetupCensor, onUpdate, onSettings, mobileOpen, setMobileOpen }) {
  const { t } = useI18n();
  return <aside className={`app-sidebar ${mobileOpen ? 'mobile-open' : ''}`}>
    <div className="app-brand"><span>PU</span><div><strong>{t('app.name')}</strong><small>{t('app.tagline')}</small></div></div>
    <IconButton className="mobile-menu-button" icon={mobileOpen ? 'x' : 'menu'} label={t('nav.menu')} onClick={() => setMobileOpen(!mobileOpen)}/>
    <div className="app-sidebar-content">
      <div className="app-sidebar-tools"><small>{t('nav.tools')}</small><button onClick={() => { onSplit(); setMobileOpen(false); }}><Icon name="split"/><span>{t('nav.split')}</span></button><button onClick={() => { onSetupCensor(); setMobileOpen(false); }}><Icon name="shield"/><span>{t('nav.installCensor')}</span></button><button onClick={() => { onUpdate(); setMobileOpen(false); }}><Icon name="refresh"/><span>{t('nav.checkUpdates')}</span></button></div>
      <div className="app-sidebar-bottom">
        <div className="sidebar-platforms"><PlatformBadge id="civitai" connected={status.civitai_logged_in}/><PlatformBadge id="pixiv" connected={status.pixiv_logged_in}/></div>
        <div className="sidebar-bottom-actions"><IconButton icon={theme === 'dark' ? 'sun' : 'moon'} label={t('nav.toggleTheme')} onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}/><button onClick={() => { onSettings(); setMobileOpen(false); }}><Icon name="settings"/><span>{t('nav.settings')}</span></button></div>
      </div>
    </div>
  </aside>;
}

function QuickPublish({ images, scheduler, onPublish, onOpenFolder }) {
  const { formatNumber, formatTime, t } = useI18n();
  const previews = images.slice(0, 4);
  return <section className="quick-publish">
    <div className="quick-preview">{previews.length ? previews.map(image => <img src={`/upload/${encodeURIComponent(image.name)}`} alt="" key={image.name}/>) : <div><Icon name="image" size={27}/></div>}</div>
    <div className="quick-copy"><span>{t('quick.pending')}</span><strong>{formatNumber(images.length)}<small> {t('common.imageUnit', { count: images.length })}</small></strong></div>
    {scheduler.enabled && <div className="quick-scheduler"><Icon name="clock" size={16}/><span>{scheduler.next_fire_at ? formatTime(scheduler.next_fire_at) : t('quick.waitingSchedule')}</span></div>}
    <div className="quick-actions"><Button icon="folder" onClick={onOpenFolder}>{t('quick.openFolder')}</Button><Button variant="primary" icon="upload" onClick={onPublish}>{t('nav.createPublish')}</Button></div>
  </section>;
}

function FlowConsoleApp() {
  const { t } = useI18n();
  const [theme, setThemeState] = useState(() => localStorage.getItem(THEME_KEY) === 'light' ? 'light' : 'dark');
  const [status, setStatus] = useState({});
  const [images, setImages] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [logs, setLogs] = useState([]);
  const [scheduler, setScheduler] = useState({ enabled: false, targets: 'civitai,pixiv', ai_tags_by_platform: { pixiv: true } });
  const [llmConfig, setLlmConfig] = useState({ enabled: false, personas: [] });
  const [uploadDefaults, setUploadDefaults] = useState({});
  const [workbenchView, setWorkbenchView] = useState('tasks');
  const [connected, setConnected] = useState(false);
  const [dialog, setDialog] = useState('');
  const [inputRequest, setInputRequest] = useState(null);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [toasts, setToasts] = useState([]);
  const toastTimers = useRef(new Map());

  function setTheme(value) {
    setThemeState(value); localStorage.setItem(THEME_KEY, value);
    document.documentElement.dataset.flowTheme = value;
  }
  useEffect(() => { document.documentElement.dataset.flowTheme = theme; }, []);
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
    Promise.all([reloadStatus(), reloadImages(), reloadTasks(), api('/api/llm-reverse-config'), api('/api/upload-defaults')]).then(([, , , llm, defaults]) => { setLlmConfig(llm); setUploadDefaults(defaults); }).catch(error => notify(localizedError(error, t), 'error'));
    const source = new EventSource('/api/stream');
    const listen = (name, handler) => source.addEventListener(name, event => { try { handler(JSON.parse(event.data)); } catch (_) {} });
    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);
    listen('task_update', task => setTasks(previous => { const index = previous.findIndex(item => item.id === task.id); return index < 0 ? [...previous, task] : previous.map(item => item.id === task.id ? { ...item, ...task } : item); }));
    listen('task_remove', data => setTasks(previous => previous.filter(task => task.id !== data.id)));
    listen('log', entry => setLogs(previous => [...previous.slice(-499), entry]));
    listen('images_changed', () => { reloadImages(); reloadStatus(); });
    listen('scheduler_update', setScheduler);
    listen('status_update', update => setStatus(previous => ({ ...previous, ...update })));
    listen('input_required', request => setInputRequest(request));
    return () => source.close();
  }, []);

  async function runTask(command, params = {}) {
    const result = await api(`/api/run/${command}`, jsonOptions('POST', params));
    notify(t('task.queuedNotice'));
    return result;
  }
  async function cancelTask(id) { try { await api(`/api/tasks/${id}/cancel`, { method: 'POST' }); notify(t('task.cancelNotice')); } catch (error) { notify(localizedError(error, t), 'error'); } }
  async function removeTask(id) { try { await api(`/api/tasks/${id}/remove`, { method: 'POST' }); } catch (error) { notify(localizedError(error, t), 'error'); } }
  async function retryTask(task) { try { await runTask(task.cmd, task.params || {}); } catch (error) { notify(localizedError(error, t), 'error'); } }
  async function openFolder() { try { await api('/api/open-folder'); } catch (error) { notify(localizedError(error, t), 'error'); } }
  async function submitInput(answer) { try { await api(`/api/tasks/${inputRequest.task_id}/resume`, jsonOptions('POST', { answer })); setInputRequest(null); } catch (error) { notify(localizedError(error, t), 'error'); } }
  function splitPost(raw) {
    const posts = raw.split(/[\s,，]+/).map(value => value.trim()).filter(Boolean);
    runTask(1, { posts }).catch(error => notify(localizedError(error, t), 'error')); setDialog('');
  }

  return <div className="flow-application">
    <Sidebar status={status} theme={theme} setTheme={setTheme} onSplit={() => setDialog('split')} onSetupCensor={() => runTask(4).catch(error => notify(localizedError(error, t), 'error'))} onUpdate={() => runTask(5).catch(error => notify(localizedError(error, t), 'error'))} onSettings={() => setDialog('settings')} mobileOpen={mobileOpen} setMobileOpen={setMobileOpen}/>
    {mobileOpen && <button className="mobile-menu-scrim" aria-label={t('nav.closeMenu')} onClick={() => setMobileOpen(false)}/>}
    <main className="app-main">
      <header className="app-topbar"><div><h1>{t('app.workspace')}</h1><span className={connected ? 'connected' : ''}><i/>{connected ? t('app.connected') : t('app.reconnecting')}</span></div><span className="app-version">v{status.version || t('common.notAvailable')}</span></header>
      <div className="app-content">
        <QuickPublish images={images} scheduler={scheduler} onPublish={() => setDialog('publish')} onOpenFolder={openFolder}/>
        <Workbench tasks={tasks} logs={logs} view={workbenchView} onViewChange={setWorkbenchView} onCancel={cancelTask} onRemove={removeTask} onRetry={retryTask} clearLogs={() => setLogs([])}/>
      </div>
    </main>
    {dialog === 'publish' && <PublishDialog images={images} status={status} defaults={uploadDefaults} llmConfig={llmConfig} onReloadImages={reloadImages} onClose={() => setDialog('')} onRun={runTask} notify={notify}/>}
    {dialog === 'split' && <PromptDialog title={t('dialog.splitTitle')} label={t('dialog.splitLabel')} placeholder={t('dialog.splitPlaceholder')} onClose={() => setDialog('')} onConfirm={splitPost}/>}
    {dialog === 'settings' && <SettingsDialog status={status} scheduler={scheduler} llmConfig={llmConfig} theme={theme} setTheme={setTheme} onClose={() => setDialog('')} reloadStatus={reloadStatus} setScheduler={setScheduler} setLlmConfig={setLlmConfig} notify={notify}/>}
    {inputRequest && <InputRequiredDialog request={inputRequest} onSubmit={submitInput}/>}
    <ToastStack toasts={toasts} dismiss={dismissToast}/>
  </div>;
}

createRoot(document.getElementById('root')).render(<I18nProvider><FlowConsoleApp/></I18nProvider>);
