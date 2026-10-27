'use strict';
const $ = id => document.getElementById(id);
let current = null;
let running = false;
let timer;
let toastTimer;
let workspace = null;
let selectedPayment = null;
let paymentsAsOf = null;
let sessionVersion = 0;
let investigationController = null;
let paymentsController = null;
let activeView = 'investigations';
let paymentRecords = [];
let paymentsLoaded = false;
let paymentsLoading = false;
const history = [];
let asyncMode = false;
let authRequired = false;
let password = sessionStorage.getItem('paymentdesk-password') || '';
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
function authHeaders() { return password ? { Authorization: 'Bearer ' + password } : {}; }
function askForPassword() { if (!$('access-dialog').open) $('access-dialog').showModal(); }
async function apiFetch(url, options = {}) {
  const response = await fetch(url, { ...options, headers: { ...authHeaders(), ...options.headers } });
  if (response.status === 401) {
    lockWorkspace();
    throw new Error('Your workspace is locked. Enter your access code to continue.');
  }
  return response;
}
const buttons = [...document.querySelectorAll('.example')];
function updateCount() { $('character-count').textContent = `${$('prompt').value.length.toLocaleString()} / 10,000`; }
function showState(state) {
  for (const name of ['empty-state', 'loading-state', 'error-state', 'report-result']) $(name).hidden = name !== state;
  $('report-actions').hidden = state !== 'report-result';
  $('report-panel').setAttribute('aria-busy', String(state === 'loading-state'));
}
function setRunning(value) {
  running = value;
  for (const button of [$('submit'), $('new-investigation'), $('retry'), ...buttons, ...document.querySelectorAll('.history-item')]) button.disabled = value;
  $('prompt').readOnly = value;
  $('submit-label').textContent = value ? 'Investigating…' : 'Start investigation';
  $('submit-icon').textContent = value ? '◌' : '↗';
}
function toast(message) {
  clearTimeout(toastTimer);
  $('toast').textContent = message;
  $('toast').hidden = false;
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, 3000);
}
function renderReport(report) {
  current = report;
  // Only use HTML produced by our backend's Markdown parser with raw HTML disabled.
  $('answer').innerHTML = report.answer_html;
  $('result-time').textContent = new Date(report.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  $('result-duration').textContent = `${Number(report.elapsed_seconds).toFixed(1)}s`;
  $('request-reference').textContent = `Request ${report.request_id}`;
  showState('report-result');
  renderHistory();
}
function renderHistory() {
  $('history-count').textContent = String(history.length).padStart(2, '0');
  $('history').replaceChildren();
  if (!history.length) {
    const message = document.createElement('p'); message.className = 'history-empty'; message.textContent = 'No investigations yet.'; $('history').append(message);
  }
  for (const item of history) {
    const button = document.createElement('button');
    button.className = 'history-item' + (current?.request_id === item.request_id ? ' selected' : '');
    button.textContent = item.prompt; button.title = item.prompt; button.disabled = running;
    const time = document.createElement('small'); time.textContent = new Date(item.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); button.append(time);
    button.addEventListener('click', () => { if (running) return; showView('investigations'); $('prompt').value = item.prompt; updateCount(); renderReport(item); });
    $('history').append(button);
  }
}
async function submit(event) {
  event?.preventDefault();
  if (running) return;
  await ready;
  if (authRequired && !password) { askForPassword(); return; }
  const prompt = $('prompt').value.trim();
  if (!prompt) { $('prompt').focus(); toast('Enter a question to investigate.'); return; }
  const version = sessionVersion;
  setRunning(true); showState('loading-state');
  if (window.matchMedia('(max-width:950px)').matches) window.scrollTo({ top: window.scrollY + $('report-panel').getBoundingClientRect().top - 20, behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' });
  const start = performance.now();
  $('elapsed').textContent = '0 seconds elapsed';
  timer = setInterval(() => { $('elapsed').textContent = `${Math.floor((performance.now() - start) / 1000)} seconds elapsed`; }, 1000);
  const controller = new AbortController(); investigationController = controller;
  const deadline = setTimeout(() => controller.abort(), 600000);
  try {
    const response = await apiFetch('/api/investigations', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(asyncMode ? { prompt, request_id: crypto.randomUUID() } : { prompt }), signal: controller.signal });
    let data = await response.json();
    if (!response.ok) {
      throw new Error(typeof data.detail === 'string' ? data.detail : 'The question was rejected. Use between 1 and 10,000 characters.');
    }
    if (response.status === 202 && data.job_id) {
      const jobId = data.job_id;
      let polls = 0;
      while (true) {
        await pause(Math.min(2000 + polls * 200, 5000)); polls++;
        const result = await apiFetch('/api/investigations/' + encodeURIComponent(jobId), { signal: controller.signal });
        data = await result.json();
        if (!result.ok) throw new Error(data.detail || 'The investigation result could not be retrieved.');
        if (data.status === 'failed') throw new Error(data.detail || 'The agent could not complete this investigation.');
        if (data.status === 'completed') break;
      }
    }
    if (version !== sessionVersion) return;
    if (typeof data.answer !== 'string' || typeof data.answer_html !== 'string') throw new Error('The backend returned an unexpected response.');
    const report = { ...data, prompt };
    history.unshift(report); if (history.length > 10) history.pop();
    renderReport(report);
  } catch (error) {
    if (version !== sessionVersion) return;
    $('error-message').textContent = error.name === 'AbortError' ? 'The browser stopped waiting after ten minutes. The remote investigation may still be running. Check the agent logs before trying again.' : error instanceof TypeError ? 'The backend could not be reached. Check your connection and try again.' : error.message;
    showState('error-state');
  } finally {
    clearTimeout(deadline);
    if (version === sessionVersion) { clearInterval(timer); setRunning(false); investigationController = null; }
  }
}
$('query-form').addEventListener('submit', submit);
$('retry').addEventListener('click', submit);
$('prompt').addEventListener('input', updateCount);
$('prompt').addEventListener('keydown', event => { if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') { event.preventDefault(); $('query-form').requestSubmit(); } });
buttons.forEach(button => button.addEventListener('click', () => { let text = button.dataset.prompt; if (text.includes('{payment_id}')) { if (!selectedPayment) { toast('Select a payment to investigate.'); showView('payments'); return; } text = text.replaceAll('{payment_id}', selectedPayment.payment_id); } $('prompt').value = text; updateCount(); $('prompt').focus(); }));
$('new-investigation').addEventListener('click', () => { if (running) return; showView('investigations'); current = null; $('prompt').value = ''; updateCount(); showState('empty-state'); renderHistory(); $('prompt').focus(); });
$('copy').addEventListener('click', async () => { if (!current) return; try { await navigator.clipboard.writeText(current.answer); toast('Report copied to clipboard.'); } catch { toast('Clipboard unavailable. Select and copy the report text.'); } });
$('download').addEventListener('click', () => {
  if (!current) return;
  const content = `# Payment investigation\n\nQuestion: ${current.prompt}\n\nCreated: ${current.created_at}\n\nRequest: ${current.request_id}\n\n${current.answer}\n`;
  const url = URL.createObjectURL(new Blob([content], { type: 'text/markdown;charset=utf-8' }));
  const link = document.createElement('a'); link.href = url; link.download = `investigation-${current.request_id.slice(0, 8)}.md`; document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
});
async function checkHealth() {
  try {
    const response = await fetch('/api/health'); if (!response.ok) throw new Error();
    const data = await response.json();
    asyncMode = data.mode === 'async'; authRequired = Boolean(data.auth_required);
    $('backend-status').textContent = data.status === 'ready' ? 'Service available' : 'Service configuration required';
    $('backend-dot').className = 'backend-dot ' + (data.status === 'ready' ? 'ready' : 'error');
    $('region').textContent = data.region === 'ap-southeast-2' ? 'Sydney' : data.region;
    $('workspace-access').hidden = !authRequired || !password;
    $('workspace-lock').hidden = !authRequired;
    if (authRequired && !password) askForPassword();
    else {
      const access = await apiFetch('/api/access');
      if (access.ok) applyWorkspace((await access.json()).workspace);
    }
  } catch {
    if (!authRequired || password) { $('backend-status').textContent = 'Service unavailable'; $('backend-dot').className = 'backend-dot error'; }
  }
}
$('workspace-access').addEventListener('click', lockWorkspace);
$('access-form').addEventListener('submit', async event => {
  event.preventDefault();
  $('access-submit').disabled = true;
  $('access-error').textContent = '';
  const candidate = $('workspace-password').value.trim();
  try {
    const response = await fetch('/api/access', { headers: { Authorization: 'Bearer ' + candidate } });
    if (!response.ok) throw new Error('That access code was not accepted.');
    const access = await response.json(); applyWorkspace(access.workspace);
    password = candidate; $('workspace-access').hidden = false; sessionStorage.setItem('paymentdesk-password', password);
    $('workspace-password').value = ''; $('access-dialog').close(); toast('Workspace unlocked.');
    if (activeView === 'payments') loadPayments();
  } catch (error) { $('access-error').textContent = error.message; }
  finally { $('access-submit').disabled = false; }
});
updateCount();
const ready = checkHealth();

function showView(view) {
  activeView = view === 'payments' ? 'payments' : 'investigations';
  $('payments-view').hidden = activeView !== 'payments';
  $('investigations-view').hidden = activeView !== 'investigations';
  $('current-screen').textContent = activeView === 'payments' ? 'Payments' : 'Investigations';
  document.title = 'Payment desk · ' + $('current-screen').textContent;
  for (const name of ['payments', 'investigations']) {
    $('nav-' + name).classList.toggle('nav-active', activeView === name);
    if (activeView === name) $('nav-' + name).setAttribute('aria-current', 'page');
    else $('nav-' + name).removeAttribute('aria-current');
  }
  window.history.replaceState(null, '', '#' + activeView);
  if (activeView === 'payments' && !paymentsLoaded) loadPayments();
}
function formatUtc(value) {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return '—';
  return date.toISOString().slice(0, 19).replace('T', ' ');
}
function renderPayments() {
  const filtered = filteredPayments();
  updatePaymentControls();
  $('payments-rows').replaceChildren();
  for (const payment of filtered) {
    const row = document.createElement('tr');
    function cell(text) { const td = document.createElement('td'); td.textContent = text; row.append(td); return td; }
    const reference = document.createElement('button'); reference.className = 'payment-reference'; reference.textContent = payment.payment_id; reference.setAttribute('aria-label', 'View ' + payment.payment_id); reference.addEventListener('click', () => openPaymentDetails(payment)); cell('').append(reference);
    const amount = cell(Number(payment.amount).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
    const currency = document.createElement('span'); currency.className = 'payment-currency'; currency.textContent = payment.currency; amount.append(currency);
    const badge = document.createElement('span'); badge.className = 'payment-status';
    if (['PENDING','SETTLED','REJECTED','FAILED'].includes(payment.status)) badge.classList.add(payment.status.toLowerCase());
    badge.textContent = payment.status; cell('').append(badge);
    cell(formatUtc(payment.created_at)); cell(formatUtc(payment.updated_at));
    const button = document.createElement('button'); button.className = 'investigate-payment'; button.textContent = 'Investigate ↗'; button.setAttribute('aria-label', 'Investigate ' + payment.payment_id);
    button.addEventListener('click', () => openInvestigation(payment));
    cell('').append(button); $('payments-rows').append(row);
  }
  $('payments-count').textContent = `${filtered.length} of ${paymentRecords.length} payments`;
  $('payments-empty').hidden = filtered.length !== 0;
  $('payments-table-wrap').hidden = filtered.length === 0;
}
async function loadPayments() {
  if (paymentsLoading) return;
  await ready;
  if (authRequired && !password) { askForPassword(); return; }
  const version = sessionVersion;
  paymentsLoading = true; $('payments-count').textContent = 'Retrieving workspace records…'; $('refresh-payments').disabled = true; $('export-payments').disabled = true; $('payments-error').hidden = true;
  $('payments-table-wrap').hidden = true; $('payments-empty').hidden = true; $('payments-loading').hidden = false; updatePaymentControls();
  const controller = new AbortController(); paymentsController = controller; const deadline = setTimeout(() => controller.abort(), 420000);
  try {
    let response = await apiFetch('/api/payments', { signal: controller.signal });
    let data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Payment records could not be retrieved.');
    if (response.status === 202) {
      const jobId = data.job_id;
      while (true) {
        await pause(3000);
        response = await apiFetch('/api/investigations/' + encodeURIComponent(jobId), { signal: controller.signal }); data = await response.json();
        if (!response.ok || data.status === 'failed') throw new Error(data.detail || 'Payment records could not be retrieved.');
        if (data.status === 'completed') break;
      }
    }
    if (!Array.isArray(data.payments)) throw new Error('The payment listing returned an unexpected response.');
    if (version !== sessionVersion) return;
    if (workspace && data.tenant !== workspace.id) throw new Error('The records do not match your active workspace.');
    paymentsAsOf = data.as_of; paymentRecords = data.payments; paymentsLoaded = true;
    $('metric-total').textContent = paymentRecords.length;
    for (const status of ['PENDING','SETTLED','REJECTED']) $('metric-' + status.toLowerCase()).textContent = paymentRecords.filter(p => p.status === status).length;
    $('payments-nav-count').textContent = paymentRecords.length;
    $('payment-tenant').textContent = data.tenant;
    $('payments-updated').textContent = 'Fetched ' + formatUtc(data.as_of) + ' UTC';
    const selected = $('payment-status-filter').value;
    $('payment-status-filter').replaceChildren(new Option('All statuses', ''));
    [...new Set(paymentRecords.map(p => p.status))].sort().forEach(value => $('payment-status-filter').append(new Option(value, value)));
    $('payment-status-filter').value = [...$('payment-status-filter').options].some(o => o.value === selected) ? selected : '';
    renderPayments();
  } catch (error) {
    if (version !== sessionVersion) return;
    $('payments-error').hidden = false;
    $('payments-error-message').textContent = error.name === 'AbortError' ? 'Loading took too long. Refresh to try again.' : error.message;
  } finally {
    clearTimeout(deadline);
    if (version === sessionVersion) { paymentsLoading = false; paymentsController = null; $('refresh-payments').disabled = false; $('payments-loading').hidden = true; $('export-payments').disabled = !paymentsLoaded || !filteredPayments().length; updatePaymentControls(); }
  }
}
$('nav-payments').addEventListener('click', () => showView('payments'));
$('nav-investigations').addEventListener('click', () => showView('investigations'));
$('refresh-payments').addEventListener('click', loadPayments);
$('retry-payments').addEventListener('click', loadPayments);
$('payment-search').addEventListener('input', renderPayments);
$('payment-status-filter').addEventListener('change', renderPayments);
ready.then(() => showView(window.location.hash === '#payments' ? 'payments' : 'investigations'));

function applyWorkspace(context) {
  if (!context || typeof context.id !== 'string' || typeof context.name !== 'string') throw new Error('Workspace information is unavailable.');
  workspace = context;
  $('workspace-sidebar-name').textContent = context.name;
  $('workspace-header').textContent = context.name;
  $('workspace-dialog-name').textContent = context.name;
  $('workspace-id').textContent = context.id;
  $('workspace-region').textContent = context.region === 'ap-southeast-2' ? 'Sydney · ap-southeast-2' : context.region;
  $('workspace-environment').textContent = context.environment;
  $('workspace-retention').textContent = `${context.report_retention_hours} hours · investigation reports`;
  $('environment-label').textContent = context.environment === 'Test' ? 'Test environment' : context.environment;
}
function lockWorkspace() {
  sessionVersion++;
  investigationController?.abort(); paymentsController?.abort(); clearInterval(timer);
  password = ''; sessionStorage.removeItem('paymentdesk-password');
  history.length = 0; paymentRecords = []; paymentsLoaded = false; paymentsLoading = false;
  current = null; selectedPayment = null; paymentsAsOf = null;
  $('answer').replaceChildren(); $('payments-rows').replaceChildren();
  $('prompt').value = ''; updateCount(); renderHistory(); showState('empty-state'); setRunning(false);
  for (const id of ['metric-total','metric-pending','metric-settled','metric-rejected']) $(id).textContent = '—';
  for (const id of ['detail-payment-id','detail-amount','detail-status','detail-created','detail-updated','detail-fetched','detail-workspace']) $(id).textContent = '—';
  for (const id of ['workspace-dialog','payment-dialog']) if ($(id).open) $(id).close();
  $('payments-nav-count').textContent = ''; $('payments-updated').textContent = ''; $('payments-count').textContent = 'Workspace locked';
  $('payments-table-wrap').hidden = true; $('payments-loading').hidden = true; $('payments-error').hidden = true;
  $('export-payments').disabled = true; $('refresh-payments').disabled = false; $('workspace-access').hidden = true;
  $('workspace-password').value = ''; $('access-error').textContent = '';
  askForPassword();
}
function filteredPayments() {
  const query = $('payment-search').value.trim().toUpperCase();
  const status = $('payment-status-filter').value;
  const rows = paymentRecords.filter(p => p.payment_id.toUpperCase().includes(query) && (!status || p.status === status));
  const sort = $('payment-sort').value;
  rows.sort((a,b) => sort === 'amount_desc' ? Number(b.amount) - Number(a.amount) : sort === 'amount_asc' ? Number(a.amount) - Number(b.amount) : sort === 'updated' ? new Date(b.updated_at) - new Date(a.updated_at) || a.payment_id.localeCompare(b.payment_id) : a.payment_id.localeCompare(b.payment_id));
  return rows;
}
function updatePaymentControls() {
  const active = $('payment-status-filter').value;
  for (const button of document.querySelectorAll('[data-payment-view]')) {
    button.setAttribute('aria-pressed', String(button.dataset.paymentView === active));
    button.disabled = paymentsLoading;
  }
  for (const id of ['payment-search','payment-status-filter','payment-sort']) $(id).disabled = paymentsLoading;
  $('clear-payment-filters').hidden = !$('payment-search').value && !active && $('payment-sort').value === 'id';
  $('clear-payment-filters').disabled = paymentsLoading;
  $('export-payments').disabled = paymentsLoading || !paymentsLoaded || !filteredPayments().length;
}
function openInvestigation(payment) {
  if (running) { toast('Wait for the current investigation to finish.'); return; }
  if ($('payment-dialog').open) $('payment-dialog').close();
  selectedPayment = payment;
  showView('investigations'); current = null; showState('empty-state'); renderHistory();
  $('prompt').value = 'Investigate payment ' + payment.payment_id + '. What happened?'; updateCount(); $('prompt').focus();
}
function openPaymentDetails(payment) {
  selectedPayment = payment;
  $('detail-payment-id').textContent = payment.payment_id;
  $('detail-amount').textContent = Number(payment.amount).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' ' + payment.currency;
  const status = $('detail-status'); status.className = 'payment-status';
  if (['PENDING','SETTLED','REJECTED','FAILED'].includes(payment.status)) status.classList.add(payment.status.toLowerCase());
  status.textContent = payment.status;
  $('detail-workspace').textContent = workspace?.name || 'Current workspace';
  $('detail-created').textContent = formatUtc(payment.created_at);
  $('detail-updated').textContent = formatUtc(payment.updated_at);
  $('detail-fetched').textContent = formatUtc(paymentsAsOf);
  $('payment-dialog').showModal();
}
function csvCell(value) {
  let text = String(value ?? '');
  if (/^[=+@-]/.test(text)) text = "'" + text;
  return '"' + text.replaceAll('"', '""') + '"';
}
function exportPayments() {
  const rows = filteredPayments(); if (!rows.length || !workspace) return;
  const records = [['Workspace ID','Payment ID','Amount','Currency','Recorded status','Created UTC','Updated UTC'], ...rows.map(p => [workspace.id,p.payment_id,p.amount,p.currency,p.status,formatUtc(p.created_at),formatUtc(p.updated_at)])];
  const csv = '\ufeff' + records.map(row => row.map(csvCell).join(',')).join('\r\n');
  const url = URL.createObjectURL(new Blob([csv], {type:'text/csv;charset=utf-8'}));
  const link = document.createElement('a'); link.href = url; link.download = `payments-${workspace.id}-${new Date().toISOString().slice(0,10)}.csv`;
  document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url),1000);
  toast(`${rows.length} payments exported.`);
}
for (const id of ['workspace-details','workspace-header']) $(id).addEventListener('click', () => { if (workspace) $('workspace-dialog').showModal(); });
$('workspace-lock').addEventListener('click', lockWorkspace);
for (const button of document.querySelectorAll('[data-close-dialog]')) button.addEventListener('click', () => $(button.dataset.closeDialog).close());
$('detail-investigate').addEventListener('click', () => { if (selectedPayment) openInvestigation(selectedPayment); });
for (const button of document.querySelectorAll('[data-payment-view]')) button.addEventListener('click', () => { $('payment-status-filter').value = button.dataset.paymentView; renderPayments(); });
$('payment-sort').addEventListener('change', renderPayments);
$('clear-payment-filters').addEventListener('click', () => { $('payment-search').value = ''; $('payment-status-filter').value = ''; $('payment-sort').value = 'id'; renderPayments(); });
$('export-payments').addEventListener('click', exportPayments);
