'use strict';
/* Client Onboarding Portal - single-page UI. No build step. All text is inserted with textContent (XSS-safe). */

const TOKEN_KEY = 'onb_token';
const S = { user: null };
const STAFF = (u) => u && (u.role === 'org_admin' || u.role === 'team_member');

function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else if (k === 'class') e.className = v;
    else if (k === 'value' && tag !== 'input') e.value = v;
    else if (v === true) e.setAttribute(k, '');
    else e.setAttribute(k, v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    e.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return e;
}

function put(el, ...kids) { el.append(...kids.flat(Infinity).filter((k) => k != null && k !== false)); }

function toast(msg, bad) {
  const t = document.getElementById('toast');
  const d = h('div', { class: bad ? 'bad' : '' }, msg);
  t.append(d);
  setTimeout(() => d.remove(), 4500);
}

async function api(method, path, body, formData) {
  const headers = {};
  const token = localStorage.getItem(TOKEN_KEY);
  if (token) headers.Authorization = 'Bearer ' + token;
  let payload;
  if (formData) payload = formData;
  else if (body !== undefined) { headers['Content-Type'] = 'application/json'; payload = JSON.stringify(body); }
  const res = await fetch('/api' + path, { method, headers, body: payload });
  if (res.status === 401 && token && !path.startsWith('/auth/')) { logout(); throw new Error('Session expired. Please log in again.'); }
  if (!res.ok) {
    let msg = 'Request failed (' + res.status + ')';
    try {
      const j = await res.json();
      if (typeof j.detail === 'string') msg = j.detail;
      else if (Array.isArray(j.detail)) msg = j.detail.map((d) => (d.loc || []).slice(1).join('.') + ': ' + d.msg).join('; ');
    } catch (_) { /* ignore */ }
    throw new Error(msg);
  }
  return res.json();
}

function setSession(data) { localStorage.setItem(TOKEN_KEY, data.access_token); S.user = data.user; }
function logout() { localStorage.removeItem(TOKEN_KEY); S.user = null; location.hash = '#/login'; route(); }

async function act(btn, fn, okMsg) {
  if (btn) btn.disabled = true;
  try { const r = await fn(); if (okMsg) toast(okMsg); return r; }
  catch (e) { toast(e.message, true); }
  finally { if (btn) btn.disabled = false; }
}

const fmtDate = (s) => (s ? new Date(s).toLocaleDateString() : '-');
const fmtTime = (s) => (s ? new Date(s).toLocaleString() : '-');
const label = (s) => String(s || '').replace(/_/g, ' ').toLowerCase().replace(/^\w/, (c) => c.toUpperCase());
const badge = (s) => h('span', { class: 'badge ' + s }, label(s));
const bar = (p) => h('div', { class: 'bar', title: p + '%' }, h('div', { style: 'width:' + Math.max(0, Math.min(100, p || 0)) + '%' }));
const field = (lbl, input) => h('div', {}, h('label', {}, lbl), input);
const link = (text, hash) => h('a', { href: hash }, text);
const table = (heads, rows) => h('div', { class: 'wrap' }, h('table', {}, h('thead', {}, h('tr', {}, heads.map((x) => h('th', {}, x)))),
  h('tbody', {}, rows.length ? rows.map((r) => h('tr', {}, r.map((c) => h('td', {}, c)))) : h('tr', {}, h('td', { colspan: heads.length, class: 'muted' }, 'Nothing here yet.')))));

// ---------------------------------------------------------------- shell

function shell(content, active) {
  const staff = STAFF(S.user);
  const items = staff
    ? [['dashboard', 'Dashboard'], ['clients', 'Clients'], ['approvals', 'Approvals'], ['templates', 'Templates'],
       ['analytics', 'Analytics'], ['team', 'Team'], ['notifications', 'Notifications']]
    : [['dashboard', 'My onboarding'], ['approvals', 'Approvals'], ['notifications', 'Notifications']];
  if (S.user.role === 'org_admin') items.push(['audit', 'Audit log']);
  if (S.user.role === 'platform_admin') items.splice(0, items.length, ['platform', 'Platform']);
  return h('div', { class: 'layout' },
    h('nav', { class: 'side' },
      h('div', { class: 'brand' }, 'Onboarding Portal'),
      items.map(([k, t]) => h('a', { href: '#/' + k, class: active === k ? 'active' : '' }, t)),
      h('div', { class: 'who' }, S.user.name, h('br'), label(S.user.role), h('br'),
        h('a', { href: '#/login', onclick: (e) => { e.preventDefault(); logout(); } }, 'Log out'))),
    h('main', { class: 'main' }, content));
}

// ---------------------------------------------------------------- public pages

function authPage(title, children) {
  return h('div', { class: 'center' }, h('div', { class: 'card' }, h('h1', {}, title), children));
}

function loginView() {
  const email = h('input', { type: 'email', required: true, autocomplete: 'username' });
  const pw = h('input', { type: 'password', required: true, autocomplete: 'current-password' });
  const btn = h('button', { type: 'submit' }, 'Log in');
  return authPage('Log in', h('form', {
    onsubmit: async (e) => {
      e.preventDefault();
      const r = await act(btn, () => api('POST', '/auth/login', { email: email.value, password: pw.value }));
      if (r) { setSession(r); location.hash = '#/dashboard'; route(); }
    } },
  field('Email', email), field('Password', pw), h('div', { class: 'row' }, btn),
  h('p', { class: 'small' }, link('Forgot password?', '#/forgot'), ' · ', link('Create an agency account', '#/register'))));
}

function registerView() {
  const org = h('input', { required: true, maxlength: 120 });
  const name = h('input', { required: true, maxlength: 120 });
  const email = h('input', { type: 'email', required: true });
  const pw = h('input', { type: 'password', required: true, minlength: 8, maxlength: 64 });
  const btn = h('button', { type: 'submit' }, 'Create account');
  return authPage('Create your agency', h('form', {
    onsubmit: async (e) => {
      e.preventDefault();
      const r = await act(btn, () => api('POST', '/auth/register', { organization_name: org.value, name: name.value, email: email.value, password: pw.value }));
      if (r) { setSession(r); location.hash = '#/dashboard'; route(); }
    } },
  field('Agency / company name', org), field('Your name', name), field('Email', email),
  field('Password (8-64 characters)', pw), h('div', { class: 'row' }, btn),
  h('p', { class: 'small' }, link('Back to log in', '#/login'))));
}

function forgotView() {
  const email = h('input', { type: 'email', required: true });
  const btn = h('button', { type: 'submit' }, 'Send reset link');
  return authPage('Forgot password', h('form', {
    onsubmit: async (e) => { e.preventDefault(); await act(btn, () => api('POST', '/auth/forgot-password', { email: email.value }), 'If that email exists, a reset link was sent.'); } },
  field('Email', email), h('div', { class: 'row' }, btn), h('p', { class: 'small' }, link('Back to log in', '#/login'))));
}

function tokenPasswordView(title, endpoint, pwKey, query) {
  const pw = h('input', { type: 'password', required: true, minlength: 8, maxlength: 64, autocomplete: 'new-password' });
  const btn = h('button', { type: 'submit' }, 'Save password');
  return authPage(title, h('form', {
    onsubmit: async (e) => {
      e.preventDefault();
      const r = await act(btn, () => api('POST', endpoint, { token: query.token || '', [pwKey]: pw.value }));
      if (r && r.access_token) { setSession(r); location.hash = '#/dashboard'; route(); }
      else if (r) { toast('Password updated. Please log in.'); location.hash = '#/login'; route(); }
    } }, field('New password (8-64 characters)', pw), h('div', { class: 'row' }, btn)));
}

async function verifyView(query) {
  let msg = 'Verifying...';
  const p = h('p', {}, msg);
  api('POST', '/auth/verify-email', { token: query.token || '' }).then(() => { p.textContent = 'Email verified. You can close this tab.'; }, (e) => { p.textContent = e.message; });
  return authPage('Verify email', [p, link('Go to the app', '#/dashboard')]);
}

// ---------------------------------------------------------------- dashboards

function statCard(n, t) { return h('div', { class: 'stat' }, h('b', {}, n), h('span', {}, t)); }

function taskList(tasks, withLink = true) {
  return table(['Task', 'Status', 'Due'], tasks.map((t) => [
    withLink ? link(t.title, '#/tasks/' + t.id) : t.title, badge(t.status),
    h('span', { class: t.overdue ? 'err' : '' }, fmtDate(t.due_date) + (t.overdue ? ' (overdue)' : ''))]));
}

async function dashboardView() {
  const d = await api('GET', '/dashboard');
  if (!STAFF(S.user)) {
    const blocks = d.projects.map((p) => h('div', { class: 'card' },
      h('h2', {}, link(p.name, '#/onboarding/' + p.id)), h('div', { class: 'row' }, h('div', { class: 'grow' }, bar(p.progress)), h('b', {}, p.progress + '%')),
      h('p', { class: 'muted small' }, p.completed + ' of ' + p.total + ' done · ' + p.changes_requested + ' need changes'), taskList(p.tasks)));
    return shell([h('h1', {}, 'Welcome, ' + (d.client.company_name || d.client.name)), blocks.length ? blocks : h('p', { class: 'muted' }, 'Your agency has not started your onboarding yet.')], 'dashboard');
  }
  const s = d.stats;
  return shell([
    h('h1', {}, 'Dashboard'),
    h('div', { class: 'grid' }, statCard(s.total_clients, 'Total clients'), statCard(s.active_onboarding, 'Active onboarding'),
      statCard(s.awaiting_clients, 'Waiting for client'), statCard(s.pending_reviews, 'Pending reviews'),
      statCard(s.overdue_tasks, 'Overdue tasks'), statCard(s.completed, 'Completed'), statCard(s.completion_rate + '%', 'Completion rate')),
    h('div', { class: 'card' }, h('h2', {}, 'Recent onboarding'),
      table(['Client', 'Onboarding', 'Progress', 'Status'], d.recent_onboarding.map((p) => [link(p.client_name, '#/clients/' + p.client_id), link(p.name, '#/onboarding/' + p.id),
        h('div', { class: 'row' }, bar(p.progress), p.progress + '%'), badge(p.status)]))),
    h('div', { class: 'card' }, h('h2', {}, 'Waiting for your review'), taskList(d.pending_reviews)),
    h('div', { class: 'card' }, h('h2', {}, 'Overdue'), taskList(d.overdue_tasks)),
    h('div', { class: 'card' }, h('h2', {}, 'Upcoming deadlines (7 days)'), taskList(d.upcoming_deadlines)),
    h('div', { class: 'card' }, h('h2', {}, 'Pending approvals'),
      table(['Item', 'Requested'], d.pending_approvals.map((a) => [link(a.task_title, '#/tasks/' + a.task_id), fmtTime(a.created_at)]))),
    h('div', { class: 'card' }, h('h2', {}, 'Recent uploads'),
      table(['File', 'Version', 'When'], d.recent_uploads.map((u) => [link(u.file_name, '#/tasks/' + u.task_id), 'v' + u.version, fmtTime(u.created_at)]))),
    h('div', { class: 'card' }, h('h2', {}, 'Recent activity'),
      table(['When', 'Who', 'What'], d.recent_activity.map((a) => [fmtTime(a.created_at), a.user_name, a.summary]))),
  ], 'dashboard');
}

// ---------------------------------------------------------------- clients

async function clientsView() {
  const search = h('input', { placeholder: 'Search name, company or email' });
  const status = h('select', {}, h('option', { value: '' }, 'All active'),
    ['Invited', 'Onboarding', 'Waiting for Client', 'Under Review', 'Changes Requested', 'Completed', 'Archived'].map((s) => h('option', { value: s }, s)));
  const holder = h('div');
  async function load() {
    const q = new URLSearchParams();
    if (search.value) q.set('q', search.value);
    if (status.value) q.set('status', status.value);
    const r = await api('GET', '/clients?' + q);
    holder.replaceChildren(table(['Name', 'Company', 'Email', 'Status', 'Progress'], r.items.map((c) => [link(c.name, '#/clients/' + c.id), c.company_name, c.email, badge(c.status),
      c.progress == null ? '-' : c.progress + '%'])));
  }
  search.addEventListener('input', () => load().catch((e) => toast(e.message, true)));
  status.addEventListener('change', () => load().catch((e) => toast(e.message, true)));
  const team = await api('GET', '/team');
  const f = { name: h('input', { required: true }), company_name: h('input'), email: h('input', { type: 'email', required: true }),
    phone: h('input'), industry: h('input'), website: h('input'), assigned: h('select', {}, team.map((u) => h('option', { value: u.id }, u.name))) };
  const btn = h('button', { type: 'submit' }, 'Create client');
  const form = h('form', { class: 'card', style: 'display:none', onsubmit: async (e) => {
    e.preventDefault();
    const r = await act(btn, () => api('POST', '/clients', { name: f.name.value, company_name: f.company_name.value, email: f.email.value, phone: f.phone.value,
      industry: f.industry.value, website: f.website.value, assigned_user_id: Number(f.assigned.value) }), 'Client created');
    if (r) location.hash = '#/clients/' + r.id;
  } }, h('h2', {}, 'New client'), field('Name', f.name), field('Company', f.company_name), field('Email', f.email), field('Phone', f.phone),
  field('Industry', f.industry), field('Website', f.website), field('Assigned to', f.assigned), h('div', { class: 'row' }, btn));
  await load();
  return shell([h('h1', {}, 'Clients'),
    h('div', { class: 'row' }, h('div', { class: 'grow' }, search), status, h('button', { onclick: () => { form.style.display = form.style.display === 'none' ? 'block' : 'none'; } }, 'New client')),
    form, h('div', { class: 'card' }, holder)], 'clients');
}

async function clientDetailView(id) {
  const c = await api('GET', '/clients/' + id);
  const [templates, timeline] = await Promise.all([api('GET', '/templates'), api('GET', '/clients/' + id + '/timeline')]);
  const tpl = h('select', {}, templates.map((t) => h('option', { value: t.id }, t.name + ' (' + t.task_count + ' tasks)')));
  const inviteOut = h('p', { class: 'small' });
  const inviteBtn = h('button', { class: 'secondary', onclick: () => act(inviteBtn, async () => {
    const r = await api('POST', '/clients/' + id + '/invite');
    inviteOut.replaceChildren('Invitation link (also emailed): ', h('input', { readonly: true, value: r.invite_url, onclick: (e) => e.target.select() }));
  }) }, c.portal_user_status === 'active' ? 'Client has joined' : 'Send invitation');
  if (c.portal_user_status === 'active') inviteBtn.disabled = true;
  const startBtn = h('button', { onclick: async () => {
    const r = await act(startBtn, () => api('POST', '/onboarding', { client_id: Number(id), template_id: Number(tpl.value) }), 'Onboarding started');
    if (r) location.hash = '#/onboarding/' + r.id;
  } }, 'Start onboarding');
  const archive = S.user.role === 'org_admin' && c.status !== 'Archived' ? h('button', { class: 'danger', onclick: async () => {
    if (!confirm('Archive this client? Their login will be disabled.')) return;
    if (await act(null, () => api('DELETE', '/clients/' + id), 'Client archived')) location.hash = '#/clients';
  } }, 'Archive') : null;
  return shell([
    h('p', {}, link('< Clients', '#/clients')), h('h1', {}, c.name, ' ', badge(c.status)),
    h('div', { class: 'card' }, h('p', {}, [c.company_name, c.email, c.phone, c.industry, c.website].filter(Boolean).join(' · ')),
      h('p', { class: 'small muted' }, 'Portal access: ' + (c.portal_user_status ? label(c.portal_user_status) : 'not invited')),
      h('div', { class: 'row' }, inviteBtn, archive), inviteOut),
    h('div', { class: 'card' }, h('h2', {}, 'Start a new onboarding'), h('div', { class: 'row' }, h('div', { class: 'grow' }, tpl), startBtn)),
    h('div', { class: 'card' }, h('h2', {}, 'Onboarding projects'),
      table(['Name', 'Progress', 'Status'], c.projects.map((p) => [link(p.name, '#/onboarding/' + p.id), h('div', { class: 'row' }, bar(p.progress), p.progress + '%'), badge(p.status)]))),
    h('div', { class: 'card' }, h('h2', {}, 'Activity timeline'), table(['When', 'Who', 'What'], timeline.map((a) => [fmtTime(a.created_at), a.user_name, a.summary]))),
  ], 'clients');
}

// ---------------------------------------------------------------- onboarding workspace

async function onboardingView(id) {
  const p = await api('GET', '/onboarding/' + id);
  const staff = STAFF(S.user);
  const rows = p.tasks.map((t) => [t.position + 1, link(t.title, '#/tasks/' + t.id), label(t.type) + (t.audience === 'agency' ? ' (internal)' : ''), t.is_required ? 'Required' : 'Optional', badge(t.status),
    h('span', { class: t.overdue ? 'err' : '' }, fmtDate(t.due_date))]);
  let addForm = null;
  if (staff) {
    const title = h('input', { required: true, maxlength: 200 });
    const type = h('select', {}, ['INFORMATION', 'FILE_UPLOAD', 'FORM', 'APPROVAL', 'INTERNAL_TASK', 'CLIENT_CONFIRMATION', 'SIGNATURE', 'QUESTIONNAIRE'].map((t) => h('option', { value: t }, label(t))));
    const due = h('input', { type: 'date' });
    const btn = h('button', { type: 'submit' }, 'Add task');
    addForm = h('form', { class: 'card', onsubmit: async (e) => {
      e.preventDefault();
      const ok = await act(btn, () => api('POST', '/onboarding/' + id + '/tasks', { title: title.value, type: type.value, due_date: due.value || null,
        audience: type.value === 'INTERNAL_TASK' ? 'agency' : 'client' }), 'Task added');
      if (ok) route();
    } }, h('h2', {}, 'Add a task'), h('div', { class: 'row' }, h('div', { class: 'grow' }, title), type, due, btn));
  }
  return shell([
    staff ? h('p', {}, link('< Client', '#/clients/' + p.client_id)) : null,
    h('h1', {}, p.name, ' ', badge(p.status)),
    h('div', { class: 'card' }, h('div', { class: 'row' }, h('div', { class: 'grow' }, bar(p.progress)), h('b', {}, p.progress + '%')),
      h('p', { class: 'small muted' }, 'Completed: ' + p.completed + ' · Pending: ' + p.pending + ' · Changes requested: ' + p.changes_requested + ' · Total: ' + p.total)),
    h('div', { class: 'card' }, table(['#', 'Task', 'Type', 'Need', 'Status', 'Due'], rows)),
    addForm,
  ], 'dashboard');
}

// ---------------------------------------------------------------- task detail

function renderFields(task, saved) {
  const inputs = [];
  const get = (n) => (saved && saved[n] != null ? saved[n] : '');
  let fields = (task.config && task.config.fields) || [];
  if (!fields.length) {
    if (task.type === 'CLIENT_CONFIRMATION') fields = [{ name: 'confirmed', label: 'I confirm that this is complete and correct', type: 'checkbox' }];
    else if (task.type === 'SIGNATURE') fields = [{ name: 'signature_name', label: 'Type your full name to sign', type: 'text' }];
    else if (['INFORMATION', 'FORM', 'QUESTIONNAIRE'].includes(task.type)) fields = [{ name: 'text', label: 'Your answer', type: 'textarea' }];
  }
  const nodes = fields.map((f) => {
    let el;
    if (f.type === 'textarea') { el = h('textarea', { rows: 4 }, get(f.name)); }
    else if (f.type === 'select') el = h('select', {}, h('option', { value: '' }, 'Choose...'), (f.options || []).map((o) => h('option', { value: o, selected: get(f.name) === o }, o)));
    else if (f.type === 'radio') {
      el = h('div', {}, (f.options || []).map((o) => h('label', { class: 'inline' }, h('input', { type: 'radio', name: 'f_' + f.name, value: o, checked: get(f.name) === o }), o)));
      inputs.push({ f, read: () => { const c = el.querySelector('input:checked'); return c ? c.value : ''; } });
      return field(f.label || f.name, el);
    } else if (f.type === 'checkbox') {
      el = h('input', { type: 'checkbox', checked: get(f.name) === true });
      inputs.push({ f, read: () => el.checked });
      return h('label', { class: 'inline' }, el, f.label || f.name);
    } else el = h('input', { type: ['email', 'tel', 'number', 'date', 'url'].includes(f.type) ? f.type : 'text', value: get(f.name) });
    inputs.push({ f, read: () => (f.type === 'number' && el.value !== '' ? Number(el.value) : el.value) });
    return field((f.label || f.name) + (f.required ? ' *' : ''), el);
  });
  return { nodes, collect: () => Object.fromEntries(inputs.map((i) => [i.f.name, i.read()])) };
}

async function downloadDoc(d) {
  const res = await fetch('/api/documents/' + d.id + '/download', { headers: { Authorization: 'Bearer ' + localStorage.getItem(TOKEN_KEY) } });
  if (!res.ok) throw new Error('Download failed');
  const url = URL.createObjectURL(await res.blob());
  const a = h('a', { href: url, download: d.file_name });
  document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 5000);
}

async function taskView(id) {
  const t = await api('GET', '/tasks/' + id);
  const staff = STAFF(S.user);
  const refresh = () => route();
  const comment = h('textarea', { rows: 3, placeholder: 'Comment (required when requesting changes)' });
  const actions = h('div', { class: 'card' }, h('h2', {}, 'Actions'));
  const decide = (kind, btnLabel, cls) => { const b = h('button', { class: cls, onclick: async () => {
    if (await act(b, () => api('POST', '/tasks/' + id + '/' + kind, { comment: comment.value }), 'Done')) refresh(); } }, btnLabel); return b; };

  const waitingOnClient = ['NOT_STARTED', 'IN_PROGRESS', 'CHANGES_REQUESTED'].includes(t.status);
  const inReview = ['SUBMITTED', 'RESUBMITTED', 'UNDER_REVIEW'].includes(t.status);
  if (t.status === 'APPROVED') put(actions, h('p', { class: 'muted' }, 'This task is approved. No further action needed.'));
  else if (!staff && t.type !== 'APPROVAL' && waitingOnClient) {
    const form = renderFields(t, t.response);
    const btn = h('button', { onclick: async () => {
      if (await act(btn, () => api('POST', '/tasks/' + id + '/submit', { content: form.collect(), comment: comment.value }), 'Submitted')) refresh(); } }, 'Submit');
    put(actions, ...form.nodes, t.type === 'FILE_UPLOAD' ? h('p', { class: 'small muted' }, 'Upload your file(s) below, then press Submit.') : null, comment, h('div', { class: 'row' }, btn));
  } else if (!staff && t.type === 'APPROVAL' && inReview) {
    put(actions, comment, h('div', { class: 'row' }, decide('approve', 'Approve', 'good'), decide('request-changes', 'Request changes', 'danger')));
  } else if (staff && t.type === 'APPROVAL' && waitingOnClient) {
    const deadline = h('input', { type: 'date' });
    const btn = h('button', { onclick: async () => {
      if (await act(btn, () => api('POST', '/approvals', { task_id: Number(id), comment: comment.value, deadline: deadline.value || null }), 'Approval requested')) refresh(); } }, 'Send to client for approval');
    put(actions, h('p', { class: 'small muted' }, 'Upload the item for approval below, then send it to the client.'), comment, field('Approval deadline (optional)', deadline), h('div', { class: 'row' }, btn));
  } else if (staff && t.audience === 'agency') {
    put(actions, h('div', { class: 'row' }, decide('approve', 'Mark complete', 'good')));
  } else if (staff && inReview && t.type !== 'APPROVAL') {
    put(actions, comment, h('div', { class: 'row' }, t.status !== 'UNDER_REVIEW' ? decide('review', 'Start review', 'secondary') : null,
      decide('approve', 'Approve', 'good'), decide('request-changes', 'Request changes', 'danger')));
  } else put(actions, h('p', { class: 'muted' }, staff ? 'Waiting for the client.' : 'Waiting for your agency.'));

  // documents
  const fileInput = h('input', { type: 'file' });
  const upBtn = h('button', { class: 'secondary', onclick: async () => {
    if (!fileInput.files.length) return toast('Choose a file first', true);
    const fd = new FormData(); fd.append('task_id', id); fd.append('file', fileInput.files[0]);
    if (await act(upBtn, () => api('POST', '/documents/upload', undefined, fd), 'Uploaded')) refresh();
  } }, 'Upload');
  const docs = h('div', { class: 'card' }, h('h2', {}, 'Files'),
    table(['File', 'Version', 'Size', 'Uploaded', ''], t.documents.map((d) => [d.file_name, 'v' + d.version, Math.ceil(d.file_size / 1024) + ' KB', fmtTime(d.created_at),
      h('div', { class: 'row' }, h('button', { class: 'secondary', onclick: () => act(null, () => downloadDoc(d)) }, 'Download'),
        t.status !== 'APPROVED' ? h('button', { class: 'secondary', onclick: async () => { if (confirm('Delete this file?') && await act(null, () => api('DELETE', '/documents/' + d.id), 'Deleted')) refresh(); } }, 'Delete') : null)])),
    t.status !== 'APPROVED' ? h('div', { class: 'row' }, fileInput, upBtn) : null,
    h('p', { class: 'small muted' }, 'Uploading a file with the same name creates a new version.'));

  // comments
  const body = h('textarea', { rows: 3, placeholder: 'Write a comment' });
  const internal = staff ? h('input', { type: 'checkbox' }) : null;
  const cBtn = h('button', { onclick: async () => {
    if (!body.value.trim()) return;
    if (await act(cBtn, () => api('POST', '/tasks/' + id + '/comments', { body: body.value, is_internal: internal ? internal.checked : false }))) refresh();
  } }, 'Post');
  const comments = h('div', { class: 'card' }, h('h2', {}, 'Comments'),
    t.comments.length ? t.comments.map((c) => h('div', { class: 'comment' + (c.is_internal ? ' internal' : '') },
      h('b', {}, c.user_name), c.is_internal ? h('span', { class: 'tag' }, ' INTERNAL NOTE (hidden from client)') : null, h('span', { class: 'muted small' }, ' · ' + fmtTime(c.created_at)),
      h('div', {}, c.body))) : h('p', { class: 'muted' }, 'No comments yet.'),
    body, h('div', { class: 'row' }, internal ? h('label', { class: 'inline' }, internal, 'Internal note (clients cannot see)') : null, cBtn));

  const history = h('div', { class: 'card' }, h('h2', {}, 'Approval history'),
    table(['When', 'Status', 'Comment'], t.approvals.map((a) => [fmtTime(a.created_at), badge(a.status), a.comment])));

  return shell([
    h('p', {}, link('< Back', '#/onboarding/' + t.project_id)),
    h('h1', {}, t.title, ' ', badge(t.status)),
    h('div', { class: 'card' }, h('p', {}, t.description || 'No description.'),
      h('p', { class: 'small muted' }, label(t.type) + ' · ' + (t.is_required ? 'Required' : 'Optional') + ' · Priority: ' + t.priority + ' · Due: ' + fmtDate(t.due_date))),
    actions, docs, comments, history,
  ], 'dashboard');
}

// ---------------------------------------------------------------- approvals, templates, team, audit, analytics, notifications, platform

async function approvalsView() {
  const list = await api('GET', '/approvals');
  return shell([h('h1', {}, 'Approvals'), h('div', { class: 'card' },
    table(['Item', 'Status', 'Requested', 'Deadline', 'Comment'], list.map((a) => [link(a.task_title, '#/tasks/' + a.task_id), badge(a.status), fmtTime(a.created_at), fmtDate(a.deadline), a.comment])))], 'approvals');
}

async function templatesView() {
  const list = await api('GET', '/templates');
  const admin = S.user.role === 'org_admin';
  const name = h('input', { required: true }); const desc = h('input');
  const rows = h('div');
  const addRow = () => {
    const title = h('input', { placeholder: 'Task title', required: true });
    const type = h('select', {}, ['INFORMATION', 'FILE_UPLOAD', 'FORM', 'APPROVAL', 'INTERNAL_TASK', 'CLIENT_CONFIRMATION', 'SIGNATURE', 'QUESTIONNAIRE'].map((t) => h('option', { value: t }, label(t))));
    const req = h('input', { type: 'checkbox', checked: true });
    const row = h('div', { class: 'row', 'data-row': '1' }, h('div', { class: 'grow' }, title), type, h('label', { class: 'inline' }, req, 'Required'));
    row._read = () => ({ title: title.value, type: type.value, is_required: req.checked, audience: type.value === 'INTERNAL_TASK' ? 'agency' : 'client' });
    rows.append(row);
  };
  addRow();
  const btn = h('button', { type: 'submit' }, 'Save template');
  const form = admin ? h('form', { class: 'card', onsubmit: async (e) => {
    e.preventDefault();
    if (await act(btn, () => api('POST', '/templates', { name: name.value, description: desc.value, tasks: [...rows.children].map((r) => r._read()) }), 'Template saved')) route();
  } }, h('h2', {}, 'New template'), field('Name', name), field('Description', desc), h('h3', {}, 'Tasks'), rows,
  h('div', { class: 'row' }, h('button', { type: 'button', class: 'secondary', onclick: addRow }, 'Add task'), btn)) : null;
  return shell([h('h1', {}, 'Onboarding templates'), h('div', { class: 'card' },
    table(['Template', 'Description', 'Tasks', ''], list.map((t) => [t.name, t.description, t.task_count,
      admin ? h('button', { class: 'secondary', onclick: async () => { if (confirm('Delete template "' + t.name + '"?') && await act(null, () => api('DELETE', '/templates/' + t.id), 'Deleted')) route(); } }, 'Delete') : '']))), form], 'templates');
}

async function teamView() {
  const list = await api('GET', '/team');
  const admin = S.user.role === 'org_admin';
  const f = { name: h('input', { required: true }), email: h('input', { type: 'email', required: true }), pw: h('input', { type: 'password', minlength: 8, maxlength: 64, required: true }),
    role: h('select', {}, h('option', { value: 'team_member' }, 'Team member'), h('option', { value: 'org_admin' }, 'Admin')) };
  const btn = h('button', { type: 'submit' }, 'Add member');
  const form = admin ? h('form', { class: 'card', onsubmit: async (e) => {
    e.preventDefault();
    if (await act(btn, () => api('POST', '/team', { name: f.name.value, email: f.email.value, password: f.pw.value, role: f.role.value }), 'Member added')) route();
  } }, h('h2', {}, 'Add team member'), field('Name', f.name), field('Email', f.email), field('Temporary password', f.pw), field('Role', f.role), h('div', { class: 'row' }, btn)) : null;
  return shell([h('h1', {}, 'Team'), h('div', { class: 'card' }, table(['Name', 'Email', 'Role', 'Status', ''], list.map((u) => [u.name, u.email, label(u.role), badge(u.status),
    admin && u.id !== S.user.id ? h('button', { class: 'secondary', onclick: async () => { if (await act(null, () => api('PATCH', '/team/' + u.id, { status: u.status === 'active' ? 'disabled' : 'active' }), 'Updated')) route(); } }, u.status === 'active' ? 'Disable' : 'Enable') : '']))), form], 'team');
}

async function auditView() {
  const logs = await api('GET', '/audit-logs?limit=200');
  return shell([h('h1', {}, 'Audit log'), h('div', { class: 'card' }, table(['When', 'User', 'Action', 'Details', 'IP'], logs.map((a) => [fmtTime(a.created_at), a.user_name, a.action, a.summary, a.ip_address])))], 'audit');
}

async function analyticsView() {
  const a = await api('GET', '/analytics');
  const v = (x, s = '') => (x == null ? 'n/a' : x + s);
  return shell([h('h1', {}, 'Analytics'),
    h('div', { class: 'grid' }, statCard(v(a.average_onboarding_days, ' d'), 'Avg onboarding time'), statCard(a.completion_rate + '%', 'Completion rate'),
      statCard(a.task_completion_rate + '%', 'Task completion'), statCard(v(a.average_approval_hours, ' h'), 'Avg approval time'), statCard(a.changes_requested_total, 'Changes requested')),
    h('div', { class: 'card' }, h('h2', {}, 'Bottlenecks (most changes requested)'), table(['Task', 'Times'], a.bottlenecks.map((b) => [b.title, b.changes_requested]))),
    h('div', { class: 'card' }, h('h2', {}, 'Most delayed tasks'), table(['Task', 'Overdue'], a.most_delayed_tasks.map((b) => [b.title, b.count]))),
    h('div', { class: 'card' }, h('h2', {}, 'By template'), table(['Template', 'Total', 'Completed'], a.by_template.map((b) => [b.template, b.total, b.completed]))),
  ], 'analytics');
}

async function notificationsView() {
  const n = await api('GET', '/notifications');
  const btn = h('button', { class: 'secondary', onclick: async () => { if (await act(btn, () => api('POST', '/notifications/read-all'))) route(); } }, 'Mark all as read');
  return shell([h('h1', {}, 'Notifications (' + n.unread + ' unread)'), h('div', { class: 'row' }, btn),
    h('div', { class: 'card' }, n.items.length ? n.items.map((i) => h('div', { class: 'comment' }, h('b', {}, i.title, i.read ? '' : ' •'), h('div', { class: 'small' }, i.message), h('div', { class: 'small muted' }, fmtTime(i.created_at)))) : h('p', { class: 'muted' }, 'No notifications.'))], 'notifications');
}

async function platformView() {
  const [stats, orgs] = await Promise.all([api('GET', '/platform/stats'), api('GET', '/platform/organizations')]);
  return shell([h('h1', {}, 'Platform'), h('div', { class: 'grid' }, Object.entries(stats).map(([k, v]) => statCard(v, label(k)))),
    h('div', { class: 'card' }, table(['Organization', 'Plan', 'Users', 'Clients', 'Created'], orgs.map((o) => [o.name, o.plan, o.users, o.clients, fmtDate(o.created_at)])))], 'platform');
}

// ---------------------------------------------------------------- router

function parseHash() {
  const raw = location.hash.replace(/^#\/?/, '');
  const [path, qs] = raw.split('?');
  return { parts: path.split('/').filter(Boolean), query: Object.fromEntries(new URLSearchParams(qs || '')) };
}

async function route() {
  const app = document.getElementById('app');
  const { parts, query } = parseHash();
  const first = parts[0] || '';
  try {
    if (['login', 'register', 'forgot', 'accept-invite', 'reset-password', 'verify-email'].includes(first)) {
      const pub = { login: loginView, register: registerView, forgot: forgotView,
        'accept-invite': () => tokenPasswordView('Accept your invitation', '/auth/accept-invite', 'password', query),
        'reset-password': () => tokenPasswordView('Reset password', '/auth/reset-password', 'new_password', query),
        'verify-email': () => verifyView(query) };
      return app.replaceChildren(await pub[first]());
    }
    if (!localStorage.getItem(TOKEN_KEY)) { location.hash = '#/login'; return app.replaceChildren(loginView()); }
    if (!S.user) S.user = await api('GET', '/auth/me');
    const id = parts[1];
    let view;
    if (first === 'clients' && id) view = clientDetailView(id);
    else if (first === 'onboarding' && id) view = onboardingView(id);
    else if (first === 'tasks' && id) view = taskView(id);
    else {
      const map = { clients: clientsView, approvals: approvalsView, templates: templatesView, team: teamView, audit: auditView,
        analytics: analyticsView, notifications: notificationsView, platform: platformView };
      view = (map[first] || (S.user.role === 'platform_admin' ? platformView : dashboardView))();
    }
    app.replaceChildren(await view);
  } catch (e) {
    app.replaceChildren(h('div', { class: 'center' }, h('div', { class: 'card' }, h('h1', {}, 'Something went wrong'), h('p', { class: 'err' }, e.message),
      h('p', {}, link('Back to dashboard', '#/dashboard')))));
  }
}

window.addEventListener('hashchange', route);
window.addEventListener('DOMContentLoaded', route);
if (document.readyState !== 'loading') route();
