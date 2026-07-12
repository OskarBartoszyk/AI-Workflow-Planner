# Agent Task Graph

A multi-agent task-graph dashboard, implemented from the Claude Design file
`Agent Task Graph.dc.html`. Plain HTML/CSS/JS — no dependencies, no build step.

## Run

Open `index.html` directly in a browser, or serve the folder:

```sh
python3 -m http.server 8080
# → http://localhost:8080
```

## What it does

- **Agent panes** — each visible agent renders its task DAG as a live
  force-directed graph (pan by dragging the background, zoom with the wheel,
  drag nodes, hover to highlight neighbors, click a node for its detail card).
- **Dock** — minimized agents live in the bottom dock. Click a chip to promote
  it to a pane, or drag it onto a pane to swap the two.
- **Side panel** — per-agent WRITING tab (chat with the agent) and MEMORY tab
  (PAST / NOW / FUTURE task timeline). Open it with the ▤ buttons; Esc closes.
- **Layout & theme** — →/↓ toggles left-to-right vs top-to-bottom flow;
  ☀/☾ toggles dark/light.
- **Simulation** — task graphs advance on their own every ~5s (running tasks
  finish, ready tasks start), streaming status lines into each agent's chat.

## Config

Edit `CONFIG` at the top of `app.js`:

| key | default | meaning |
| --- | --- | --- |
| `theme` | `'dark'` | initial theme (`'dark'` or `'light'`) |
| `accent` | `'#e5a50a'` | color used for running tasks |
| `maxPanes` | `3` | visible panes before promoting starts swapping |
| `autoProgress` | `true` | advance the simulated task graphs |

URL overrides for demo links: `?theme=light`, `?dir=TB`, `?panel=1`.
