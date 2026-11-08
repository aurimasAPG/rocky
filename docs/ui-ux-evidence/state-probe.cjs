const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
class Element {
  constructor(id = '') {
    this.id = id; this.value = ''; this.textContent = ''; this._html = ''; this.children = []; this.listeners = {};
    this.classes = new Set(); this.style = {}; this.dataset = {}; this.disabled = false; this.hidden = false;
    this.classList = {
      add: (...s) => s.forEach(x => this.classes.add(x)),
      remove: (...s) => s.forEach(x => this.classes.delete(x)),
      toggle: (s, yes) => { const on = yes === undefined ? !this.classes.has(s) : yes; on ? this.classes.add(s) : this.classes.delete(s); },
      contains: s => this.classes.has(s),
    };
  }
  set innerHTML(v) { this._html = v; if (v === '') this.children = []; }
  get innerHTML() { return this._html; }
  append(...xs) { this.children.push(...xs); }
  appendChild(x) { this.children.push(x); }
  prepend(x) { this.children.unshift(x); }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  focus() {}
}
const elements = new Map();
const get = id => { if (!elements.has(id)) elements.set(id, new Element(id)); return elements.get(id); };
const strips = ['critic', 'planner', 'worker', 'verifier'].map(role => { const e = new Element(); e.dataset.role = role; return e; });
const requests = [];
let cookies = { rocky_token: 'session-token' };
let cookieWrites = [];
const document = {
  getElementById: get,
  createElement: name => new Element(name),
  querySelectorAll: name => name === '.strip' ? strips : [],
  get cookie() { return Object.entries(cookies).map(([k, v]) => `${k}=${v}`).join('; '); },
  set cookie(v) { cookieWrites.push(v); const [entry] = v.split(';'); const [k, value] = entry.split('='); if (v.includes('Max-Age=0')) delete cookies[k]; else cookies[k] = value; },
};
class EventSource {
  constructor(url) { this.url = url; this.listeners = {}; this.closed = false; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  close() { this.closed = true; }
}
const ctx = vm.createContext({ document, window: { addEventListener() {} }, EventSource, console, setTimeout, clearTimeout,
  localStorage: { getItem: () => null, setItem() {} },
  fetch: async (url, options) => {
    requests.push({ url, options });
    let body = {};
    if (url === '/api/meta') body = { needs_token: true, workspaces: ['/campaign-a'] };
    else if (url.startsWith('/api/runs?')) body = [];
    else if (url.startsWith('/api/playbook/search')) body = { topics: [], results: [] };
    else if (url.endsWith('/apply')) body = { id: 'follow-up-a', workspace: '/campaign-a' };
    return { ok: true, status: 200, json: async () => body };
  }
});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../../rocky/static/app.js'), 'utf8'), ctx);
const run = code => vm.runInContext(code, ctx);
const flush = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  await flush(); await flush();
  const observations = [];
  get('result').classList.remove('hidden');
  get('result-frame').src = '/api/runs/old-a/board';
  get('model-worker').textContent = 'old/model';
  get('status-meter').textContent = 'passed old-a';
  strips[3].classList.add('active');
  run('clearBoard()');
  observations.push({ test: 'clearBoard stale content', resultStillVisible: !get('result').classList.contains('hidden'), frameStill: get('result-frame').src, modelStill: get('model-worker').textContent, statusStill: get('status-meter').textContent, activeRoleStill: strips.find(e => e.classList.contains('active')).dataset.role });

  run('listen("crash-a", "/campaign-a"); handle({type:"run_started"}); handle({type:"meta", payload:{status:"crashed",eur:0.123}}); current.source.listeners.end()');
  observations.push({ test: 'terminal metadata after crash', status: get('status-meter').textContent, runButtonEnabled: !get('go').disabled, stopButtonDisabled: get('stop').disabled });

  run('handle({type:"cost",payload:{eur:0.456,usd:0.5,total_tokens:1200}})');
  const beforeCost = get('cost-meter').textContent;
  run('handle({type:"done",payload:{passed:false,status:"stopped",summary:"stopped by the user"}}); handle({type:"meta",payload:{status:"stopped",eur:0.456,files:["deliverables/draft.md"],deliverable_exists:true,deliverable_path:"deliverables/draft.md"}})');
  observations.push({ test: 'stop sparse done receipt', beforeCost, afterCost: get('cost-meter').textContent, status: get('status-meter').textContent, cardText: get('verify-log').children.map(c => c.children.map(x=>x.textContent).join(': ')) });

  cookies = {rocky_token:'session-token'}; cookieWrites = [];
  get('token').value = '';
  run('listen("history-a", "/campaign-a")');
  observations.push({ test: 'blank token field on restored authenticated session', cookieWrite: cookieWrites[0], remainingCookie: document.cookie });

  get('workspace').value = '/campaign-b';
  get('workspace').listeners.change();
  await flush(); await flush();
  const stateAfterWorkspaceChange = run('({id:current.id,workspace:current.workspace})');
  await run('applyEntry({number:42})');
  const applyRequest = requests.findLast(r => r.url.endsWith('/apply'));
  observations.push({ test:'cross-workspace apply', displayedWorkspace:get('workspace').value, selectedRunAfterChange:stateAfterWorkspaceChange, applyUrl:applyRequest.url, applyBody:JSON.parse(applyRequest.options.body) });

  get('work-log').innerHTML = '';
  run('handle({type:"tool_call",payload:{name:"write_file",subtask:"draft"}}); handle({type:"tool_call",payload:{name:"write_file",subtask:"draft"}})');
  observations.push({ test:'replayed event effects', repeatedEventRenderedCount:get('work-log').children.length });
  console.log(JSON.stringify(observations,null,2));
})();
