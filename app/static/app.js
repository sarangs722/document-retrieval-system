'use strict';
const $ = (id) => document.getElementById(id);
const state = { config: null, documents: [], selected: new Set(), conversation: null, mode: 'answer', busy: false, uploads: 0, refreshing: false };
let noticeTimer;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function notify(message, error = false) {
  clearTimeout(noticeTimer);
  $('notice').textContent = message;
  $('notice').className = error ? 'notice error' : 'notice';
  $('notice').hidden = false;
  noticeTimer = setTimeout(() => { $('notice').hidden = true; }, error ? 14000 : 7000);
}
function headers(json = false) {
  const value = {};
  const key = sessionStorage.getItem('atlas-api-key');
  if (key) value['X-API-Key'] = key;
  if (json) value['Content-Type'] = 'application/json';
  return value;
}
async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { ...headers(), ...options.headers } });
  if (!response.ok) {
    if (response.status === 401 && !$('settings-dialog').open) $('settings-dialog').showModal();
    let detail;
    try { const body = await response.json(); detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail); } catch { detail = response.statusText; }
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return response.status === 204 ? null : response.json();
}
function updateScope() {
  $('scope-label').textContent = state.selected.size ? `${state.selected.size} selected` : 'All documents';
}
async function refresh() {
  if (state.refreshing) return;
  state.refreshing = true;
  try {
    const [documents, stats] = await Promise.all([api('/api/documents?limit=200'), api('/api/stats')]);
    state.documents = documents;
    const ready = documents.filter((d) => d.status === 'ready' && d.index_signature === state.config?.index_signature);
    const eligible = new Set(ready.map((d) => d.id));
    for (const id of state.selected) if (!eligible.has(id)) state.selected.delete(id);
    $('stat-documents').textContent = stats.documents;
    $('stat-chunks').textContent = stats.chunks;
    $('stat-ready').textContent = ready.length;
    $('library-count').textContent = String(stats.documents).padStart(2, '0');
    renderLibrary();
    updateScope();
  } finally { state.refreshing = false; }
}
function renderLibrary() {
  const list = $('document-list');
  list.replaceChildren();
  if (!state.documents.length) {
    const empty = element('div', 'empty-library');
    empty.append(element('span', '', '▤'), element('p', '', 'A little knowledge goes a long way.\nAdd your first document above.'));
    list.append(empty);
    return;
  }
  for (const doc of state.documents) {
    const row = element('div', 'document-row');
    const stale = doc.status === 'ready' && doc.index_signature !== state.config?.index_signature;
    row.append(element('span', 'file-icon', doc.filename.split('.').pop().toUpperCase().slice(0, 3)));
    const info = element('div', 'file-info');
    const name = element('span', 'file-name', doc.filename);
    name.title = doc.filename;
    const meta = element('div', 'file-meta');
    const status = element('span', `file-status ${doc.status}`, stale ? 'Reindex required' : doc.status === 'ready' ? `${doc.page_count} page${doc.page_count === 1 ? '' : 's'} · ${doc.chunk_count} passages` : doc.status);
    meta.append(status, document.createTextNode(` · ${Math.max(1, Math.round(doc.size_bytes / 1024))} KB`));
    info.append(name, meta);
    if (doc.error) { const error = element('div', 'file-meta error-text', doc.error); info.append(error); }
    row.append(info);
    const actions = element('div', 'file-actions');
    if (doc.status === 'failed' || stale) {
      const retry = element('button', '', '↻'); retry.title = 'Reindex document'; retry.setAttribute('aria-label', `Reindex ${doc.filename}`);
      retry.addEventListener('click', async () => { try { await api(`/api/documents/${doc.id}/reindex`, { method: 'POST' }); await refresh(); } catch (e) { notify(e.message, true); } });
      actions.append(retry);
    }
    const remove = element('button', '', '×'); remove.title = 'Delete document'; remove.setAttribute('aria-label', `Delete ${doc.filename}`);
    remove.addEventListener('click', async () => {
      if (!confirm(`Delete “${doc.filename}” and its index? Saved conversations retain their cited excerpts.`)) return;
      try { await api(`/api/documents/${doc.id}`, { method: 'DELETE' }); state.selected.delete(doc.id); await refresh(); } catch (e) { notify(e.message, true); }
    });
    const checkbox = element('input'); checkbox.type = 'checkbox'; checkbox.checked = state.selected.has(doc.id); checkbox.disabled = doc.status !== 'ready' || stale;
    checkbox.setAttribute('aria-label', `Include ${doc.filename} in search`);
    checkbox.addEventListener('change', () => { checkbox.checked ? state.selected.add(doc.id) : state.selected.delete(doc.id); updateScope(); });
    actions.append(remove, checkbox); row.append(actions); list.append(row);
  }
}
async function uploadFiles(files) {
  const limit = state.config?.max_upload_mb || 20;
  state.uploads++;
  $('dropzone').classList.add('dragover');
  try {
    for (const file of files) {
      if (!/\.(pdf|txt|md)$/i.test(file.name)) { notify(`${file.name}: use PDF, TXT or Markdown.`, true); continue; }
      if (file.size > limit * 1024 * 1024) { notify(`${file.name}: exceeds ${limit} MB.`, true); continue; }
      const form = new FormData(); form.append('file', file);
      try { const doc = await api('/api/documents', { method: 'POST', body: form }); notify(`${doc.filename}: ${doc.status === 'ready' ? 'already indexed' : 'added to ingestion queue'}.`); await refresh(); } catch (e) { notify(e.message, true); }
    }
  } finally { state.uploads--; if (!state.uploads) $('dropzone').classList.remove('dragover'); $('upload-input').value = ''; }
}
function scrollChat() { $('chat-body').scrollTop = $('chat-body').scrollHeight; }
function clearWelcome() { if ($('welcome')) $('welcome').remove(); }
function message(role, text = '') {
  clearWelcome();
  const outer = element('article', `message ${role}`);
  const title = element('div', 'message-heading', role === 'user' ? 'YOU' : 'ATLAS');
  const content = element('div', 'message-content', text);
  outer.append(title, content); $('chat-body').append(outer); scrollChat();
  return { outer, title, content };
}
function showSource(source) {
  $('source-title').textContent = `[${source.label}] ${source.filename}`;
  $('source-content').replaceChildren(element('small', '', `Page ${source.page} · Passage ${source.chunk_id.slice(0, 8)} · Source excerpt`), document.createTextNode(source.text));
  $('source-dialog').showModal();
}
function renderAnswer(target, answer, citations) {
  target.content.replaceChildren();
  const byLabel = new Map(citations.map((source) => [source.label, source]));
  let last = 0;
  for (const match of answer.matchAll(/\[(\d+)\]/g)) {
    target.content.append(document.createTextNode(answer.slice(last, match.index)));
    const source = byLabel.get(Number(match[1]));
    if (source) {
      const button = element('button', 'citation-link', match[0]); button.title = `${source.filename}, page ${source.page}`;
      button.addEventListener('click', () => showSource(source)); target.content.append(button);
    } else target.content.append(document.createTextNode(match[0]));
    last = match.index + match[0].length;
  }
  target.content.append(document.createTextNode(answer.slice(last)));
  const sources = element('div', 'sources');
  for (const source of citations) {
    const card = element('button', 'source-card'); card.append(element('span', '', `[${source.label}]`), element('span', '', `${source.filename} · p. ${source.page}`));
    card.addEventListener('click', () => showSource(source)); sources.append(card);
  }
  target.outer.append(sources);
}
async function streamAnswer(body, target) {
  const response = await fetch('/api/chat/stream', { method: 'POST', headers: headers(true), body: JSON.stringify(body) });
  if (!response.ok) { const err = await response.json(); throw new Error(typeof err.detail === 'string' ? err.detail : 'Unable to start response'); }
  if (!response.body) throw new Error('Streaming is unsupported in this browser.');
  const reader = response.body.getReader(); const decoder = new TextDecoder();
  let buffer = '', answer = '', completed = false, failure = null;
  target.title.classList.add('loading-dots');
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done }).replace(/\r\n/g, '\n');
      let boundary;
      while ((boundary = buffer.indexOf('\n\n')) >= 0) {
        const block = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 2);
        const lines = block.split('\n');
        const type = lines.find((line) => line.startsWith('event:'))?.slice(6).trim();
        const data = lines.filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trimStart()).join('\n');
        if (!data) continue;
        const event = JSON.parse(data);
        if (type === 'sources') state.conversation = event.conversation_id;
        if (type === 'delta') { answer += event.text; target.content.textContent = answer; scrollChat(); }
        if (type === 'error') failure = event.message;
        if (type === 'done') {
          completed = true; state.conversation = event.conversation_id;
          renderAnswer(target, event.answer, event.citations);
          const status = event.citation_status === 'valid' ? 'Citation labels checked' : event.citation_status === 'not_applicable' ? 'No supporting sources found' : 'Citation check needs review';
          target.outer.append(element('div', 'answer-meta', `${status} · ${event.provider === 'extractive' ? 'Extractive mode' : 'LLM answer'} · ${(event.latency_ms / 1000).toFixed(2)}s`));
          scrollChat();
        }
      }
      if (done) break;
    }
    if (failure || !completed) throw new Error(failure || 'Connection closed before completion. Please retry; no partial answer was saved.');
  } finally { target.title.classList.remove('loading-dots'); reader.releaseLock(); }
}
async function searchPassages(body, target) {
  const results = await api('/api/search', { method: 'POST', headers: headers(true), body: JSON.stringify(body) });
  target.content.textContent = results.length ? `${results.length} relevant passage${results.length === 1 ? '' : 's'}` : 'No supporting passages found. Try another question or add documents.';
  for (const hit of results) {
    const card = element('div', 'search-result');
    card.append(element('h4', '', `${hit.filename} · page ${hit.page}`), element('p', '', hit.text), element('small', '', `Rank fusion score: ${hit.score.toFixed(4)} · ${body.mode} retrieval`)); target.outer.append(card);
  }
  scrollChat();
}
function busy(value) { state.busy = value; $('send-button').disabled = value; $('question').disabled = value; $('new-chat').disabled = value; }
async function submit(event) {
  event?.preventDefault();
  const question = $('question').value.trim();
  if (!question || state.busy) return;
  busy(true); $('question').value = '';
  message('user', question); const target = message('assistant');
  const body = { query: question, top_k: 5, document_ids: [...state.selected], mode: $('retrieval-mode').value };
  if (state.conversation && state.mode === 'answer') body.conversation_id = state.conversation;
  try {
    if (state.mode === 'search') await searchPassages(body, target); else await streamAnswer(body, target);
    await refreshHistory();
  } catch (error) { target.outer.append(element('p', 'error-text', error.message)); notify(error.message, true); }
  finally { busy(false); $('question').focus(); }
}
async function refreshHistory() {
  const conversations = await api('/api/conversations');
  $('history-count').textContent = conversations.length;
  $('history-list').replaceChildren();
  for (const item of conversations) {
    const row = element('div', 'history-entry');
    const open = element('button', '', item.title); open.title = item.title;
    open.addEventListener('click', async () => {
      if (state.busy) return;
      try {
        const conversation = await api(`/api/conversations/${item.id}`);
        state.conversation = conversation.id; $('chat-body').replaceChildren(); setMode('answer');
        for (const entry of conversation.messages) {
          const target = message(entry.role);
          if (entry.role === 'assistant') renderAnswer(target, entry.content, entry.citations); else target.content.textContent = entry.content;
        }
        scrollChat();
      } catch (e) { notify(e.message, true); }
    });
    const remove = element('button', '', '×'); remove.setAttribute('aria-label', `Delete conversation ${item.title}`);
    remove.addEventListener('click', async () => {
      if (state.busy || !confirm('Delete this saved conversation?')) return;
      try { await api(`/api/conversations/${item.id}`, { method: 'DELETE' }); if (state.conversation === item.id) newChat(); await refreshHistory(); } catch (e) { notify(e.message, true); }
    });
    row.append(open, remove); $('history-list').append(row);
  }
}
const welcomeTemplate = $('welcome').cloneNode(true);
function wireSuggestions() { document.querySelectorAll('[data-question]').forEach((button) => button.addEventListener('click', () => { if (state.busy) return; $('question').value = button.dataset.question; $('question').focus(); })); }
function newChat() { if (state.busy) return; state.conversation = null; $('chat-body').replaceChildren(welcomeTemplate.cloneNode(true)); wireSuggestions(); }
function setMode(mode) {
  if (state.busy) return;
  state.mode = mode;
  for (const [id, name] of [['answer-tab', 'answer'], ['search-tab', 'search']]) { $(id).classList.toggle('selected', mode === name); $(id).setAttribute('aria-selected', String(mode === name)); }
}
async function initialize() {
  try {
    state.config = await api('/api/config');
    $('provider-badge').textContent = state.config.demo_mode ? 'OFFLINE DEMO' : 'SEMANTIC RETRIEVAL';
    $('provider-detail').textContent = `${state.config.embedding_provider} embeddings · ${state.config.llm_model || state.config.llm_provider} answers`;
    $('upload-formats').textContent = `PDF, TXT, Markdown · up to ${state.config.max_upload_mb} MB each`;
    await Promise.all([refresh(), refreshHistory()]);
  } catch (error) { notify(error.message, true); }
  await checkHealth();
}
async function checkHealth() {
  try {
    const response = await fetch('/api/health'); if (!response.ok) throw new Error('unavailable');
    $('health-dot').className = 'status-dot online'; $('health-label').textContent = 'Workspace online';
  } catch { $('health-dot').className = 'status-dot offline'; $('health-label').textContent = 'Workspace offline'; }
}
$('chat-form').addEventListener('submit', submit);
$('question').addEventListener('keydown', (event) => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); submit(); } });
$('upload-input').addEventListener('change', (event) => uploadFiles([...event.target.files]));
$('dropzone').addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); $('upload-input').click(); } });
for (const name of ['dragenter', 'dragover']) $('dropzone').addEventListener(name, (event) => { event.preventDefault(); $('dropzone').classList.add('dragover'); });
$('dropzone').addEventListener('dragleave', () => { if (!state.uploads) $('dropzone').classList.remove('dragover'); });
$('dropzone').addEventListener('drop', (event) => { event.preventDefault(); uploadFiles([...event.dataTransfer.files]); });
$('refresh-button').addEventListener('click', () => refresh().catch((e) => notify(e.message, true)));
$('new-chat').addEventListener('click', newChat);
$('answer-tab').addEventListener('click', () => setMode('answer'));
$('search-tab').addEventListener('click', () => setMode('search'));
$('history-toggle').addEventListener('click', () => { $('history-list').hidden = !$('history-list').hidden; });
$('workspace-tab').addEventListener('click', newChat);
$('close-source').addEventListener('click', () => $('source-dialog').close());
$('settings-button').addEventListener('click', () => $('settings-dialog').showModal());
$('close-settings').addEventListener('click', () => $('settings-dialog').close());
$('key-form').addEventListener('submit', async (event) => { event.preventDefault(); const key = $('api-key').value.trim(); if (key) sessionStorage.setItem('atlas-api-key', key); else sessionStorage.removeItem('atlas-api-key'); $('api-key').value = ''; $('settings-dialog').close(); await initialize(); });
wireSuggestions(); initialize();
setInterval(() => { if (!document.hidden && state.config && state.documents.some((d) => ['queued', 'processing', 'deleting'].includes(d.status))) refresh().catch((e) => notify(e.message, true)); }, 2000);
setInterval(() => { if (!document.hidden) checkHealth(); }, 30000);


$('mobile-history').addEventListener('click', () => { const hidden = !$('history-list').hidden; $('history-list').hidden = hidden; $('mobile-history').setAttribute('aria-expanded', String(!hidden)); });
