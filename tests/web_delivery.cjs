/* Behavioral checks for the real browser scripts with small DOM/network doubles.
 * These complement native browser QA; they do not claim layout/browser coverage. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'rocky/static/index.html'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };

function environment() {
  const ids = new Map();
  const windowEvents = new Map();
  const requests = [];
  const streams = [];
  const clipboard = { writes: [], denied: false };
  const replies = new Map();
  let cookies = 'rocky_token=restored-session';
  let activeElement = null;
  class Element {
    constructor(tag = 'div', id = '') {
      this.tagName = tag.toUpperCase(); this.id = id; this.value = ''; this.children = [];
      this.events = new Map(); this.attrs = new Map(); this.classes = new Set();
      this.style = {}; this.dataset = {}; this.hidden = false; this.disabled = false;
      this.open = false; this._text = ''; this._html = ''; this.htmlWrites = [];
      this.classList = {
        add: (...names) => names.forEach(n => this.classes.add(n)),
        remove: (...names) => names.forEach(n => this.classes.delete(n)),
        contains: n => this.classes.has(n),
        toggle: (n, force) => {
          const on = force === undefined ? !this.classes.has(n) : force;
          if (on) this.classes.add(n); else this.classes.delete(n);
          return on;
        },
      };
      this.contentWindow = { messages: [], postMessage(message, target) { this.messages.push({ message, target }); } };
    }
    set className(v) { this.classes = new Set(v.split(/\s+/).filter(Boolean)); }
    get className() { return [...this.classes].join(' '); }
    set textContent(v) { this._text = String(v ?? ''); this.children = []; }
    get textContent() { return this._text + this.children.map(c => typeof c === 'string' ? c : c.textContent).join(''); }
    set innerHTML(v) { this.htmlWrites.push(v); this._html = v; this._text = ''; this.children = []; }
    get innerHTML() { return this._html; }
    append(...items) { this.children.push(...items); }
    appendChild(item) { this.children.push(item); return item; }
    prepend(item) { this.children.unshift(item); }
    replaceChildren(...items) { this._text = ''; this.children = items; }
    setAttribute(name, value) { this.attrs.set(name, String(value)); if (name === 'class') this.className = value; }
    getAttribute(name) { return this.attrs.get(name) ?? null; }
    removeAttribute(name) { this.attrs.delete(name); if (name === 'src' || name === 'href') delete this[name]; }
    addEventListener(name, fn) { if (!this.events.has(name)) this.events.set(name, []); this.events.get(name).push(fn); }
    async dispatch(name, event = {}) {
      for (const fn of this.events.get(name) || []) await fn({ preventDefault() {}, target: this, ...event });
    }
    focus() { activeElement = this; }
    scrollIntoView() { this.scrolled = true; }
  }
  for (const match of html.matchAll(/<([\w-]+)\b([^>]*\bid="([^"]+)"[^>]*)>/g)) {
    const [, tag, attrs, id] = match;
    assert(!ids.has(id), `duplicate id ${id}`);
    const element = new Element(tag, id);
    const classes = attrs.match(/class="([^"]*)"/);
    if (classes) element.className = classes[1];
    element.hidden = /\bhidden(?:\s|$)/.test(attrs);
    element.disabled = /\bdisabled(?:\s|$)/.test(attrs);
    element.open = /\bopen(?:\s|$)/.test(attrs);
    const value = attrs.match(/value="([^"]*)"/);
    if (value) element.value = value[1];
    ids.set(id, element);
  }
  const get = id => { assert(ids.has(id), `missing real DOM id: ${id}`); return ids.get(id); };
  const strips = ['critic', 'planner', 'worker', 'verifier'].map(role => {
    const e = new Element('article'); e.className = 'strip'; e.dataset.role = role; return e;
  });
  function descendants(element, tag) {
    return element.children.filter(c => typeof c !== 'string').flatMap(c => [
      ...(c.tagName === tag.toUpperCase() ? [c] : []), ...descendants(c, tag),
    ]);
  }
  const document = {
    getElementById: get,
    createElement: tag => new Element(tag),
    querySelectorAll: selector => {
      if (selector === '.strip') return strips;
      if (selector === '#file-list button') return descendants(get('file-list'), 'button');
      throw new Error(`unimplemented selector ${selector}`);
    },
    get cookie() { return cookies; },
    set cookie(value) { cookies = value; },
  };
  class EventSource {
    constructor(url) { this.url = url; this.events = {}; this.closed = false; streams.push(this); }
    addEventListener(name, fn) { this.events[name] = fn; }
    close() { this.closed = true; }
    message(type, payload = {}) { this.onmessage({ data: JSON.stringify({ type, payload }) }); }
    end() { this.events.end(); }
  }
  const context = vm.createContext({
    document, EventSource, URLSearchParams, console, setTimeout, clearTimeout,
    window: { addEventListener: (name, fn) => windowEvents.set(name, fn) },
    navigator: { clipboard: { writeText: async text => {
      if (clipboard.denied) throw new Error('denied');
      clipboard.writes.push(text);
    } } },
    localStorage: { data: new Map(), getItem(key) { return this.data.get(key) ?? null; }, setItem(key, value) { this.data.set(key, value); } },
    fetch: async (url, options = {}) => {
      requests.push({ url, options });
      let response = replies.get(url);
      if (typeof response === 'function') response = response(url, options);
      response = await response;
      if (!response) {
        if (url === '/api/meta') response = { body: { needs_token: false, workspaces: ['/campaign-a'] } };
        else if (url.startsWith('/api/runs?')) response = { body: [] };
        else if (url.startsWith('/api/playbook/search')) response = { body: { topics: [], results: [] } };
        else if (url.startsWith('/api/playbook/preview')) response = { body: { items: [] } };
        else if (url.includes('/artifacts?')) response = { body: { files: [], scope: 'current_workspace' } };
        else if (/\/api\/runs\/[^/?]+\?/.test(url)) response = { body: { status: 'running', goal: 'Selected task' } };
        else response = { body: { ok: true } };
      }
      const status = response.status || 200;
      return { ok: status < 400, status, json: async () => response.body };
    },
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'rocky/static/delivery.js'), 'utf8'), context);
  vm.runInContext(fs.readFileSync(path.join(root, 'rocky/static/app.js'), 'utf8'), context);
  return { get, run: code => vm.runInContext(code, context), requests, streams, clipboard, replies,
    get cookie() { return cookies; }, get activeElement() { return activeElement; },
    message: event => windowEvents.get('message')(event),
    buttons: () => document.querySelectorAll('#file-list button'),
  };
}

const tests = [];
const test = (name, fn) => tests.push({ name, fn });
const setup = async () => { const env = environment(); await flush(); await flush(); return env; };
const file = (overrides = {}) => ({ path: 'deliverables/welcome.md', name: 'welcome.md', primary: true, available: true, previewable: true, size: 24, ...overrides });
const scope = (env, id = 'run-a', workspace = '/campaign-a') => env.run(`current = { id: ${JSON.stringify(id)}, workspace: ${JSON.stringify(workspace)}, source: null, running: false, meta: {} }`);
const filesUrl = (id = 'run-a', workspace = '/campaign-a') => `/api/runs/${id}/artifacts?workspace=${encodeURIComponent(workspace)}`;
const statusUrl = (id = 'run-a', workspace = '/campaign-a') => `/api/runs/${id}?workspace=${encodeURIComponent(workspace)}`;
const artifactUrl = (name, id = 'run-a', workspace = '/campaign-a') => `/api/runs/${id}/artifact?${new URLSearchParams({ workspace, path: name })}`;

test('completed task opens its main text result as text and offers scoped download', async () => {
  const e = await setup(); const name = 'deliverables/hello & ą.md';
  const content = '<script>not executable</script>\nWelcome to Rocky.';
  e.replies.set(statusUrl(), { body: { status: 'passed', passed: true, goal: 'Write a welcome', eur: 0.035 } });
  e.replies.set(filesUrl(), { body: { files: [file({ path: name, name: 'hello & ą.md' })] } });
  e.replies.set(artifactUrl(name), { body: { text: content, truncated: false } });
  e.run('listen("run-a", "/campaign-a")'); await flush(); await flush();
  assert.equal(e.get('task-composer').open, false);
  assert.equal(e.get('delivery-status').textContent, 'Complete');
  assert.equal(e.get('selected-goal').textContent, 'Write a welcome');
  assert.equal(e.get('preview-text').textContent, content);
  assert.deepEqual(e.get('preview-text').htmlWrites, []);
  const download = new URL(e.get('download-result').href, 'http://rocky.local');
  assert.equal(download.pathname, '/api/runs/run-a/artifact');
  assert.equal(download.searchParams.get('workspace'), '/campaign-a');
  assert.equal(download.searchParams.get('path'), name);
  assert.equal(download.searchParams.get('download'), 'true');
  assert.equal(e.buttons()[0].getAttribute('aria-current'), 'true');
  assert.equal(e.get('copy-result').disabled, false);
});

test('nontext downloads and missing files never initiate a text preview', async () => {
  const e = await setup(); scope(e);
  await e.run(`previewArtifact(${JSON.stringify(file({ path: 'deliverables/slides.pptx', previewable: false }))})`);
  assert.match(e.get('preview-message').textContent, /Download this file/);
  assert(e.get('copy-result').classList.contains('hidden'));
  assert(e.get('download-result').href.includes('download=true'));
  const count = e.requests.length;
  await e.run(`previewArtifact(${JSON.stringify(file({ available: false, previewable: false }))})`);
  assert.equal(e.requests.length, count);
  assert.equal(e.get('download-result').href, undefined);
  assert(e.get('download-result').classList.contains('hidden'));
  assert.match(e.get('preview-message').textContent, /no longer/);
});

test('copy reports success, truncation and clipboard failure accurately', async () => {
  const e = await setup(); scope(e);
  e.replies.set(artifactUrl(file().path), { body: { text: 'A partial preview', truncated: true } });
  await e.run(`previewArtifact(${JSON.stringify(file())})`);
  assert.equal(e.get('copy-result').textContent, 'Copy preview');
  assert.match(e.get('preview-message').textContent, /beginning/);
  await e.get('copy-result').dispatch('click');
  assert.deepEqual(e.clipboard.writes, ['A partial preview']);
  assert.equal(e.get('preview-message').textContent, 'Copied to clipboard.');
  e.clipboard.denied = true;
  await e.get('copy-result').dispatch('click');
  assert.match(e.get('preview-message').textContent, /Clipboard unavailable/);
  assert.equal(e.activeElement, e.get('preview-text'));
});

test('late preview from another file or run cannot replace the selected result', async () => {
  const e = await setup(); scope(e); const slow = deferred();
  e.replies.set(artifactUrl(file().path), slow.promise);
  const old = e.run(`previewArtifact(${JSON.stringify(file())})`);
  scope(e, 'run-b');
  e.replies.set(artifactUrl('deliverables/next.md', 'run-b'), { body: { text: 'B contents', truncated: false } });
  await e.run(`previewArtifact(${JSON.stringify(file({ path: 'deliverables/next.md' }))})`);
  slow.resolve({ body: { text: 'A stale contents', truncated: false } }); await old;
  assert.equal(e.get('preview-text').textContent, 'B contents');
  assert.equal(e.get('preview-name').textContent, 'deliverables/next.md');
});

test('late inventory, metadata and stream from a deselected run are ignored', async () => {
  const e = await setup(); const oldSnapshot = deferred(); const oldFiles = deferred();
  e.replies.set(statusUrl(), oldSnapshot.promise);
  e.replies.set(filesUrl(), oldFiles.promise);
  e.run('listen("run-a", "/campaign-a")');
  const a = e.streams.at(-1);
  e.run('handle({type:"meta",payload:{status:"passed",passed:true,goal:"A"}})');
  e.run('listen("run-b", "/campaign-a")'); await flush();
  const b = e.streams.at(-1);
  a.message('run_started', { goal: 'Late A goal' }); a.end();
  oldSnapshot.resolve({ body: { status: 'passed', passed: true, goal: 'Late snapshot A' } });
  oldFiles.resolve({ body: { files: [file()] } }); await flush(); await flush();
  assert(a.closed);
  assert.equal(e.run('current.id'), 'run-b');
  assert.equal(e.get('selected-goal').textContent, 'Selected task');
  assert.equal(e.get('stop').disabled, false);
  assert.equal(e.get('file-list').children.length, 0);
  assert.equal(b.closed, false);
});

test('clear resets ad preview, file content, model cast and bill together', async () => {
  const e = await setup(); scope(e);
  e.run('showResult(); handle({type:"cost",payload:{eur:0.45}}); handle({type:"cast",payload:{models:{worker:"old/model"}}})');
  e.get('preview-text').textContent = 'Old text'; e.get('result-frame').style.height = '3000px';
  e.run('clearBoard()');
  assert(e.get('result').classList.contains('hidden'));
  assert.equal(e.get('result-frame').src, undefined);
  assert.equal(e.get('result-frame').style.height, '');
  assert.equal(e.get('preview-text').textContent, '');
  assert.equal(e.get('model-worker').textContent, 'waiting');
  assert.equal(e.get('delivery-cost').textContent, '€0.000 spent');
  assert(e.get('handover').classList.contains('hidden'));
});

test('terminal crash metadata works without done and sparse stop retains receipt', async () => {
  const e = await setup(); scope(e);
  e.run('handle({type:"cost",payload:{eur:0.456,usd:0.5,total_tokens:1200}}); handle({type:"done",payload:{status:"stopped",passed:false}})');
  assert.equal(e.get('cost-meter').textContent, '$0.500 · €0.456 · 1,200 tok');
  e.run('handle({type:"meta",payload:{status:"crashed",goal:"Interrupted task",eur:0.456}})'); await flush();
  assert.equal(e.get('delivery-status').textContent, 'Interrupted');
  assert.equal(e.get('selected-goal').textContent, 'Interrupted task');
  assert.equal(e.get('go').disabled, false);
  assert.equal(e.get('stop').disabled, true);
});

test('restored auth cookie is retained when selecting history with blank token field', async () => {
  const e = await setup(); e.get('token').value = '';
  e.run('listen("run-a", "/campaign-a")'); await flush();
  assert.equal(e.cookie, 'rocky_token=restored-session');
});

test('questions stay answerable with collapsed composer and submit to selected run', async () => {
  const e = await setup(); e.run('listen("run-a", "/campaign-a")'); await flush();
  e.run('handle({type:"ask",payload:{question:"Which format?",options:["Text","Document"]}})');
  assert.equal(e.get('task-composer').open, false);
  assert(!e.get('ask-form').classList.contains('hidden'));
  assert.equal(e.get('ask-options').textContent, 'Text  ·  Document');
  e.get('ask-input').value = 'Text'; await e.get('ask-form').dispatch('submit');
  const request = e.requests.find(r => r.url.endsWith('/answer'));
  assert.equal(request.url, '/api/runs/run-a/answer');
  assert.deepEqual(JSON.parse(request.options.body), { workspace: '/campaign-a', text: 'Text' });
  assert(e.get('ask-form').classList.contains('hidden'));
  assert.equal(e.get('delivery-status').textContent, 'Working');
});

test('failed answers stay editable and recover on retry', async () => {
  const e = await setup(); e.run('listen("run-a", "/campaign-a")'); await flush();
  e.run('handle({type:"ask",payload:{question:"Which format?",options:["Text","Document"]}})');
  e.get('ask-input').value = 'Text';
  e.replies.set('/api/runs/run-a/answer', { status: 503, body: { detail: 'Please retry your answer' } });
  await e.get('ask-form').dispatch('submit');
  assert(!e.get('ask-form').classList.contains('hidden'));
  assert.equal(e.get('ask-input').value, 'Text');
  assert.equal(e.get('ask-options').textContent, 'Please retry your answer');
  assert.equal(e.get('delivery-status').textContent, 'Needs your input');
  e.replies.set('/api/runs/run-a/answer', { body: { ok: true } });
  await e.get('ask-form').dispatch('submit');
  assert(e.get('ask-form').classList.contains('hidden'));
  assert.equal(e.get('delivery-status').textContent, 'Working');
});

test('late answer success or failure cannot hide or overwrite another run question', async () => {
  for (const response of [{ body: { ok: true } }, { status: 409, body: { detail: 'Old answer expired' } }]) {
    const e = await setup(); const slow = deferred();
    e.run('listen("run-a", "/campaign-a")'); await flush();
    e.run('handle({type:"ask",payload:{question:"A question"}})');
    e.replies.set('/api/runs/run-a/answer', slow.promise);
    e.get('ask-input').value = 'A response'; const pending = e.get('ask-form').dispatch('submit');
    e.run('listen("run-b", "/campaign-a")'); await flush();
    e.run('handle({type:"ask",payload:{question:"B question",options:["Keep B"]}})');
    e.get('ask-input').value = 'B draft';
    slow.resolve(response); await pending;
    assert.equal(e.run('current.id'), 'run-b');
    assert(!e.get('ask-form').classList.contains('hidden'));
    assert.equal(e.get('ask-input').value, 'B draft');
    assert.equal(e.get('ask-options').textContent, 'Keep B');
    assert.equal(e.get('delivery-status').textContent, 'Needs your input');
    assert(!e.get('work-log').textContent.includes('A response'));
  }
});

test('accepted answer arriving after completion does not revive a finished task', async () => {
  const e = await setup(); const slow = deferred();
  e.run('listen("run-a", "/campaign-a")'); await flush();
  e.run('handle({type:"ask",payload:{question:"Which format?"}})');
  e.get('ask-input').value = 'Text'; e.replies.set('/api/runs/run-a/answer', slow.promise);
  const pending = e.get('ask-form').dispatch('submit');
  e.run('handle({type:"meta",payload:{status:"passed",passed:true,goal:"Done"}})');
  slow.resolve({ body: { ok: true } }); await pending;
  assert.equal(e.get('delivery-status').textContent, 'Complete');
  assert.equal(e.get('stop').disabled, true);
  assert.equal(e.get('delivery-review').open, false);
});

test('stop applies authoritative task metadata and keeps existing cost', async () => {
  const e = await setup(); e.run('listen("run-a", "/campaign-a")'); await flush();
  e.run('handle({type:"cost",payload:{eur:0.075,total_tokens:500}})');
  e.replies.set('/api/runs/run-a/stop', { body: { status: 'stopped', goal: 'Stopped task', passed: false } });
  await e.get('stop').dispatch('click'); await flush();
  assert.equal(e.get('delivery-status').textContent, 'Stopped');
  assert.equal(e.get('selected-goal').textContent, 'Stopped task');
  assert.equal(e.get('delivery-cost').textContent, '€0.075 spent');
  assert.equal(e.get('stop').disabled, true);
  assert(e.requests.some(r => r.url === filesUrl()));
});

test('late stop success or failure cannot change the newly selected run', async () => {
  for (const response of [{ body: { status: 'stopped', goal: 'Old A task' } }, { status: 503, body: { detail: 'Old stop failed' } }]) {
    const e = await setup(); const slow = deferred();
    e.run('listen("run-a", "/campaign-a")'); await flush();
    e.replies.set('/api/runs/run-a/stop', slow.promise);
    const pending = e.get('stop').dispatch('click');
    e.replies.set(statusUrl('run-b'), { body: { status: 'passed', passed: true, goal: 'B completed' } });
    e.run('listen("run-b", "/campaign-a")'); await flush();
    slow.resolve(response); await pending;
    assert.equal(e.get('delivery-status').textContent, 'Complete');
    assert.equal(e.get('selected-goal').textContent, 'B completed');
    assert.equal(e.get('stop').disabled, true);
    assert(!e.get('work-log').textContent.includes('stop'));
  }
});

test('playbook apply and ad fix still open their returned followup runs', async () => {
  for (const action of ['apply', 'fix']) {
    const e = await setup(); scope(e);
    e.replies.set(`/api/runs/run-a/${action}`, { body: { id: 'followup', workspace: '/campaign-a' } });
    await e.run(action === 'apply' ? 'applyEntry({number:42})' : 'fixFinding({ad:2,code:"headline",field:"title",value:"Revised"})');
    assert.equal(e.run('current.id'), 'followup');
    const request = e.requests.find(r => r.url.endsWith(`/${action}`));
    const payload = JSON.parse(request.options.body);
    assert.equal(payload.workspace, '/campaign-a');
    if (action === 'apply') assert.equal(payload.number, 42);
    else assert.deepEqual(payload, { workspace: '/campaign-a', ad: 2, code: 'headline', field: 'title', value: 'Revised' });
    assert(e.streams.at(-1).url.includes('/followup/events'));
  }
});

test('late playbook or ad fix responses never select their followup or error over another run', async () => {
  for (const action of ['apply', 'fix']) {
    for (const response of [{ body: { id: 'old-followup', workspace: '/campaign-a' } }, { status: 503, body: { detail: 'Old mutation failed' } }]) {
      const e = await setup(); const slow = deferred(); scope(e);
      e.replies.set(`/api/runs/run-a/${action}`, slow.promise);
      const pending = e.run(action === 'apply' ? 'applyEntry({number:42})' : 'fixFinding({ad:2,code:"headline"})');
      e.run('listen("run-b", "/campaign-a")'); await flush();
      slow.resolve(response); await pending;
      assert.equal(e.run('current.id'), 'run-b');
      assert.equal(e.get('selected-goal').textContent, 'Selected task');
      assert(!e.streams.some(s => s.url.includes('/old-followup/events')));
      assert(!e.get('work-log').textContent.includes('Old mutation failed'));
      assert.notEqual(e.get('pb-note').textContent, 'Old mutation failed');
      assert(e.get('result').classList.contains('hidden'));
    }
  }
});

test('late history from another workspace cannot replace or mis-scope selected history', async () => {
  const e = await setup(); const slow = deferred();
  e.get('workspace').value = '/campaign-a';
  e.replies.set('/api/runs?workspace=%2Fcampaign-a', slow.promise);
  const old = e.run('refreshRuns()');
  e.get('workspace').value = '/campaign-b';
  e.replies.set('/api/runs?workspace=%2Fcampaign-b', { body: [{ id: 'b-history', goal: 'B result', status: 'passed', eur: 0.03 }] });
  await e.run('refreshRuns()');
  slow.resolve({ body: [{ id: 'a-history', goal: 'Old A result', status: 'passed' }] }); await old;
  assert(e.get('runs').textContent.includes('B result'));
  assert(!e.get('runs').textContent.includes('Old A result'));
  await e.get('runs').children[0].children[0].dispatch('click');
  assert.equal(e.run('current.id'), 'b-history');
  assert.equal(e.run('current.workspace'), '/campaign-b');
});

test('workspace change detaches the old run and prevents applying to it', async () => {
  const e = await setup(); e.run('listen("run-a", "/campaign-a")'); await flush();
  const old = e.streams.at(-1);
  e.get('workspace').value = '/campaign-b'; await e.get('workspace').dispatch('change');
  await e.run('applyEntry({number:42})');
  assert(old.closed);
  assert.equal(e.run('current.id'), null);
  assert.equal(e.get('task-composer').open, true);
  assert.equal(e.requests.filter(r => r.url.endsWith('/apply')).length, 0);
});

test('ad frame restore/fix events are scoped and stop is preserved', async () => {
  const e = await setup(); scope(e); e.run('showResult()');
  const frame = e.get('result-frame').contentWindow;
  e.message({ source: frame, data: { type: 'rocky-ready' } });
  assert.equal(frame.messages[0].message.type, 'rocky-restore');
  e.message({ source: frame, data: { type: 'rocky-height', h: 900 } });
  assert.equal(e.get('result-frame').style.height, '900px');
  e.run('clearBoard()'); scope(e, 'run-b');
  e.message({ source: frame, data: { type: 'rocky-fix', ad: 1, code: 'x' } });
  assert.equal(e.requests.filter(r => r.url.endsWith('/fix')).length, 0);
  await e.get('stop').dispatch('click');
  const request = e.requests.find(r => r.url.endsWith('/stop'));
  assert.equal(request.url, '/api/runs/run-b/stop');
  assert.deepEqual(JSON.parse(request.options.body), { workspace: '/campaign-a' });
});

test('artifact failure exposes retry; preview failure leaves download usable', async () => {
  const e = await setup(); scope(e);
  e.replies.set(filesUrl(), { status: 503, body: { detail: 'Try again' } });
  await e.run('loadDeliveryFiles()');
  assert(!e.get('retry-files').classList.contains('hidden'));
  assert.match(e.get('files-message').textContent, /Try again/);
  e.replies.set(filesUrl(), { body: { files: [file()] } });
  e.replies.set(artifactUrl(file().path), { status: 404, body: { detail: 'File moved' } });
  await e.get('retry-files').dispatch('click'); await flush();
  assert.match(e.get('preview-message').textContent, /File moved/);
  assert(e.get('download-result').href.includes('download=true'));
  assert.equal(e.get('copy-result').disabled, true);
});

(async () => {
  for (const { name, fn } of tests) {
    await fn(); console.log(`ok - ${name}`);
  }
  console.log(`${tests.length} delivery behavior checks passed`);
})().catch(error => { console.error(error); process.exitCode = 1; });
