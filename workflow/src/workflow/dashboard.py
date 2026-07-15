"""Local live dashboard server for `workflow.py` Workflow objects.

Not meant to be imported directly in normal use - call
`workflow.visualize()` instead. That method imports this module lazily
(only on first call), so scripts that never touch the dashboard never pay
for http.server / webbrowser / threading imports.

How it hooks into a running workflow, with zero extra wiring from the
caller:

  1. `register(workflow, Run=Run)` remembers the workflow, and - the
     first time it's called in this process - monkeypatches `Run.execute`
     (the exact class object passed in, so it's always the real one the
     caller's script uses) to transparently attach dashboard listeners to
     every `Run` created against a *registered* workflow, right before it
     actually executes. Runs against workflows that never called
     `.visualize()` are completely untouched (register() never having
     been called for them means `execute()` behaves exactly as before).

  2. Those listeners use `Run.on(...)`, the library's own public event
     API - the dashboard is just another listener, coexisting fine with
     any listeners the user's own script already attached.

  3. Every event is pushed to any connected browser tab(s) over
     Server-Sent Events (GET /events). GET /api/snapshot gives a fresh
     full snapshot (for first load, or to resync after a dropped
     connection).
"""

from __future__ import annotations

import json
import queue
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

_STATIC_DIR = Path(__file__).resolve().parent / "static"

EVENTS = (
    "run_started", "run_finished", "run_cancelled", "validation_failed",
    "level_started", "level_finished",
    "task_started", "task_succeeded", "task_failed", "task_retrying",
)

_lock = threading.RLock()
_workflows: dict[str, Any] = {}          # wf_id -> live Workflow instance
_clients: list["queue.Queue[str]"] = []  # one queue per connected SSE tab
_attached_run_ids: set[int] = set()      # id(run) already wired to the dashboard
_patched_run_classes: set[int] = set()   # id(RunClass) already monkeypatched

_server: ThreadingHTTPServer | None = None
_server_thread: threading.Thread | None = None
_server_port: int | None = None
_browser_opened = False


# ----------------------------------------------------------------------
# Serialization helpers - all duck-typed (getattr with defaults) rather
# than isinstance-checked against `workflow.py`'s classes, so this module
# never needs to import that module and can't get out of sync with it.
# ----------------------------------------------------------------------

def _wf_id(workflow: Any) -> str:
    return str(id(workflow))


def _task_key(task: Any) -> str:
    return str(id(task))


def _status_value(status: Any) -> str:
    if status is None:
        return "pending"
    return getattr(status, "value", str(status))


def _task_dict(task: Any) -> dict[str, Any]:
    return {
        "key": _task_key(task),
        "value": task.value,
        "task_id": getattr(task, "id", None),
        "group": getattr(task, "group", None),
        "status": _status_value(getattr(task, "run_status", None)),
        "tags": list(getattr(task, "tags", None) or []),
        "timeout": getattr(task, "timeout", None),
        "retries": getattr(task, "retries", 0),
        "cache": getattr(task, "cache", False),
        "has_function": getattr(task, "function", None) is not None,
        "metadata": getattr(task, "metadata", None) or {},
    }


def _edge_dict(edge: Any) -> dict[str, Any]:
    return {
        "source": _task_key(edge.source),
        "target": _task_key(edge.target),
        "directed": edge.directed,
        "weight": edge.weight,
        "description": edge.description,
    }


def _depth_map(workflow: Any) -> tuple[dict[str, int], bool]:
    """{task_key: depth}, depth = index of the parallel-execution level
    (`workflow.plan_levels()`) the task falls into. Falls back to depth 0
    for every task (and reports acyclic=False) if the graph currently has
    a dependency cycle or fails to plan for any other reason - a broken
    workflow should still be inspectable in the dashboard, not crash it."""
    try:
        levels = workflow.plan_levels()
    except Exception:
        return {_task_key(n): 0 for n in workflow.nodes}, False
    depths: dict[str, int] = {}
    for depth, level in enumerate(levels):
        for t in level:
            depths[_task_key(t)] = depth
    for n in workflow.nodes:
        depths.setdefault(_task_key(n), 0)
    return depths, True


def _validation_summary(workflow: Any) -> dict[str, Any]:
    try:
        issues = workflow.validate()
    except Exception as exc:  # validate() itself should never raise, but just in case
        return {"errors": 1, "warnings": 0, "issues": [f"validate() raised: {exc!r}"]}
    errors = [i for i in issues if getattr(i, "level", None) == "error"]
    warnings = [i for i in issues if getattr(i, "level", None) == "warning"]
    return {"errors": len(errors), "warnings": len(warnings), "issues": [str(i) for i in issues]}


def snapshot(wf_id: str) -> dict[str, Any] | None:
    """Full current-state snapshot of one registered workflow."""
    with _lock:
        workflow = _workflows.get(wf_id)
    if workflow is None:
        return None

    depths, acyclic = _depth_map(workflow)
    tasks = []
    task_keys: set[str] = set()
    for n in workflow.nodes:
        if not hasattr(n, "run_status"):
            continue  # a plain Node, not a Task - nothing to run or visualize
        d = _task_dict(n)
        d["depth"] = depths.get(d["key"], 0)
        tasks.append(d)
        task_keys.add(d["key"])

    edges = [
        _edge_dict(e) for e in workflow.edges
        if _task_key(e.source) in task_keys and _task_key(e.target) in task_keys
    ]

    groups: list[str | None] = []
    for t in tasks:
        if t["group"] not in groups:
            groups.append(t["group"])

    return {
        "id": wf_id,
        "name": workflow.name or f"workflow-{wf_id[-4:]}",
        "acyclic": acyclic,
        "tasks": tasks,
        "edges": edges,
        "groups": groups,
        "validation": _validation_summary(workflow),
        "history_count": len(getattr(workflow, "history", None) or []),
    }


def full_snapshot() -> dict[str, Any]:
    with _lock:
        ids = list(_workflows.keys())
    return {"workflows": [s for s in (snapshot(i) for i in ids) if s is not None]}


# ----------------------------------------------------------------------
# Broadcasting
# ----------------------------------------------------------------------

def _broadcast(kind: str, payload: dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False, default=str)
    msg = f"event: {kind}\ndata: {data}\n\n"
    with _lock:
        clients = list(_clients)
    for q in clients:
        try:
            q.put_nowait(msg)
        except Exception:
            with _lock:
                if q in _clients:
                    _clients.remove(q)


def _task_run_dict(task_run: Any) -> dict[str, Any]:
    d = task_run.to_dict()
    d["key"] = _task_key(task_run.task)
    return d


def _forward_event(event: str, wf_id: str, args: tuple) -> None:
    payload: dict[str, Any] = {"workflow_id": wf_id, "event": event}

    if event in ("run_started", "run_finished", "run_cancelled"):
        payload["run"] = args[0].to_dict()
    elif event == "validation_failed":
        payload["issues"] = [str(i) for i in args[0]]
    elif event == "level_started":
        payload["tasks"] = [_task_key(t) for t in args[0]]
    elif event == "level_finished":
        level, task_runs = args
        payload["tasks"] = [_task_key(t) for t in level]
        payload["task_runs"] = [_task_run_dict(tr) for tr in task_runs]
    else:  # task_started / task_succeeded / task_failed / task_retrying
        payload["task_run"] = _task_run_dict(args[0])

    # Piggyback a fresh snapshot on the events that change overall shape
    # (validation, run start/end) so the browser never has to guess.
    if event in ("run_started", "run_finished", "run_cancelled", "validation_failed"):
        payload["snapshot"] = snapshot(wf_id)

    _broadcast(event, payload)


def _attach_listeners(run: Any, wf_id: str) -> None:
    rid = id(run)
    with _lock:
        if rid in _attached_run_ids:
            return
        _attached_run_ids.add(rid)
    for event in EVENTS:
        run.on(event, lambda *args, _evt=event, _wf=wf_id: _forward_event(_evt, _wf, args))


def _ensure_patched(run_class: Any) -> None:
    key = id(run_class)
    with _lock:
        if key in _patched_run_classes:
            return
        _patched_run_classes.add(key)
        original_execute = run_class.execute

        def patched_execute(self, *args, **kwargs):
            wf = self.workflow
            if wf is not None:
                wf_id = _wf_id(wf)
                with _lock:
                    is_registered = wf_id in _workflows
                if is_registered:
                    _attach_listeners(self, wf_id)
            return original_execute(self, *args, **kwargs)

        run_class.execute = patched_execute


# ----------------------------------------------------------------------
# HTTP + SSE server
# ----------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:  # keep stdout clean
        pass

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._serve_file("index.html", "text/html; charset=utf-8")
        elif path == "/app.js":
            self._serve_file("app.js", "application/javascript; charset=utf-8")
        elif path == "/styles.css":
            self._serve_file("styles.css", "text/css; charset=utf-8")
        elif path == "/api/snapshot":
            self._serve_json(full_snapshot())
        elif path == "/events":
            self._serve_sse()
        else:
            self.send_error(404)

    def _serve_file(self, name: str, content_type: str) -> None:
        try:
            data = (_STATIC_DIR / name).read_bytes()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _serve_json(self, obj: Any) -> None:
        data = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _serve_sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        q: "queue.Queue[str]" = queue.Queue()
        with _lock:
            _clients.append(q)
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while True:
                try:
                    msg = q.get(timeout=15)
                except queue.Empty:
                    msg = ": ping\n\n"
                self.wfile.write(msg.encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with _lock:
                if q in _clients:
                    _clients.remove(q)


def _ensure_server(preferred_port: int) -> str:
    global _server, _server_thread, _server_port
    with _lock:
        if _server is not None:
            return f"http://127.0.0.1:{_server_port}"

        port = preferred_port
        httpd = None
        for _ in range(50):
            try:
                httpd = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
                break
            except OSError:
                port += 1
        if httpd is None:
            raise RuntimeError("taskgraph dashboard: could not find a free local port")

        httpd.daemon_threads = True
        _server = httpd
        _server_port = port
        _server_thread = threading.Thread(
            target=httpd.serve_forever, name="taskgraph-dashboard", daemon=False
        )
        _server_thread.start()
        return f"http://127.0.0.1:{port}"


def register(workflow: Any, Run: Any, port: int = 8420, open_browser: bool = True) -> str:
    """Registers `workflow` with the (possibly newly started) dashboard
    server, wires up live event streaming, and returns the dashboard URL.
    See `Workflow.visualize()` for the public docstring."""
    global _browser_opened

    wf_id = _wf_id(workflow)
    with _lock:
        is_new = wf_id not in _workflows
        _workflows[wf_id] = workflow
        _ensure_patched(Run)
        base_url = _ensure_server(port)

    if is_new:
        _broadcast("workflow_registered", {"workflow_id": wf_id, "snapshot": snapshot(wf_id)})

    full_url = f"{base_url}/"
    label = workflow.name or wf_id
    print(f"[taskgraph] dashboard ready at {full_url}  (workflow: {label!r})")

    if open_browser and not _browser_opened:
        _browser_opened = True
        threading.Timer(0.2, lambda: webbrowser.open(full_url)).start()

    return full_url
