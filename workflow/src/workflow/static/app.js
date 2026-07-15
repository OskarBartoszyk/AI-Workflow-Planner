'use strict';

/* ------------------------------------------------------------ config */

const CONFIG = {
  theme: 'dark',      // 'dark' | 'light'
  maxPanes: 4,         // visible panes before new workflows go to the dock (1/2/3/4-up split)
};

// URL overrides for demo links, e.g. ?theme=light&dir=TB&panel=1
const QS = new URLSearchParams(location.search);
if (QS.get('theme') === 'light' || QS.get('theme') === 'dark') CONFIG.theme = QS.get('theme');

const ST_COLOR = {
  success: 'var(--ok)',
  failed: 'var(--err)',
  running: 'var(--run)',
  pending: 'var(--wait)',
  cancelled: 'var(--cancel)',
};
const ST_ICON = { success: '✓', failed: '✕', running: '●', pending: '○', cancelled: '⦸' };

const GROUP_PALETTE = ['#3fb9c9', '#a371f7', '#f778ba', '#e3b341', '#7ee787', '#79c0ff', '#ff9bce', '#ffa657'];

const SSE_EVENTS = [
  'workflow_registered', 'run_started', 'run_finished', 'run_cancelled', 'validation_failed',
  'level_started', 'level_finished', 'task_started', 'task_succeeded', 'task_failed', 'task_retrying',
];

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

function fmtTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  return d.toTimeString().slice(0, 8);
}

function fmtDuration(sec) {
  if (sec == null) return '';
  if (sec < 1) return Math.round(sec * 1000) + 'ms';
  return sec.toFixed(1) + 's';
}

/* ------------------------------------------------------------ app */

class App {
  constructor() {
    this.data = {};      // wf_id -> { id, name, tasks, edges, groups, acyclic, validation, history_count, taskLog }
    this.order = [];     // registration order of workflow ids
    this.graphs = {};
    this.paneEls = {};
    this._panesKey = null;
    this._panelKey = null;
    this.state = {
      theme: CONFIG.theme,
      dir: QS.get('dir') === 'TB' ? 'TB' : 'LR',
      panelOpen: QS.get('panel') === '1',
      panes: [],
      dock: [],
      hidden: [],
      active: null,
      sel: {},
      hiddenPopoverOpen: false,
    };
    this._dir = this.state.dir;
    this._es = null;
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
      panelName: $('panel-name'), panelClose: $('panel-close'), panelBody: $('panel-body'),
      dock: $('dock'),
    };
    this.dom.dirLR.addEventListener('click', () => this.setState({ dir: 'LR' }));
    this.dom.dirTB.addEventListener('click', () => this.setState({ dir: 'TB' }));
    this.dom.themeBtn.addEventListener('click', () =>
      this.setState({ theme: this.state.theme === 'dark' ? 'light' : 'dark' }));
    this.dom.panelBtn.addEventListener('click', () => this.setState({ panelOpen: !this.state.panelOpen }));
    this.dom.panelClose.addEventListener('click', () => this.setState({ panelOpen: false }));
    window.addEventListener('keydown', e => {
      if (e.key === 'Escape') this.setState({ panelOpen: false, hiddenPopoverOpen: false });
    });
    window.addEventListener('resize', () => Object.values(this.graphs).forEach(G => {
      if (G.stub) return;
      G.W = G.el.clientWidth; G.H = G.el.clientHeight;
      this._setAnchors(G); this._reheat(G);
    }));

    this._loadSnapshot(true);
    this._connectEvents();
    this.render();
  }

  /* ---------------------------------------------------------- data loading */

  _loadSnapshot(initial) {
    fetch('/api/snapshot')
      .then(r => r.json())
      .then(json => {
        this._ingestWorkflows(json.workflows || []);
        this.render();
      })
      .catch(() => { /* server not reachable yet — SSE reconnect / next poll will retry */ });
  }

  _ingestWorkflows(list) {
    list.forEach(w => {
      if (!w) return;
      const existing = this.data[w.id];
      if (existing) {
        existing.name = w.name; existing.tasks = w.tasks; existing.edges = w.edges;
        existing.groups = w.groups; existing.acyclic = w.acyclic;
        existing.validation = w.validation; existing.history_count = w.history_count;
      } else {
        this.data[w.id] = {
          id: w.id, name: w.name, tasks: w.tasks, edges: w.edges, groups: w.groups,
          acyclic: w.acyclic, validation: w.validation, history_count: w.history_count,
          taskLog: {},
        };
        this.order.push(w.id);
        if (this.state.panes.length < CONFIG.maxPanes) this.state.panes.push(w.id);
        else this.state.dock.push(w.id);
        if (this.state.active == null) this.state.active = w.id;
      }
    });
  }

  _connectEvents() {
    if (this._es) this._es.close();
    const es = new EventSource('/events');
    this._es = es;
    es.addEventListener('open', () => this._loadSnapshot(false));
    SSE_EVENTS.forEach(name => {
      es.addEventListener(name, e => {
        try { this._onEvent(name, JSON.parse(e.data)); } catch (err) { /* ignore malformed event */ }
      });
    });
  }

  _onEvent(kind, payload) {
    if (kind === 'workflow_registered') {
      this._ingestWorkflows([payload.snapshot]);
      this.render();
      return;
    }

    const wf = this.data[payload.workflow_id];
    if (!wf) { this._loadSnapshot(false); return; }

    if (payload.snapshot) {
      wf.tasks = payload.snapshot.tasks; wf.edges = payload.snapshot.edges;
      wf.groups = payload.snapshot.groups; wf.acyclic = payload.snapshot.acyclic;
      wf.validation = payload.snapshot.validation; wf.history_count = payload.snapshot.history_count;
    }

    if (payload.task_run) {
      const tr = payload.task_run;
      wf.taskLog[tr.key] = tr;
      const t = wf.tasks.find(x => x.key === tr.key);
      if (t) t.status = tr.status;
    }

    this._syncAfterEvent(wf.id);
  }

  _syncAfterEvent(wfId) {
    const wf = this.data[wfId];
    const R = this.paneEls[wfId];
    const G = this.graphs[wfId];
    const needsRemount = R && (!G || (G.stub && wf.tasks.length) || (!G.stub && G.nodes.length !== wf.tasks.length));
    if (needsRemount) {
      this._destroyGraph(wfId);
      this._mount(R.host, wfId);
    }
    if (R) { this._updatePane(wfId); this._paint(wfId); }
    const G2 = this.graphs[wfId];
    if (G2 && !G2.stub) {
      G2.alpha = Math.max(G2.alpha, 0.3);
      if (!G2.raf) G2.raf = requestAnimationFrame(() => this._step(G2));
    }
    this._renderDock();
    if (this.state.active === wfId) this._renderPanel();
    this.dom.summary.textContent = this._summaryText();
  }

  /* ---------------------------------------------------------- derived */

  _summaryText() {
    const s = this.state;
    return this.order.length + ' workflow' + (this.order.length === 1 ? '' : 's') +
      ' · ' + s.panes.length + ' visible · ' + s.dock.length + ' docked' +
      (s.hidden.length ? ' · ' + s.hidden.length + ' hidden' : '');
  }

  _counts(A) {
    const c = { success: 0, running: 0, pending: 0, failed: 0, cancelled: 0 };
    A.tasks.forEach(t => { c[t.status] = (c[t.status] || 0) + 1; });
    const parts = [];
    if (c.success) parts.push('✓' + c.success);
    if (c.running) parts.push('●' + c.running);
    if (c.failed) parts.push('✕' + c.failed);
    if (c.cancelled) parts.push('⦸' + c.cancelled);
    if (c.pending) parts.push('○' + c.pending);
    return parts.join(' ') || 'empty';
  }

  _wfDot(A) {
    if (!A) return 'var(--wait)';
    if (A.tasks.some(t => t.status === 'running')) return 'var(--run)';
    if (A.tasks.some(t => t.status === 'failed')) return 'var(--err)';
    if (A.tasks.length && A.tasks.every(t => t.status === 'success')) return 'var(--ok)';
    return 'var(--wait)';
  }

  /* ---------------------------------------------------------- render */

  render() {
    const s = this.state;
    document.body.dataset.theme = s.theme;

    this.dom.summary.textContent = this._summaryText();
    this.dom.dirLR.classList.toggle('on', s.dir === 'LR');
    this.dom.dirTB.classList.toggle('on', s.dir === 'TB');
    this.dom.themeBtn.textContent = s.theme === 'dark' ? '☀ light' : '☾ dark';
    this.dom.panelBtn.classList.toggle('on', s.panelOpen);

    const key = s.panes.join(',');
    if (key !== this._panesKey) { this._panesKey = key; this._buildPanes(); }
    this._applyPaneGrid();
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

  _applyPaneGrid() {
    const n = this.state.panes.length;
    const el = this.dom.panes;
    if (n <= 1) {
      el.style.gridTemplateColumns = '1fr'; el.style.gridTemplateRows = '1fr';
      el.style.gridTemplateAreas = '"a"';
    } else if (n === 2) {
      el.style.gridTemplateColumns = '1fr 1fr'; el.style.gridTemplateRows = '1fr';
      el.style.gridTemplateAreas = '"a b"';
    } else if (n === 3) {
      el.style.gridTemplateColumns = '1fr 1fr'; el.style.gridTemplateRows = '1fr 1fr';
      el.style.gridTemplateAreas = '"a b" "a c"';
    } else {
      el.style.gridTemplateColumns = 'repeat(2, 1fr)';
      el.style.gridTemplateRows = 'repeat(' + Math.ceil(n / 2) + ', 1fr)';
      el.style.gridTemplateAreas = '';
    }
    const letters = 'abcdefghij';
    this.state.panes.forEach((id, i) => {
      const R = this.paneEls[id];
      if (R) R.root.style.gridArea = (n <= 3) ? letters[i] : 'auto';
    });
  }

  _buildPanes() {
    this.dom.panes.innerHTML = '';
    this.paneEls = {};
    this.state.panes.forEach(id => {
      const A = this.data[id];
      const R = {};
      R.dot = h('span', { class: 'pane-dot' });
      R.counts = h('span', { class: 'pane-counts' });
      R.warn = h('span', { class: 'warn-badge', text: '!' });
      R.badge = h('span', { class: 'active-badge', text: 'ACTIVE' });
      const openBtn = h('button', { class: 'icon-btn', title: 'Open side panel for this workflow', text: '▤' });
      openBtn.addEventListener('click', e => { e.stopPropagation(); this.setState({ panelOpen: true, active: id }); });
      R.minBtn = h('button', { class: 'icon-btn', title: 'Minimize to dock', text: '–' });
      R.minBtn.addEventListener('click', e => { e.stopPropagation(); this._minimize(id); });

      R.host = h('div', { class: 'graph-host', 'data-graph': id });

      R.selDot = h('span', { class: 'sel-dot' });
      R.selLabel = h('span', { class: 'sel-label' });
      R.selState = h('span', { class: 'sel-state' });
      R.selMeta = h('div', { class: 'sel-meta' });
      R.selDetail = h('div', { class: 'sel-detail' });
      R.selCard = h('div', { class: 'sel-card' },
        h('div', { class: 'sel-head' }, R.selDot, R.selLabel, R.selState),
        R.selMeta, R.selDetail);

      const kids = [
        h('div', { class: 'pane-head' },
          R.dot,
          h('span', { class: 'pane-name', text: A.name }),
          R.counts, R.warn,
          h('div', { class: 'spacer' }),
          R.badge, openBtn, R.minBtn),
        R.host,
      ];
      if (!A.tasks.length) kids.push(
        h('div', { class: 'empty-overlay' },
          h('div', { class: 'empty-circle' }),
          h('div', { class: 'empty-title', text: 'no tasks in this workflow yet' }),
          h('div', { class: 'empty-sub', text: 'the graph fills in as tasks are added' })));
      kids.push(R.selCard);

      R.root = h('section', { class: 'pane' }, ...kids);
      R.root.addEventListener('pointerdown', () => { if (this.state.active !== id) this.setState({ active: id }); });
      R.root.addEventListener('dragover', e => e.preventDefault());
      R.root.addEventListener('drop', e => {
        e.preventDefault();
        const cid = e.dataTransfer.getData('text/workflow');
        if (cid) this._swap(id, cid);
      });

      this.paneEls[id] = R;
      this.dom.panes.appendChild(R.root);
    });
  }

  _updatePane(id) {
    const s = this.state, A = this.data[id], R = this.paneEls[id];
    if (!R || !A) return;
    R.dot.style.background = this._wfDot(A);
    R.counts.textContent = this._counts(A);
    const errCount = A.validation ? A.validation.errors : 0;
    R.warn.style.display = errCount ? '' : 'none';
    if (errCount) R.warn.title = (A.validation.issues || []).join('\n');
    R.badge.style.display = (s.active === id && s.panes.length > 1) ? '' : 'none';
    R.minBtn.style.display = s.panes.length > 1 ? '' : 'none';
    const selT = A.tasks.find(t => t.key === s.sel[id]) || null;
    R.selCard.style.display = selT ? '' : 'none';
    if (selT) {
      const log = A.taskLog[selT.key];
      R.selDot.style.background = ST_COLOR[selT.status];
      R.selLabel.textContent = selT.value;
      R.selState.textContent = selT.status;
      R.selState.style.color = ST_COLOR[selT.status];
      const metaBits = [];
      if (selT.group != null) metaBits.push('group: ' + selT.group);
      if (selT.tags && selT.tags.length) metaBits.push('tags: ' + selT.tags.join(', '));
      if (log && log.attempt > 1) metaBits.push('attempt ' + log.attempt);
      if (log && log.duration != null) metaBits.push(fmtDuration(log.duration));
      R.selMeta.textContent = metaBits.join('  ·  ');
      R.selDetail.style.display = (log && log.error) ? '' : 'none';
      R.selDetail.textContent = (log && log.error) || '';
    }
  }

  _renderDock() {
    const s = this.state, D = this.dom.dock;
    D.innerHTML = '';
    if (!s.dock.length && !s.hidden.length) { D.style.display = 'none'; return; }
    D.style.display = '';

    if (s.dock.length) {
      D.appendChild(h('span', { class: 'dock-title', text: 'DOCK' }));
      s.dock.forEach(id => {
        const A = this.data[id];
        if (!A) return;
        const order = ['running', 'failed', 'success', 'pending', 'cancelled'];
        const dots = order.filter(st => A.tasks.some(t => t.status === st)).map(st => ST_COLOR[st]);
        if (!dots.length) dots.push('var(--wait)');
        const nameEl = h('span', { class: 'chip-name', text: A.name, title: 'Click to open this workflow as a pane' });
        nameEl.addEventListener('click', () => this._promote(id));
        const hideBtn = h('button', { class: 'chip-hide-btn', title: 'Hide from dock', text: '⌄' });
        hideBtn.addEventListener('click', e => { e.stopPropagation(); this._hide(id); });
        const chip = h('div', { class: 'chip', draggable: 'true', title: 'Drag onto a pane to swap' },
          h('span', { class: 'chip-glyph', text: '⣿' }),
          nameEl,
          h('span', { class: 'chip-dots' }, ...dots.map(c => h('span', { style: { background: c } }))),
          hideBtn);
        chip.addEventListener('dragstart', e => {
          e.dataTransfer.setData('text/workflow', id);
          e.dataTransfer.effectAllowed = 'move';
        });
        D.appendChild(chip);
      });
    }

    D.appendChild(h('div', { class: 'spacer' }));

    if (s.hidden.length) {
      const pill = h('button', { class: 'hidden-pill' },
        h('span', { text: '👁' }), h('span', { text: s.hidden.length + ' hidden' }));
      pill.addEventListener('click', e => {
        e.stopPropagation();
        this.setState({ hiddenPopoverOpen: !this.state.hiddenPopoverOpen });
      });
      D.appendChild(pill);
      if (s.hiddenPopoverOpen) {
        const pop = h('div', { class: 'hidden-popover' });
        s.hidden.forEach(id => {
          const A = this.data[id];
          if (!A) return;
          const item = h('div', { class: 'hidden-popover-item' },
            h('span', { text: A.name }), h('span', { text: '+ show', style: { color: 'var(--sel)' } }));
          item.addEventListener('click', () => this._unhide(id));
          pop.appendChild(item);
        });
        if (!s.hidden.length) pop.appendChild(h('div', { class: 'hidden-popover-empty', text: 'nothing hidden' }));
        D.appendChild(pop);
      }
    } else {
      D.appendChild(h('span', { class: 'dock-hint', text: 'drag a chip onto a pane to swap · ⌄ hides it' }));
    }
  }

  /* ---------------------------------------------------------- side panel (memory only) */

  _renderPanel() {
    const s = this.state, A = this.data[s.active];
    this.dom.panel.classList.toggle('open', s.panelOpen);
    if (!A) return;
    this.dom.panelDot.style.background = this._wfDot(A);
    this.dom.panelName.textContent = A.name;
    const key = s.active;
    if (key !== this._panelKey) { this._panelKey = key; this._buildPanelBody(); }
    this._refreshPanelBody();
  }

  _buildPanelBody() {
    const body = this.dom.panelBody;
    body.innerHTML = '';
    this._memBox = h('div', { class: 'mem' });
    body.append(this._memBox);
  }

  _refreshPanelBody() {
    this._refreshMem();
  }

  _refreshMem() {
    const A = this.data[this.state.active];
    if (!A) return;
    const by = {};
    A.tasks.forEach(t => { by[t.key] = t; });
    const box = this._memBox;
    box.innerHTML = '';

    const head = (label, cls, count) => {
      const kids = [h('span', { class: 'mem-title' + (cls ? ' ' + cls : ''), text: label })];
      if (count != null) kids.push(h('span', { class: 'mem-count', text: count }));
      kids.push(h('span', { class: 'mem-rule' }));
      return h('div', { class: 'mem-head' }, ...kids);
    };

    if (A.validation && A.validation.errors) {
      const banner = h('div', { class: 'validation-banner' },
        h('div', { class: 'v-title', text: A.validation.errors + ' validation error(s) — this workflow will not run as-is' }));
      (A.validation.issues || []).slice(0, 6).forEach(msg => banner.appendChild(h('div', { class: 'v-line', text: msg })));
      box.appendChild(banner);
    } else if (A.acyclic === false) {
      box.appendChild(h('div', { class: 'validation-banner' },
        h('div', { class: 'v-title', text: 'dependency cycle detected — execution order cannot be computed' })));
    }

    const past = A.tasks.filter(t => t.status === 'success' || t.status === 'failed');
    const pastSec = h('div', { class: 'mem-sec' }, head('PAST', '', past.length + ' entries'));
    past.forEach(t => {
      const log = A.taskLog[t.key];
      const bodyEl = h('div', { class: 'past-body' }, h('div', { class: 'past-label', text: t.value }));
      if (log && log.error) bodyEl.appendChild(h('div', { class: 'past-detail', text: log.error }));
      pastSec.appendChild(h('div', { class: 'past-item' },
        h('span', { class: 'past-icon', text: ST_ICON[t.status], style: { color: ST_COLOR[t.status] } }),
        bodyEl,
        h('span', { class: 'past-time', text: log ? fmtTime(log.finished_at) : '' })));
    });
    box.appendChild(pastSec);

    const nowTasks = A.tasks.filter(t => t.status === 'running');
    const nowSec = h('div', { class: 'mem-sec' }, head('NOW', 'now', nowTasks.length || null));
    if (nowTasks.length) {
      nowTasks.forEach(t => {
        const log = A.taskLog[t.key];
        const metaBits = [];
        if (log && log.started_at) metaBits.push('started ' + fmtTime(log.started_at));
        if (log && log.attempt > 1) metaBits.push('attempt ' + log.attempt);
        if (t.timeout) metaBits.push('timeout ' + t.timeout + 's');
        nowSec.appendChild(h('div', { class: 'now-card' },
          h('div', { class: 'now-head' }, h('span', { class: 'now-dot' }), h('span', { class: 'now-label', text: t.value })),
          h('div', { class: 'now-cmd', text: metaBits.join('  ·  ') }),
          h('div', { class: 'now-logs' }, h('div', { class: 'now-cursor' }, h('span', { text: '▏ running' })))));
      });
    } else {
      nowSec.appendChild(h('div', { class: 'mem-idle', text: 'idle — nothing running' }));
    }
    box.appendChild(nowSec);

    const future = A.tasks.filter(t => t.status === 'pending').sort((a, b) => a.depth - b.depth);
    const futSec = h('div', { class: 'mem-sec' }, head('FUTURE', '', future.length + ' queued'));
    future.forEach((t, i) => {
      const deps = A.edges.filter(e => e.directed && e.target === t.key).map(e => by[e.source]).filter(Boolean);
      const kids = [
        h('span', { class: 'future-n', text: String(i + 1).padStart(2, '0') }),
        h('span', { class: 'future-label', text: t.value }),
      ];
      if (deps.length) kids.push(h('span', { class: 'future-after', text: '← ' + deps.map(d => d.value).join(', ') }));
      futSec.appendChild(h('div', { class: 'future-item' }, ...kids));
    });
    box.appendChild(futSec);

    if (A.history_count) {
      const histSec = h('div', { class: 'mem-sec' }, head('HISTORY', '', A.history_count + ' run(s) recorded'));
      box.appendChild(histSec);
    }
  }

  /* ---------------------------------------------------------- pane ops */

  _promote(id) {
    const s = this.state, max = CONFIG.maxPanes ?? 4;
    let panes = [...s.panes], dock = s.dock.filter(x => x !== id), hidden = s.hidden.filter(x => x !== id);
    if (panes.length < max) panes.push(id);
    else { const out = panes[panes.length - 1]; panes[panes.length - 1] = id; dock = [...dock, out]; }
    this.setState({ panes, dock, hidden, active: id });
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
    if (!panes.length && dock.length) { panes = [dock[0]]; dock = dock.slice(1); }
    const active = panes.includes(s.active) ? s.active : (panes[0] ?? s.active);
    this.setState({ panes, dock, active });
  }

  _hide(id) {
    const s = this.state;
    const dock = s.dock.filter(x => x !== id);
    const hidden = [...s.hidden, id];
    this.setState({ dock, hidden });
  }

  _unhide(id) {
    const s = this.state;
    const hidden = s.hidden.filter(x => x !== id);
    const dock = [...s.dock, id];
    this.setState({ dock, hidden, hiddenPopoverOpen: hidden.length > 0 });
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
    if (!A || !A.tasks.length) { this.graphs[id] = { stub: true, el }; return; }
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
    const hullG = mk('g', { class: 'group-hulls' });
    const linkG = mk('g'), nodeG = mk('g');
    root.appendChild(hullG); root.appendChild(linkG); root.appendChild(nodeG);
    el.appendChild(svg);

    const byKey = {};
    const nodes = A.tasks.map((t, i) => { const n = { t, i, x: 0, y: 0, vx: 0, vy: 0 }; byKey[t.key] = n; return n; });
    const layers = {};
    nodes.forEach(n => (layers[n.t.depth] = layers[n.t.depth] || []).push(n));
    Object.values(layers).forEach(arr => arr.forEach((n, j) => { n.li = j; n.ln = arr.length; }));

    const G = {
      id, el, svg, root, linkG, nodeG, hullG, nodes, byKey, links: [], hulls: null,
      W: el.clientWidth || 800, H: el.clientHeight || 500,
      tf: { x: 0, y: 0, k: 1 },
      alpha: 1, raf: null, dragN: null, pan: null, moved: 0,
    };
    this._setAnchors(G, true);

    A.edges.forEach(e => {
      const s = byKey[e.source], d = byKey[e.target];
      if (!s || !d) return;
      const line = mk('line');
      line.style.strokeWidth = '1.4';
      line.style.transition = 'opacity .2s';
      if (!e.directed) line.style.strokeDasharray = '2 3';
      linkG.appendChild(line);
      G.links.push({ s, d, line, directed: e.directed });
    });

    // group clusters — only worth drawing when there's more than one distinct group
    const groupsPresent = (A.groups && A.groups.length > 1) ? A.groups : [];
    if (groupsPresent.length) {
      G.hulls = groupsPresent.map((grp, i) => {
        const color = grp == null ? 'var(--tx2)' : GROUP_PALETTE[i % GROUP_PALETTE.length];
        const rect = mk('rect', { rx: '14', ry: '14' });
        rect.setAttribute('fill', color);
        rect.setAttribute('fill-opacity', '0.07');
        rect.setAttribute('stroke', color);
        rect.setAttribute('stroke-opacity', '0.55');
        rect.style.cssText = 'stroke-width:1;stroke-dasharray:4 4;pointer-events:none;';
        const label = mk('text', { 'text-anchor': 'start' });
        label.textContent = grp == null ? 'ungrouped' : grp;
        label.style.cssText = `font:600 9.5px ui-monospace,Menlo,monospace;letter-spacing:1px;fill:${color};opacity:.85;pointer-events:none;`;
        hullG.appendChild(rect); hullG.appendChild(label);
        return { group: grp, rect, label };
      });
    }

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
      lbl.textContent = n.t.value;
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
    const maxD = Math.max(0, ...G.nodes.map(n => n.t.depth));
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
    if (G.hulls) {
      G.hulls.forEach(hu => {
        const members = G.nodes.filter(n => n.t.group === hu.group);
        if (!members.length) { hu.rect.setAttribute('opacity', '0'); hu.label.setAttribute('opacity', '0'); return; }
        hu.rect.setAttribute('opacity', '1'); hu.label.setAttribute('opacity', '1');
        let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
        members.forEach(n => {
          minX = Math.min(minX, n.x); minY = Math.min(minY, n.y);
          maxX = Math.max(maxX, n.x); maxY = Math.max(maxY, n.y);
        });
        const pad = 34;
        hu.rect.setAttribute('x', minX - pad);
        hu.rect.setAttribute('y', minY - pad);
        hu.rect.setAttribute('width', (maxX - minX) + pad * 2);
        hu.rect.setAttribute('height', (maxY - minY) + pad * 2);
        hu.label.setAttribute('x', minX - pad + 10);
        hu.label.setAttribute('y', minY - pad + 16);
      });
    }
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
      const tkey = G.dragN.t.key;
      this.setState(s => ({
        sel: Object.assign({}, s.sel, { [G.id]: s.sel[G.id] === tkey ? null : tkey }),
        active: G.id,
      }));
    }
    G.dragN = null; G.pan = null;
    G.el.style.cursor = 'grab';
    try { G.svg.releasePointerCapture(e.pointerId); } catch (x) { /* already released */ }
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
    const selKey = this.state.sel[id];
    G.nodes.forEach(n => {
      const st = n.t.status, d = n.dot;
      d.style.strokeWidth = '0';
      if (st === 'success') { d.style.fill = 'var(--ok)'; d.style.stroke = 'none'; d.setAttribute('r', '7'); }
      else if (st === 'failed') { d.style.fill = 'var(--err)'; d.style.stroke = 'none'; d.setAttribute('r', '7'); }
      else if (st === 'cancelled') { d.style.fill = 'var(--cancel)'; d.style.stroke = 'none'; d.setAttribute('r', '7'); }
      else if (st === 'running') { d.style.fill = 'var(--bg1)'; d.style.stroke = 'var(--run)'; d.style.strokeWidth = '2.5'; d.setAttribute('r', '8'); }
      else { d.style.fill = 'var(--bg3)'; d.style.stroke = 'var(--wait)'; d.style.strokeWidth = '1.5'; d.setAttribute('r', '7'); }
      n.halo.style.animation = st === 'running' ? 'tgPulse 1.8s ease-out infinite' : 'none';
      n.halo.style.opacity = st === 'running' ? '' : '0';
      n.ring.style.display = n.t.key === selKey ? '' : 'none';
      n.lbl.style.fill = st === 'running' ? 'var(--tx0)' : 'var(--tx1)';
      n.lbl.style.fontWeight = st === 'running' ? '600' : '400';
    });
    G.links.forEach(l => {
      const run = l.d.t.status === 'running';
      l.line.style.stroke = run ? 'var(--run)' : 'var(--edge)';
      if (l.directed) {
        l.line.style.strokeDasharray = run ? '5 5' : '';
        l.line.style.animation = run ? 'tgDash .8s linear infinite' : 'none';
        l.line.setAttribute('marker-end', 'url(#arr' + (run ? 'R' : '') + '-' + id + ')');
      } else {
        l.line.style.animation = 'none';
        l.line.removeAttribute('marker-end');
      }
    });
  }
}

document.addEventListener('DOMContentLoaded', () => new App().init());
