'use strict';

/* ------------------------------------------------------------ config */

const CONFIG = {
  theme: 'dark',        // 'dark' | 'light'
  accent: '#e5a50a',    // running-task color; overrides --run when changed
  maxPanes: 3,          // visible agent panes before promote starts swapping
  autoProgress: true,   // advance the simulated task graphs over time
};

// URL overrides for demo links, e.g. ?theme=light&dir=TB&panel=1
const QS = new URLSearchParams(location.search);
if (QS.get('theme') === 'light' || QS.get('theme') === 'dark') CONFIG.theme = QS.get('theme');

const ST_COLOR = {
  done: 'var(--ok)',
  failed: 'var(--err)',
  running: 'var(--run)',
  waiting: 'var(--wait)',
};

/* ------------------------------------------------------------ demo data */

function buildData() {
  const T = (id, label, cmd, state, deps, extra) =>
    Object.assign({ id, label, cmd, state, deps, detail: null, logs: null, time: null }, extra || {});
  const A = {};
  A.a1 = {
    id: 'a1', name: 'auth-refactor',
    tasks: [
      T('scan', 'scan-repo', 'rg -n "passport" src/', 'done', [], { time: '13:58:04' }),
      T('map', 'map-usages', 'ast-grep -p "requireAuth($_)"', 'done', ['scan'], { time: '13:59:41' }),
      T('mw', 'extract-middleware', 'src/auth/middleware.ts', 'done', ['map'], { time: '14:02:11' }),
      T('cm', 'codemod-imports', 'jscodeshift -t codemods/auth.js src/', 'failed', ['mw'],
        { time: '14:03:27', detail: 'parse error: mixed CJS/ESM in src/legacy/session.js' }),
      T('ss', 'refactor-session-store', 'src/auth/session-store.ts', 'running', ['mw'],
        { logs: ['reading src/auth/session-store.ts', 'rewriting MemoryStore -> RedisStore adapter', 'preserving public API: get/set/touch/destroy'] }),
      T('rt', 'update-routes', 'src/routes/*.ts', 'waiting', ['ss']),
      T('mt', 'migrate-tests', 'tests/auth/**', 'waiting', ['ss']),
      T('run', 'run-suite', 'pnpm test --filter auth', 'waiting', ['rt', 'mt']),
      T('lint', 'lint-typecheck', 'pnpm lint && tsc --noEmit', 'waiting', ['run']),
      T('pr', 'open-pr', 'gh pr create --draft', 'waiting', ['lint', 'cm']),
    ],
    chat: [
      { role: 'user', text: 'Refactor the auth layer to a middleware pattern. Keep the session API stable.' },
      { role: 'ai', text: 'Plan created — 10 tasks. Starting with a repo scan to map passport usages.' },
      { role: 'sys', text: '13:59:41 · map-usages → done' },
      { role: 'sys', text: '14:03:27 · codemod-imports → failed' },
      { role: 'ai', text: 'codemod-imports failed on src/legacy/session.js (mixed CJS/ESM). Continuing on the parallel branch; flagged for review.' },
      { role: 'user', text: 'Skip legacy for now, prioritize the session store.' },
      { role: 'ai', text: 'Acknowledged. refactor-session-store is running now; routes and tests are queued next.' },
    ],
  };
  A.a2 = {
    id: 'a2', name: 'db-migration',
    tasks: [
      T('dump', 'dump-schema', 'pg_dump --schema-only', 'done', [], { time: '14:01:12' }),
      T('diff', 'diff-models', 'prisma migrate diff', 'done', ['dump'], { time: '14:04:55' }),
      T('write', 'write-migration', 'migrations/0042_sessions.sql', 'running', ['diff'],
        { logs: ['generating DDL for sessions table', 'adding index on (user_id, expires_at)'] }),
      T('dry', 'dry-run', 'psql --single-transaction', 'waiting', ['write']),
      T('apply', 'apply-migration', 'prisma migrate deploy', 'waiting', ['dry']),
      T('verify', 'verify-rows', 'scripts/verify-counts.ts', 'waiting', ['apply']),
    ],
    chat: [
      { role: 'user', text: 'Migrate the sessions table before the auth PR lands.' },
      { role: 'ai', text: 'Schema diffed — 1 new table, 2 altered columns. Writing migration 0042 now.' },
    ],
  };
  A.a3 = {
    id: 'a3', name: 'docs-writer',
    tasks: [
      T('col', 'collect-diffs', 'git log v2.3.0..HEAD --oneline', 'done', [], { time: '14:00:30' }),
      T('cl', 'draft-changelog', 'CHANGELOG.md', 'running', ['col'],
        { logs: ['grouping 23 commits by scope', 'drafting "Breaking changes" section'] }),
      T('rd', 'update-readme', 'README.md', 'waiting', ['col']),
      T('rev', 'review-pass', 'vale docs/', 'waiting', ['cl', 'rd']),
    ],
    chat: [
      { role: 'ai', text: 'Collecting merged PRs since v2.3.0 for the changelog.' },
    ],
  };
  A.a4 = { id: 'a4', name: 'release-bot', tasks: [], chat: [] };

  // depth = longest dependency chain; drives the layer anchors in the graph
  Object.values(A).forEach(ag => {
    const by = {};
    ag.tasks.forEach(t => { by[t.id] = t; });
    const depth = t => (t.deps.length ? 1 + Math.max(...t.deps.map(d => depth(by[d]))) : 0);
    ag.tasks.forEach(t => { t.depth = depth(t); });
  });
  return A;
}

/* ------------------------------------------------------------ dom helper */

function h(tag, props = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === 'class') e.className = v;
    else if (k === 'text') e.textContent = v;
    else if (k === 'style') Object.assign(e.style, v);
    else e.setAttribute(k, v);
  }
  for (const kid of kids) if (kid != null) e.append(kid);
  return e;
}

/* ------------------------------------------------------------ app */

class App {
  constructor() {
    this.data = buildData();
    this.graphs = {};
    this._rr = 0;
    this.paneEls = {};
    this._panesKey = null;
    this._panelKey = null;
    this.state = {
      theme: CONFIG.theme,
      dir: QS.get('dir') === 'TB' ? 'TB' : 'LR',
      panelOpen: QS.get('panel') === '1',
      tab: 'write',
      panes: ['a1'],
      dock: ['a2', 'a3', 'a4'],
      active: 'a1',
      sel: {},
    };
    this._dir = this.state.dir;
  }

  setState(patch) {
    Object.assign(this.state, typeof patch === 'function' ? patch(this.state) : patch);
    this.render();
  }

  init() {
    const $ = id => document.getElementById(id);
    this.dom = {
      summary: $('summary'), dirLR: $('dir-lr'), dirTB: $('dir-tb'),
      themeBtn: $('theme-btn'), panelBtn: $('panel-btn'),
      panes: $('panes'), panel: $('panel'), panelDot: $('panel-dot'),
      panelName: $('panel-name'), panelClose: $('panel-close'),
      tabWrite: $('tab-write'), tabMem: $('tab-mem'), panelBody: $('panel-body'),
      dock: $('dock'),
    };
    this.dom.dirLR.addEventListener('click', () => this.setState({ dir: 'LR' }));
    this.dom.dirTB.addEventListener('click', () => this.setState({ dir: 'TB' }));
    this.dom.themeBtn.addEventListener('click', () =>
      this.setState({ theme: this.state.theme === 'dark' ? 'light' : 'dark' }));
    this.dom.panelBtn.addEventListener('click', () => this.setState({ panelOpen: !this.state.panelOpen }));
    this.dom.panelClose.addEventListener('click', () => this.setState({ panelOpen: false }));
    this.dom.tabWrite.addEventListener('click', () => this.setState({ tab: 'write' }));
    this.dom.tabMem.addEventListener('click', () => this.setState({ tab: 'mem' }));
    window.addEventListener('keydown', e => { if (e.key === 'Escape') this.setState({ panelOpen: false }); });
    window.addEventListener('resize', () => Object.values(this.graphs).forEach(G => {
      if (G.stub) return;
      G.W = G.el.clientWidth; G.H = G.el.clientHeight;
      this._setAnchors(G); this._reheat(G);
    }));
    setInterval(() => { if (CONFIG.autoProgress) this._advance(); }, 5200);
    this.render();
  }

  _now() { return new Date().toTimeString().slice(0, 8); }

  _counts(A) {
    const c = { done: 0, running: 0, waiting: 0, failed: 0 };
    A.tasks.forEach(t => c[t.state]++);
    const parts = [];
    if (c.done) parts.push('✓' + c.done);
    if (c.running) parts.push('●' + c.running);
    if (c.failed) parts.push('✕' + c.failed);
    if (c.waiting) parts.push('○' + c.waiting);
    return parts.join(' ') || 'empty';
  }

  _agentDot(A) {
    if (A.tasks.some(t => t.state === 'running')) return 'var(--run)';
    if (A.tasks.length && A.tasks.every(t => t.state === 'done')) return 'var(--ok)';
    if (A.tasks.some(t => t.state === 'failed')) return 'var(--err)';
    return 'var(--wait)';
  }

  /* ---------------------------------------------------------- render */

  render() {
    const s = this.state;
    document.body.dataset.theme = s.theme;
    if (CONFIG.accent && CONFIG.accent !== '#e5a50a') document.body.style.setProperty('--run', CONFIG.accent);

    this.dom.summary.textContent =
      Object.keys(this.data).length + ' agents · ' + s.panes.length + ' visible · ' + s.dock.length + ' docked';
    this.dom.dirLR.classList.toggle('on', s.dir === 'LR');
    this.dom.dirTB.classList.toggle('on', s.dir === 'TB');
    this.dom.themeBtn.textContent = s.theme === 'dark' ? '☀ light' : '☾ dark';
    this.dom.panelBtn.classList.toggle('on', s.panelOpen);

    const key = s.panes.join(',');
    if (key !== this._panesKey) { this._panesKey = key; this._buildPanes(); }
    s.panes.forEach(id => this._updatePane(id));

    this._renderDock();
    this._renderPanel();

    this._syncGraphs();
    if (this._dir !== s.dir) {
      this._dir = s.dir;
      Object.values(this.graphs).forEach(G => {
        if (G.stub) return;
        this._setAnchors(G); this._reheat(G);
      });
    }
    Object.keys(this.graphs).forEach(id => this._paint(id));
  }

  _buildPanes() {
    this.dom.panes.innerHTML = '';
    this.paneEls = {};
    this.state.panes.forEach(id => {
      const A = this.data[id];
      const R = {};
      R.dot = h('span', { class: 'pane-dot' });
      R.counts = h('span', { class: 'pane-counts' });
      R.badge = h('span', { class: 'active-badge', text: 'ACTIVE' });
      const openBtn = h('button', { class: 'icon-btn', title: 'Open side panel for this agent', text: '▤' });
      openBtn.addEventListener('click', e => { e.stopPropagation(); this.setState({ panelOpen: true, active: id }); });
      R.minBtn = h('button', { class: 'icon-btn', title: 'Minimize to dock', text: '–' });
      R.minBtn.addEventListener('click', e => { e.stopPropagation(); this._minimize(id); });

      R.host = h('div', { class: 'graph-host', 'data-graph': id });

      R.selDot = h('span', { class: 'sel-dot' });
      R.selLabel = h('span', { class: 'sel-label' });
      R.selState = h('span', { class: 'sel-state' });
      R.selCmd = h('div', { class: 'sel-cmd' });
      R.selDetail = h('div', { class: 'sel-detail' });
      R.selCard = h('div', { class: 'sel-card' },
        h('div', { class: 'sel-head' }, R.selDot, R.selLabel, R.selState),
        R.selCmd, R.selDetail);

      const kids = [
        h('div', { class: 'pane-head' },
          R.dot,
          h('span', { class: 'pane-name', text: A.name }),
          R.counts,
          h('div', { class: 'spacer' }),
          R.badge, openBtn, R.minBtn),
        R.host,
      ];
      if (!A.tasks.length) kids.push(
        h('div', { class: 'empty-overlay' },
          h('div', { class: 'empty-circle' }),
          h('div', { class: 'empty-title', text: 'no tasks planned yet' }),
          h('div', { class: 'empty-sub', text: 'the graph will populate once the agent receives a goal' })));
      kids.push(R.selCard);

      R.root = h('section', { class: 'pane' }, ...kids);
      R.root.addEventListener('pointerdown', () => { if (this.state.active !== id) this.setState({ active: id }); });
      R.root.addEventListener('dragover', e => e.preventDefault());
      R.root.addEventListener('drop', e => {
        e.preventDefault();
        const cid = e.dataTransfer.getData('text/agent');
        if (cid) this._swap(id, cid);
      });

      this.paneEls[id] = R;
      this.dom.panes.appendChild(R.root);
    });
  }

  _updatePane(id) {
    const s = this.state, A = this.data[id], R = this.paneEls[id];
    if (!R) return;
    R.dot.style.background = this._agentDot(A);
    R.counts.textContent = this._counts(A);
    R.badge.style.display = (s.active === id && s.panes.length > 1) ? '' : 'none';
    R.minBtn.style.display = s.panes.length > 1 ? '' : 'none';
    const selT = A.tasks.find(t => t.id === s.sel[id]) || null;
    R.selCard.style.display = selT ? '' : 'none';
    if (selT) {
      R.selDot.style.background = ST_COLOR[selT.state];
      R.selLabel.textContent = selT.label;
      R.selState.textContent = selT.state;
      R.selState.style.color = ST_COLOR[selT.state];
      R.selCmd.textContent = '$ ' + selT.cmd;
      R.selDetail.style.display = selT.detail ? '' : 'none';
      R.selDetail.textContent = selT.detail || '';
    }
  }

  _renderDock() {
    const s = this.state, D = this.dom.dock;
    if (!s.dock.length) { D.style.display = 'none'; return; }
    D.style.display = '';
    D.innerHTML = '';
    D.appendChild(h('span', { class: 'dock-title', text: 'DOCK' }));
    s.dock.forEach(id => {
      const A = this.data[id];
      const order = ['running', 'failed', 'done', 'waiting'];
      const dots = order.filter(st => A.tasks.some(t => t.state === st)).map(st => ST_COLOR[st]);
      if (!dots.length) dots.push('var(--wait)');
      const chip = h('div', { class: 'chip', draggable: 'true', title: 'Drag onto a pane to swap · click to open' },
        h('span', { class: 'chip-glyph', text: '⣿' }),
        h('span', { class: 'chip-name', text: A.name }),
        h('span', { class: 'chip-dots' }, ...dots.map(c => h('span', { style: { background: c } }))));
      chip.addEventListener('dragstart', e => {
        e.dataTransfer.setData('text/agent', id);
        e.dataTransfer.effectAllowed = 'move';
      });
      chip.addEventListener('click', () => this._promote(id));
      D.appendChild(chip);
    });
    D.appendChild(h('div', { class: 'spacer' }));
    D.appendChild(h('span', { class: 'dock-hint', text: 'drag a chip onto a pane to swap' }));
  }

  /* ---------------------------------------------------------- side panel */

  _renderPanel() {
    const s = this.state, A = this.data[s.active];
    this.dom.panel.classList.toggle('open', s.panelOpen);
    this.dom.panelDot.style.background = this._agentDot(A);
    this.dom.panelName.textContent = A.name;
    this.dom.tabWrite.classList.toggle('on', s.tab === 'write');
    this.dom.tabMem.classList.toggle('on', s.tab === 'mem');
    const key = s.active + '|' + s.tab;
    if (key !== this._panelKey) { this._panelKey = key; this._buildPanelBody(); }
    this._refreshPanelBody();
  }

  _buildPanelBody() {
    const body = this.dom.panelBody;
    body.innerHTML = '';
    this._chatBox = this._memBox = this._input = null;
    if (this.state.tab === 'write') {
      this._chatBox = h('div', { class: 'chat' });
      this._input = h('input', { class: 'chat-input' });
      this._input.addEventListener('keydown', e => { if (e.key === 'Enter') this._send(); });
      const send = h('button', { class: 'send-btn', title: 'Send', text: '↵' });
      send.addEventListener('click', () => this._send());
      body.append(this._chatBox, h('div', { class: 'chat-inputrow' }, this._input, send));
    } else {
      this._memBox = h('div', { class: 'mem' });
      body.append(this._memBox);
    }
  }

  _refreshPanelBody() {
    const A = this.data[this.state.active];
    if (this.state.tab === 'write') {
      this._input.placeholder = 'Message ' + A.name + '…';
      const box = this._chatBox;
      box.innerHTML = '';
      if (!A.chat.length) {
        box.appendChild(h('div', { class: 'chat-empty', text: 'no messages yet — say something below' }));
      } else {
        A.chat.forEach(m => box.appendChild(this._chatNode(m)));
      }
      box.scrollTop = box.scrollHeight;
    } else {
      this._refreshMem();
    }
  }

  _chatNode(m) {
    if (m.role === 'user') return h('div', { class: 'msg-user', text: m.text });
    if (m.role === 'ai') return h('div', { class: 'msg-ai', text: m.text });
    return h('div', { class: 'msg-sys' },
      h('span', { class: 'msg-line' }),
      h('span', { class: 'msg-sys-text', text: m.text }),
      h('span', { class: 'msg-line' }));
  }

  _refreshMem() {
    const A = this.data[this.state.active];
    const by = {};
    A.tasks.forEach(t => { by[t.id] = t; });
    const box = this._memBox;
    box.innerHTML = '';

    const head = (label, cls, count) => {
      const kids = [h('span', { class: 'mem-title' + (cls ? ' ' + cls : ''), text: label })];
      if (count != null) kids.push(h('span', { class: 'mem-count', text: count }));
      kids.push(h('span', { class: 'mem-rule' }));
      return h('div', { class: 'mem-head' }, ...kids);
    };

    const past = A.tasks.filter(t => t.state === 'done' || t.state === 'failed');
    const pastSec = h('div', { class: 'mem-sec' }, head('PAST', '', past.length + ' entries'));
    past.forEach(t => {
      const bodyEl = h('div', { class: 'past-body' }, h('div', { class: 'past-label', text: t.label }));
      if (t.detail) bodyEl.appendChild(h('div', { class: 'past-detail', text: t.detail }));
      pastSec.appendChild(h('div', { class: 'past-item' },
        h('span', { class: 'past-icon', text: t.state === 'done' ? '✓' : '✕', style: { color: ST_COLOR[t.state] } }),
        bodyEl,
        h('span', { class: 'past-time', text: t.time || '' })));
    });
    box.appendChild(pastSec);

    const nowT = A.tasks.find(t => t.state === 'running') || null;
    const nowSec = h('div', { class: 'mem-sec' }, head('NOW', 'now', null));
    if (nowT) {
      const logs = h('div', { class: 'now-logs' });
      (nowT.logs || []).forEach(t => logs.appendChild(h('div', { class: 'now-log', text: t })));
      logs.appendChild(h('div', { class: 'now-cursor' }, h('span', { text: '▏' })));
      nowSec.appendChild(h('div', { class: 'now-card' },
        h('div', { class: 'now-head' }, h('span', { class: 'now-dot' }), h('span', { class: 'now-label', text: nowT.label })),
        h('div', { class: 'now-cmd', text: '$ ' + nowT.cmd }),
        logs));
    } else {
      nowSec.appendChild(h('div', { class: 'mem-idle', text: 'idle — nothing running' }));
    }
    box.appendChild(nowSec);

    const future = A.tasks.filter(t => t.state === 'waiting').sort((a, b) => a.depth - b.depth);
    const futSec = h('div', { class: 'mem-sec' }, head('FUTURE', '', future.length + ' queued'));
    future.forEach((t, i) => {
      const kids = [
        h('span', { class: 'future-n', text: String(i + 1).padStart(2, '0') }),
        h('span', { class: 'future-label', text: t.label }),
      ];
      if (t.deps.length) kids.push(h('span', { class: 'future-after', text: '← ' + t.deps.map(d => by[d].label).join(', ') }));
      futSec.appendChild(h('div', { class: 'future-item' }, ...kids));
    });
    box.appendChild(futSec);
  }

  /* ---------------------------------------------------------- pane ops */

  _promote(id) {
    const s = this.state, max = CONFIG.maxPanes ?? 2;
    let panes = [...s.panes], dock = s.dock.filter(x => x !== id);
    if (panes.length < max) panes.push(id);
    else { const out = panes[panes.length - 1]; panes[panes.length - 1] = id; dock = [...dock, out]; }
    this.setState({ panes, dock, active: id });
  }

  _swap(paneId, chipId) {
    const s = this.state;
    if (paneId === chipId || !s.dock.includes(chipId)) return;
    const panes = s.panes.map(p => p === paneId ? chipId : p);
    let dock = s.dock.filter(x => x !== chipId);
    if (!panes.includes(paneId)) dock = [...dock, paneId];
    this.setState({ panes, dock, active: chipId });
  }

  _minimize(id) {
    const s = this.state;
    let panes = s.panes.filter(p => p !== id);
    let dock = [...s.dock, id];
    if (!panes.length) { panes = [dock[0]]; dock = dock.slice(1); }
    const active = panes.includes(s.active) ? s.active : panes[0];
    this.setState({ panes, dock, active });
  }

  /* ---------------------------------------------------------- chat */

  _send() {
    const inp = this._input;
    if (!inp) return;
    const v = inp.value.trim();
    if (!v) return;
    const A = this.data[this.state.active];
    A.chat.push({ role: 'user', text: v });
    inp.value = '';
    this.render();
    setTimeout(() => {
      const run = A.tasks.find(t => t.state === 'running');
      const q = A.tasks.filter(t => t.state === 'waiting').length;
      A.chat.push({
        role: 'ai',
        text: A.tasks.length
          ? 'Noted — folding that into the plan. Current focus: ' + (run ? run.label : 'idle') + '; ' + q + ' tasks queued.'
          : 'Noted. I have no goal yet — give me one and I will plan the task graph.',
      });
      this.render();
    }, 900);
  }

  /* ---------------------------------------------------------- simulation */

  _advance() {
    const ids = Object.keys(this.data).filter(id => this.data[id].tasks.length);
    let changed = false;
    for (let k = 0; k < ids.length && !changed; k++) {
      const id = ids[this._rr % ids.length]; this._rr++;
      const A = this.data[id];
      const now = this._now();
      const running = A.tasks.find(t => t.state === 'running');
      if (running) {
        running.state = 'done'; running.time = now;
        A.chat.push({ role: 'sys', text: now + ' · ' + running.label + ' → done' });
        changed = true;
      }
      const by = {}; A.tasks.forEach(t => { by[t.id] = t; });
      const ready = A.tasks.find(t => t.state === 'waiting' && t.deps.every(d => by[d].state === 'done'));
      if (ready) {
        ready.state = 'running';
        if (!ready.logs) ready.logs = ['executing ' + ready.cmd, 'streaming output…'];
        A.chat.push({ role: 'sys', text: now + ' · ' + ready.label + ' → running' });
        changed = true;
      }
      if (changed) {
        this._paint(id);
        const G = this.graphs[id];
        if (G && !G.stub) {
          G.alpha = Math.max(G.alpha, 0.15);
          if (!G.raf) G.raf = requestAnimationFrame(() => this._step(G));
        }
      }
    }
    if (changed) this.render();
  }

  /* ---------------------------------------------------------- graphs */

  _syncGraphs() {
    const want = this.state.panes;
    Object.keys(this.graphs).forEach(id => { if (!want.includes(id)) this._destroyGraph(id); });
    want.forEach(id => {
      const R = this.paneEls[id];
      if (!R) return;
      const host = R.host;
      const G = this.graphs[id];
      if (G) {
        if (G.stub) { G.el = host; return; }
        if (G.svg.parentNode !== host) {
          host.appendChild(G.svg); G.el = host;
          G.W = host.clientWidth; G.H = host.clientHeight;
          this._setAnchors(G); this._reheat(G);
        }
      } else {
        this._mount(host, id);
      }
    });
  }

  _destroyGraph(id) {
    const G = this.graphs[id];
    if (!G) return;
    if (G.raf) cancelAnimationFrame(G.raf);
    if (G.svg) G.svg.remove();
    delete this.graphs[id];
  }

  _mount(el, id) {
    const A = this.data[id];
    if (!A.tasks.length) { this.graphs[id] = { stub: true, el }; return; }
    const NS = 'http://www.w3.org/2000/svg';
    const mk = (n, at) => {
      const e = document.createElementNS(NS, n);
      for (const k in (at || {})) e.setAttribute(k, at[k]);
      return e;
    };
    const svg = mk('svg');
    svg.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;display:block;';
    const defs = mk('defs');
    const mkM = (mid, color) => {
      const m = mk('marker', { id: mid, markerWidth: '8', markerHeight: '8', refX: '7', refY: '3', orient: 'auto' });
      const p = mk('path', { d: 'M0,0 L7,3 L0,6 Z' });
      p.style.fill = color;
      m.appendChild(p);
      return m;
    };
    defs.appendChild(mkM('arr-' + id, 'var(--edge)'));
    defs.appendChild(mkM('arrR-' + id, 'var(--run)'));
    svg.appendChild(defs);
    const root = mk('g'); svg.appendChild(root);
    const linkG = mk('g'), nodeG = mk('g');
    root.appendChild(linkG); root.appendChild(nodeG);
    el.appendChild(svg);

    const byId = {};
    const nodes = A.tasks.map((t, i) => { const n = { t, i, x: 0, y: 0, vx: 0, vy: 0 }; byId[t.id] = n; return n; });
    const layers = {};
    nodes.forEach(n => (layers[n.t.depth] = layers[n.t.depth] || []).push(n));
    Object.values(layers).forEach(arr => arr.forEach((n, j) => { n.li = j; n.ln = arr.length; }));

    const G = {
      id, el, svg, root, linkG, nodeG, nodes, byId, links: [],
      W: el.clientWidth || 800, H: el.clientHeight || 500,
      tf: { x: 0, y: 0, k: 1 },
      alpha: 1, raf: null, dragN: null, pan: null, moved: 0,
    };
    this._setAnchors(G, true);

    A.tasks.forEach(t => t.deps.forEach(d => {
      const line = mk('line');
      line.style.strokeWidth = '1.4';
      line.style.transition = 'opacity .2s';
      linkG.appendChild(line);
      G.links.push({ s: byId[d], d: byId[t.id], line });
    }));

    nodes.forEach(n => {
      const g = mk('g', { 'data-node': n.i });
      g.style.cursor = 'pointer';
      g.style.transition = 'opacity .2s';
      const halo = mk('circle', { r: '14' });
      halo.style.cssText = 'fill:var(--run);opacity:0;transform-origin:center;transform-box:fill-box;pointer-events:none;';
      const ring = mk('circle', { r: '13', fill: 'none' });
      ring.style.cssText = 'stroke:var(--sel);stroke-width:1.5;display:none;pointer-events:none;';
      const dot = mk('circle', { r: '8' });
      const lbl = mk('text', { y: '24', 'text-anchor': 'middle' });
      lbl.textContent = n.t.label;
      lbl.style.cssText = 'font:10.5px ui-monospace,Menlo,monospace;fill:var(--tx1);user-select:none;pointer-events:none;';
      g.appendChild(halo); g.appendChild(ring); g.appendChild(dot); g.appendChild(lbl);
      nodeG.appendChild(g);
      n.g = g; n.dot = dot; n.halo = halo; n.ring = ring; n.lbl = lbl;
      g.addEventListener('pointerenter', () => this._hover(G, n));
      g.addEventListener('pointerleave', () => this._hover(G, null));
    });

    svg.addEventListener('pointerdown', e => this._pdown(G, e));
    svg.addEventListener('pointermove', e => this._pmove(G, e));
    svg.addEventListener('pointerup', e => this._pup(G, e));
    svg.addEventListener('wheel', e => this._wheel(G, e), { passive: false });

    this.graphs[id] = G;
    this._paint(id);
    this._applyTf(G);
    this._reheat(G);
  }

  _setAnchors(G, init) {
    const dir = this.state.dir;
    const maxD = Math.max(...G.nodes.map(n => n.t.depth));
    G.nodes.forEach(n => {
      if (dir === 'LR') { n.anchorX = 90 + n.t.depth * 150; n.centerY = G.H / 2; }
      else { n.anchorY = 70 + n.t.depth * 120; n.centerX = G.W / 2; }
      if (init) {
        const off = (n.li - (n.ln - 1) / 2) * 85 + (Math.random() - .5) * 20;
        if (dir === 'LR') { n.x = n.anchorX; n.y = G.H / 2 + off; }
        else { n.y = n.anchorY; n.x = G.W / 2 + off; }
      }
    });
    if (init) {
      if (dir === 'LR') G.tf.x = Math.max(10, (G.W - (180 + maxD * 150)) / 2);
      else G.tf.y = Math.max(10, (G.H - (140 + maxD * 120)) / 2);
    }
  }

  _reheat(G) {
    if (G.stub) return;
    G.alpha = 1;
    if (!G.raf) G.raf = requestAnimationFrame(() => this._step(G));
  }

  _step(G) {
    const dir = this.state.dir, a = G.alpha, N = G.nodes;
    for (let i = 0; i < N.length; i++) for (let j = i + 1; j < N.length; j++) {
      const n1 = N[i], n2 = N[j];
      let dx = n2.x - n1.x, dy = n2.y - n1.y;
      let d2 = dx * dx + dy * dy;
      if (d2 < 1) { dx = Math.random() - .5; dy = Math.random() - .5; d2 = 1; }
      if (d2 < 48400) {
        const d = Math.sqrt(d2), f = 2400 / d2 * a, fx = dx / d * f, fy = dy / d * f;
        n1.vx -= fx; n1.vy -= fy; n2.vx += fx; n2.vy += fy;
      }
    }
    G.links.forEach(l => {
      const dx = l.d.x - l.s.x, dy = l.d.y - l.s.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const f = (d - 115) * 0.04 * a, fx = dx / d * f, fy = dy / d * f;
      l.s.vx += fx; l.s.vy += fy; l.d.vx -= fx; l.d.vy -= fy;
    });
    N.forEach(n => {
      if (dir === 'LR') { n.vx += (n.anchorX - n.x) * 0.08 * a; n.vy += (n.centerY - n.y) * 0.004 * a; }
      else { n.vy += (n.anchorY - n.y) * 0.08 * a; n.vx += (n.centerX - n.x) * 0.004 * a; }
    });
    N.forEach(n => {
      if (n === G.dragN) { n.vx = 0; n.vy = 0; return; }
      n.vx *= 0.8; n.vy *= 0.8; n.x += n.vx; n.y += n.vy;
    });
    G.alpha *= 0.99;
    this._draw(G);
    if (G.alpha > 0.012 || G.dragN) G.raf = requestAnimationFrame(() => this._step(G));
    else G.raf = null;
  }

  _draw(G) {
    G.nodes.forEach(n => n.g.setAttribute('transform', 'translate(' + n.x + ',' + n.y + ')'));
    G.links.forEach(l => {
      const dx = l.d.x - l.s.x, dy = l.d.y - l.s.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const ux = dx / d, uy = dy / d;
      l.line.setAttribute('x1', l.s.x + ux * 11);
      l.line.setAttribute('y1', l.s.y + uy * 11);
      l.line.setAttribute('x2', l.d.x - ux * 15);
      l.line.setAttribute('y2', l.d.y - uy * 15);
    });
  }

  _applyTf(G) {
    G.root.setAttribute('transform', 'translate(' + G.tf.x + ',' + G.tf.y + ') scale(' + G.tf.k + ')');
  }

  _gpt(G, e) {
    const r = G.svg.getBoundingClientRect();
    return { x: (e.clientX - r.left - G.tf.x) / G.tf.k, y: (e.clientY - r.top - G.tf.y) / G.tf.k };
  }

  _pdown(G, e) {
    G.svg.setPointerCapture(e.pointerId);
    G.moved = 0;
    const ng = e.target.closest ? e.target.closest('g[data-node]') : null;
    if (ng) {
      G.dragN = G.nodes[+ng.getAttribute('data-node')];
      this._reheat(G);
    } else {
      G.pan = { px: e.clientX, py: e.clientY, tx: G.tf.x, ty: G.tf.y };
      G.el.style.cursor = 'grabbing';
    }
  }

  _pmove(G, e) {
    if (G.dragN) {
      const p = this._gpt(G, e);
      G.dragN.x = p.x; G.dragN.y = p.y;
      G.moved += 1;
      G.alpha = Math.max(G.alpha, 0.4);
      if (!G.raf) G.raf = requestAnimationFrame(() => this._step(G));
    } else if (G.pan) {
      G.tf.x = G.pan.tx + (e.clientX - G.pan.px);
      G.tf.y = G.pan.ty + (e.clientY - G.pan.py);
      G.moved += 1;
      this._applyTf(G);
    }
  }

  _pup(G, e) {
    if (G.dragN && G.moved < 4) {
      const tid = G.dragN.t.id;
      this.setState(s => ({
        sel: Object.assign({}, s.sel, { [G.id]: s.sel[G.id] === tid ? null : tid }),
        active: G.id,
      }));
    }
    G.dragN = null; G.pan = null;
    G.el.style.cursor = 'grab';
    try { G.svg.releasePointerCapture(e.pointerId); } catch (x) {}
  }

  _wheel(G, e) {
    e.preventDefault();
    const r = G.svg.getBoundingClientRect();
    const mx = e.clientX - r.left, my = e.clientY - r.top;
    const k2 = Math.min(2.5, Math.max(0.35, G.tf.k * Math.exp(-e.deltaY * 0.0015)));
    G.tf.x = mx - (mx - G.tf.x) / G.tf.k * k2;
    G.tf.y = my - (my - G.tf.y) / G.tf.k * k2;
    G.tf.k = k2;
    this._applyTf(G);
  }

  _hover(G, n) {
    const nb = new Set();
    if (n) {
      nb.add(n);
      G.links.forEach(l => { if (l.s === n) nb.add(l.d); if (l.d === n) nb.add(l.s); });
    }
    G.nodes.forEach(m => m.g.style.opacity = (!n || nb.has(m)) ? '1' : '0.15');
    G.links.forEach(l => l.line.style.opacity = (!n || l.s === n || l.d === n) ? '1' : '0.08');
  }

  _paint(id) {
    const G = this.graphs[id];
    if (!G || G.stub) return;
    const selId = this.state.sel[id];
    G.nodes.forEach(n => {
      const st = n.t.state, d = n.dot;
      d.style.strokeWidth = '0';
      if (st === 'done') { d.style.fill = 'var(--ok)'; d.style.stroke = 'none'; d.setAttribute('r', '7'); }
      else if (st === 'failed') { d.style.fill = 'var(--err)'; d.style.stroke = 'none'; d.setAttribute('r', '7'); }
      else if (st === 'running') { d.style.fill = 'var(--bg1)'; d.style.stroke = 'var(--run)'; d.style.strokeWidth = '2.5'; d.setAttribute('r', '8'); }
      else { d.style.fill = 'var(--bg3)'; d.style.stroke = 'var(--wait)'; d.style.strokeWidth = '1.5'; d.setAttribute('r', '7'); }
      n.halo.style.animation = st === 'running' ? 'tgPulse 1.8s ease-out infinite' : 'none';
      n.halo.style.opacity = st === 'running' ? '' : '0';
      n.ring.style.display = n.t.id === selId ? '' : 'none';
      n.lbl.style.fill = st === 'running' ? 'var(--tx0)' : 'var(--tx1)';
      n.lbl.style.fontWeight = st === 'running' ? '600' : '400';
    });
    G.links.forEach(l => {
      const run = l.d.t.state === 'running';
      l.line.style.stroke = run ? 'var(--run)' : 'var(--edge)';
      l.line.style.strokeDasharray = run ? '5 5' : '';
      l.line.style.animation = run ? 'tgDash .8s linear infinite' : 'none';
      l.line.setAttribute('marker-end', 'url(#arr' + (run ? 'R' : '') + '-' + id + ')');
    });
  }
}

document.addEventListener('DOMContentLoaded', () => new App().init());
