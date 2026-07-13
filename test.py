"""
Kompleksowy test WSZYSTKICH mozliwosci biblioteki (graph.py / workflow.py).

Sekcje:
  1. Budowa workflow + planer (plan / plan_levels)
  2. System eventow (Run.on(...))
  3. Walidacja (Workflow.validate / validate_group)
  4. Query API (find / query / tagi)
  5. Graph manipulation API (replace_node, insert_between, merge,
     clone_node, clone_subgraph, extract_subgraph, move_to_group,
     copy_group, rename_group)
  6. merge_graphs (how="union" / "node" / "specify")
  7. Historia runow (Workflow.history, save_history, load_history)
  8. Dry-run (Run.dry_run, RunPreview, execute(confirm=True))

Uruchomienie (z folderu NADRZEDNEGO wzgledem paczki z workflow.py/graph.py):
    python -m twoja_paczka.test_workflow

Jesli workflow.py / graph.py NIE sa czescia paczki (nie ma importow
relatywnych ".graph"), zamien import ponizej na:
    from workflow import (Task, Workflow, Run, RunStatus, CycleError,
                           ValidationError, ValidationIssue, RunPreview)
"""

import builtins
import json
import os
import sys
import subprocess
import time
import datetime

from workflow import (
    Task,
    Workflow,
    Run,
    RunStatus,
    CycleError,
    ValidationError,
    RunPreview,
)


def section(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def make_job(name: str, duration: float):
    """Symuluje robote trwajaca `duration` sekund, loguje start/koniec."""
    def _job() -> None:
        ts_start = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(f"      [{ts_start}] -> START  {name:<10} ({duration:.1f}s)")
        time.sleep(duration)
        ts_end = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(f"      [{ts_end}] <- KONIEC {name:<10}")
    return _job


# ======================================================================
# 1. Budowa workflow + planer
# ======================================================================
def demo_planner() -> Workflow:
    section("1. BUDOWA WORKFLOW + PLANER (plan / plan_levels)")

    w = Workflow(name="demo-workflow")

    fetch = w.add_task(Task(value="fetch", group="etl", function=make_job("fetch", 0.3)))
    clean = w.add_task(Task(value="clean", group="etl", function=make_job("clean", 0.6)))
    validate_t = w.add_task(Task(value="validate", group="etl", function=make_job("validate", 0.4)))
    load = w.add_task(Task(value="load", group="etl", function=make_job("load", 0.2)))
    w.add_edge(fetch, clean, directed=True)
    w.add_edge(fetch, validate_t, directed=True)
    w.add_edge(clean, load, directed=True)
    w.add_edge(validate_t, load, directed=True)

    w.add_task(Task(value="generate", group="reports", function=make_job("generate", 0.3)))
    w.add_task(Task(value="format", group="reports", function=make_job("format", 0.2)))
    w.add_task(Task(value="send", group="reports", function=make_job("send", 0.1)))
    w.build_group_chain("reports")

    w.add_task(Task(value="email", group="notifications", function=make_job("email", 0.3)))
    w.add_task(Task(value="sms", group="notifications", function=make_job("sms", 0.2)))
    w.add_task(Task(value="push", group="notifications", function=make_job("push", 0.1)))

    for group in ("etl", "reports", "notifications"):
        order = w.plan_group(group)
        levels = w.plan_levels_group(group)
        levels_str = " -> ".join(str([t.value for t in lvl]) for lvl in levels)
        print(f"  Plan '{group}':    {[t.value for t in order]}")
        print(f"  Poziomy '{group}': {levels_str}")

    return w


# ======================================================================
# 2. System eventow
# ======================================================================
def demo_events(w: Workflow) -> None:
    section("2. SYSTEM EVENTOW (Run.on(...))")

    run = Run(workflow=w)
    run.on("run_started", lambda r: print(f"    [EVENT] run_started      id={r.id[:8]}"))
    run.on("level_started", lambda lvl: print(f"    [EVENT] level_started    {[t.value for t in lvl]}"))
    run.on("task_started", lambda tr: print(f"    [EVENT] task_started     {tr.task.value}"))
    run.on("task_succeeded", lambda tr: print(f"    [EVENT] task_succeeded   {tr.task.value} ({tr.duration:.2f}s)"))
    run.on("level_finished", lambda lvl, trs: print(f"    [EVENT] level_finished   {[t.value for t in lvl]}"))
    run.on("run_finished", lambda r: print(f"    [EVENT] run_finished     status={r.status.value}"))

    print("  Odpalam grupe 'etl' z pelnym logiem eventow:")
    run.execute(group="etl")


# ======================================================================
# 3. Walidacja
# ======================================================================
def demo_validation() -> None:
    section("3. WALIDACJA (Workflow.validate / validate_group)")

    print("  -- workflow OK --")
    w_ok = Workflow(name="ok")
    a = w_ok.add_task(Task(value="a", group="g", function=lambda: None))
    b = w_ok.add_task(Task(value="b", group="g", function=lambda: None))
    w_ok.add_edge(a, b, directed=True)
    print("    problemy:", w_ok.validate())

    print("\n  -- duplicate id + brak funkcji + ujemny timeout --")
    w_bad = Workflow(name="bad")
    x = w_bad.add_task(Task(value="x", group="g", id=1, function=None))
    y = w_bad.add_task(Task(value="y", group="g", id=1, function=lambda: None, timeout=-5))
    for issue in w_bad.validate():
        print("   ", issue)

    print("\n  -- cykl + brak start node + unreachable --")
    w_cycle = Workflow(name="cycle")
    a2 = w_cycle.add_task(Task(value="a", group="g", function=lambda: None))
    b2 = w_cycle.add_task(Task(value="b", group="g", function=lambda: None))
    c2 = w_cycle.add_task(Task(value="c", group="g", function=lambda: None))
    w_cycle.add_edge(a2, b2, directed=True)
    w_cycle.add_edge(b2, c2, directed=True)
    w_cycle.add_edge(c2, a2, directed=True)
    for issue in w_cycle.validate():
        print("   ", issue)

    print("\n  -- Run.execute() na zlym workflow: 0 taskow odpalonych --")
    run_bad = Run(workflow=w_cycle)
    run_bad.on("validation_failed", lambda issues: print(f"    [EVENT] validation_failed: {len(issues)} problem(ow)"))
    run_bad.execute(group="g")
    print(f"    status={run_bad.status.value}, ile taskow probowano odpalic={len(run_bad.tasks)}")

    print("\n  -- strict=True rzuca ValidationError --")
    try:
        w_cycle.validate(strict=True)
    except ValidationError as e:
        print(f"    OK, zlapano: {e}".splitlines()[0])


# ======================================================================
# 4. Query API
# ======================================================================
def demo_query_api() -> None:
    section("4. QUERY API (find / query / tagi)")

    w = Workflow(name="query-demo")
    t1 = w.add_task(Task(value="train_model", group="ML", cache=True, timeout=30, tags=["gpu", "urgent"]))
    t2 = w.add_task(Task(value="eval_model", group="ML", cache=False, timeout=5, tags=["gpu"]))
    t3 = w.add_task(Task(value="send_report", group="reports", cache=False, retries=2, tags=["email"]))
    t4 = w.add_task(Task(value="cleanup", group="ML", cache=True, timeout=60, tags=[]))
    t1.run_status = RunStatus.SUCCESS
    t2.run_status = RunStatus.FAILED
    t3.run_status = RunStatus.FAILED

    print("  find(status='FAILED'):           ", [t.value for t in w.find(status="FAILED")])
    print("  find(group='ML'):                ", [t.value for t in w.find(group="ML")])
    print("  find(cache=True):                ", [t.value for t in w.find(cache=True)])
    print("  find(tags='gpu'):                ", [t.value for t in w.find(tags="gpu")])
    print("  find(timeout__gt=10):            ", [t.value for t in w.find(timeout__gt=10)])
    print("  find(group='ML', status='FAILED'):", [t.value for t in w.find(group="ML", status="FAILED")])
    print("  query(lambda cache and timeout>10):", [t.value for t in w.query(lambda t: t.cache and t.timeout and t.timeout > 10)])


# ======================================================================
# 5. Graph manipulation API
# ======================================================================
def demo_graph_manipulation() -> None:
    section("5. GRAPH MANIPULATION API")

    def edges_str(wf: Workflow) -> list[str]:
        return sorted(f"{e.source.value}->{e.target.value}" for e in wf.edges)

    print("  -- replace_node --")
    w = Workflow(name="t")
    a = w.add_task(Task(value="a", group="g"))
    b = w.add_task(Task(value="b", group="g"))
    c = w.add_task(Task(value="c", group="g"))
    w.add_edge(a, b, directed=True)
    w.add_edge(b, c, directed=True)
    w.replace_node(b, Task(value="b2", group="g"))
    print("    edges:", edges_str(w))

    print("  -- insert_between --")
    w2 = Workflow(name="t2")
    x = w2.add_task(Task(value="x", group="g"))
    y = w2.add_task(Task(value="y", group="g"))
    w2.add_edge(x, y, directed=True)
    w2.insert_between(x, y, Task(value="z", group="g"))
    print("    edges:", edges_str(w2))

    print("  -- merge --")
    w3 = Workflow(name="t3")
    calls: list[str] = []
    p = w3.add_task(Task(value="p", group="g", function=lambda: calls.append("p")))
    q = w3.add_task(Task(value="q", group="g", function=lambda: calls.append("q")))
    r = w3.add_task(Task(value="r", group="g"))
    w3.add_edge(p, r, directed=True)
    w3.add_edge(q, r, directed=True)
    merged_task = w3.merge(p, q)
    merged_task.function()
    print("    nodes:", [n.value for n in w3.nodes], " edges:", edges_str(w3), " wywolane:", calls)

    print("  -- clone_node --")
    w4 = Workflow(name="t4")
    orig = w4.add_task(Task(value="orig", group="g", tags=["x"]))
    clone = w4.clone_node(orig, value="orig-copy")
    clone.tags.append("y")
    print(f"    orig.tags={orig.tags}  clone.tags={clone.tags}  clone.value={clone.value!r}")

    print("  -- clone_subgraph --")
    w5 = Workflow(name="t5")
    s1 = w5.add_task(Task(value="s1", group="etl"))
    s2 = w5.add_task(Task(value="s2", group="etl"))
    w5.add_edge(s1, s2, directed=True)
    mapping = w5.clone_subgraph(s1, new_group="etl-copy")
    print("    grupa 'etl-copy':", [t.value for t in w5.tasks_by_group("etl-copy")])

    print("  -- extract_subgraph --")
    w6 = Workflow(name="t6")
    m1 = w6.add_task(Task(value="m1", group="g"))
    m2 = w6.add_task(Task(value="m2", group="g"))
    w6.add_edge(m1, m2, directed=True)
    extracted = w6.extract_subgraph(m2, name="wyodrebniony")
    print("    zostalo w oryginale:", [n.value for n in w6.nodes], " nowy workflow:", [n.value for n in extracted.nodes])

    print("  -- move_to_group / copy_group / rename_group --")
    w7 = Workflow(name="t7")
    n1 = w7.add_task(Task(value="n1", group="dev"))
    n2 = w7.add_task(Task(value="n2", group="dev"))
    w7.add_edge(n1, n2, directed=True)
    w7.move_to_group(n1, "staging")
    copies = w7.copy_group("dev", "dev-backup")
    moved = w7.rename_group("staging", "prod")
    print(f"    n1.group={n1.group!r}  dev-backup={[t.value for t in copies]}  przeniesionych przez rename={moved}")


# ======================================================================
# 6. merge_graphs
# ======================================================================
def demo_merge_graphs() -> None:
    section("6. MERGE_GRAPHS (how='union' / 'node' / 'specify')")

    w1 = Workflow(name="etl")
    fetch = w1.add_task(Task(value="fetch", group="etl"))
    clean = w1.add_task(Task(value="clean", group="etl"))
    w1.add_edge(fetch, clean, directed=True)

    w2 = Workflow(name="reports")
    generate = w2.add_task(Task(value="generate", group="reports"))
    send = w2.add_task(Task(value="send", group="reports"))
    w2.add_edge(generate, send, directed=True)

    union_wf = Workflow.merge_graphs(w1, w2, how="union")
    print("  how='union':   nodes =", [n.value for n in union_wf.nodes])

    node_wf = Workflow.merge_graphs(w1, w2, how="node", connections=[(clean, generate)])
    print("  how='node':    plan  =", [t.value for t in node_wf.plan()])

    spec_wf = Workflow.merge_graphs(
        w1, w2, how="specify",
        spec=[{"source": clean, "target": generate, "directed": False, "weight": 5, "description": "most"}],
    )
    bridge = [e for e in spec_wf.edges if e.description == "most"][0]
    print(f"  how='specify': krawedz {bridge.source.value}-{bridge.target.value} directed={bridge.directed} weight={bridge.weight}")


# ======================================================================
# 7. Historia runow
# ======================================================================
def demo_run_history() -> None:
    section("7. HISTORIA RUNOW (Workflow.history + AUTOMATYCZNA trwalosc na dysku)")

    history_path = "/tmp/workflow_auto_history_demo.jsonl"
    if os.path.exists(history_path):
        os.remove(history_path)

    w = Workflow(name="historia-demo", history_path=history_path)
    a = w.add_task(Task(value="a", group="g", function=make_job("a", 0.1)))
    b = w.add_task(Task(value="b", group="g", function=make_job("b", 0.1)))
    w.add_edge(a, b, directed=True)

    print(f"  workflow.history_path = {history_path!r}")
    print("  odpalam 2 razy pod rzad (BEZ zadnego recznego save_history())...")
    Run(workflow=w).execute(group="g")
    Run(workflow=w).execute(group="g")
    print(f"  w.history (w pamieci tego procesu): {len(w.history)} run(y)")
    print(f"  plik na dysku istnieje? {os.path.exists(history_path)}  rozmiar: {os.path.getsize(history_path)} bajtow")

    print("\n  Teraz odpalam ZUPELNIE NOWY, niezalezny proces pythona, ktory")
    print("  nic nie wie o obiekcie 'w' powyzej - i probuje odczytac historie:")
    result = subprocess.run(
        [
            sys.executable, "-c",
            f"from {__package__}.workflow import Workflow\n"
            f"records = Workflow.load_history_file({history_path!r})\n"
            "print(f'    [NOWY PROCES] wczytano {len(records)} run(y) z dysku:')\n"
            "for r in records:\n"
            "    print(f'    [NOWY PROCES]   - workflow={r[\"workflow\"]!r} status={r[\"status\"]!r} taskow={len(r[\"tasks\"])}')\n"
        ],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        capture_output=True, text=True,
    )
    print(result.stdout, end="")
    if result.returncode != 0:
        print("    BLAD w podprocesie:", result.stderr)

    removed = w.clear_history()
    print(f"\n  clear_history() usunal {removed} wpisow z PAMIECI, w.history = {w.history}")
    print(f"  ...ale plik na dysku nadal istnieje: {os.path.exists(history_path)} (clear_history go nie rusza)")


# ======================================================================
# 8. Dry-run
# ======================================================================
def demo_dry_run() -> None:
    section("8. DRY-RUN (Run.dry_run, RunPreview, execute(confirm=True))")

    w = Workflow(name="dry-run-demo")
    a = w.add_task(Task(value="a", group="g", function=make_job("a", 0.1)))
    b = w.add_task(Task(value="b", group="g", function=make_job("b", 0.1)))
    w.add_edge(a, b, directed=True)

    print("  -- dry_run() na poprawnym workflow --")
    preview = Run(workflow=w).dry_run(group="g")
    print(preview)
    assert isinstance(preview, RunPreview)
    assert preview.is_safe_to_run

    print("\n  -- execute(confirm=True), czlowiek ODRZUCA (symulacja input='n') --")
    original_input = builtins.input
    builtins.input = lambda prompt="": "n"
    try:
        run_declined = Run(workflow=w).execute(group="g", confirm=True)
    finally:
        builtins.input = original_input
    print(f"  status po odrzuceniu: {run_declined.status.value}, wykonanych taskow: {len(run_declined.tasks)}")

    print("\n  -- execute(confirm=True), czlowiek AKCEPTUJE (symulacja input='y') --")
    builtins.input = lambda prompt="": "y"
    try:
        run_accepted = Run(workflow=w).execute(group="g", confirm=True)
    finally:
        builtins.input = original_input
    print(f"  status po akceptacji: {run_accepted.status.value}, wykonanych taskow: {len(run_accepted.tasks)}")


def main() -> None:
    w = demo_planner()
    demo_events(w)
    demo_validation()
    demo_query_api()
    demo_graph_manipulation()
    demo_merge_graphs()
    demo_run_history()
    demo_dry_run()
    print("\n" + "=" * 70)
    print("WSZYSTKIE SEKCJE ZAKONCZONE")
    print("=" * 70)


if __name__ == "__main__":
    main()