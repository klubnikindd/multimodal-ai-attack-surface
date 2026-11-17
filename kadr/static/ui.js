'use strict';
const $ = (id) => document.getElementById(id);
let token = '';
let csrf = '';

function setBadge(text) { $('badge').textContent = text; }

async function api(path, options = {}) {
  const headers = Object.assign({}, options.headers || {});
  if (token) headers['Authorization'] = 'Bearer ' + token;
  if (options.method && options.method !== 'GET') headers['X-Kadr-CSRF'] = csrf;
  if (options.body) headers['Content-Type'] = 'application/json';
  const response = await fetch(path, Object.assign({}, options, { headers }));
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || ('HTTP ' + response.status));
  return data;
}

async function connect() {
  token = $('token').value.trim();
  csrf = '';
  try {
    const status = await api('/v1/system/status');
    csrf = status.csrf_token || '';
    setBadge('подключено · ' + status.profile.profile);
    $('status').textContent = JSON.stringify(status, null, 2);
  } catch (error) {
    setBadge('ошибка');
    $('status').textContent = String(error);
  }
}

$('connect').addEventListener('click', connect);
$('search-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const list = $('results');
  list.replaceChildren();
  try {
    const data = await api('/v1/search', { method: 'POST', body: JSON.stringify({ query: $('query').value, k: 30 }) });
    $('index-badge').textContent = data.index_badge;
    for (const hit of data.hits) {
      const li = document.createElement('li');
      const path = hit.fields.path || '(нет пути)';
      li.textContent = '#' + hit.asset_id + ' · ' + hit.score.toFixed(3) + ' · ' + hit.reason + ' · ' + path;
      list.appendChild(li);
    }
    if (!data.hits.length) {
      const li = document.createElement('li');
      li.textContent = 'Ничего не найдено.';
      list.appendChild(li);
    }
  } catch (error) {
    $('index-badge').textContent = String(error);
  }
});

$('plan-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const data = await api('/v1/plans', { method: 'POST', body: JSON.stringify({ preset: $('preset').value, library_root: $('root').value }) });
    $('plan').textContent = data.tree_text + '\n\n' + JSON.stringify(data.plan.summary, null, 2);
  } catch (error) {
    $('plan').textContent = String(error);
  }
});

