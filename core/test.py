"""
Pelny test Workflow / Task / Run / planera.

Tworzy 3 grupy task-ow:
  1) "etl"            - diament: fetch -> (clean, validate) -> load
  2) "reports"         - prosty lancuch: generate -> format -> send
  3) "notifications"   - 3 taski calkowicie niezalezne od siebie

Kazdy task "pracuje" przez zadany czas (time.sleep) i wypisuje w konsoli
dokladny moment startu / konca. Kazda grupa jest odpalana przez osobny
obiekt `Run`, ktory zwraca pelna historie wykonania (TaskRun per task).

Na koncu dodatkowo:
  - test wykrywania cyklu w zaleznosciach (CycleError),
  - test Run-a na workflow gdzie jeden task pada (z retry) i pokazuje,
    ze zalezne od niego taski w ogole sie nie odpalaja.

Uruchomienie (z folderu NADRZEDNEGO wzgledem paczki z workflow.py/graph.py):
    python -m twoja_paczka.test_workflow

Jesli workflow.py / graph.py NIE sa czescia paczki (nie ma importow
relatywnych ".graph"), zamien import ponizej na:
    from workflow import Task, Workflow, Run, RunStatus, CycleError
"""

import time
import datetime

from workflow import Task, Workflow, Run, RunStatus, CycleError


# ----------------------------------------------------------------------
# Pomocnicza funkcja: tworzy "prace" dla taska - symuluje robote trwajaca
# `duration` sekund i loguje w konsoli moment startu/konca.
# ----------------------------------------------------------------------
def make_job(name: str, duration: float):
    def _job() -> None:
        ts_start = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(f"    [{ts_start}] -> START  {name:<12} (potrwa {duration:.1f}s)")
        time.sleep(duration)
        ts_end = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(f"    [{ts_end}] <- KONIEC {name:<12}")
    return _job


def print_run_summary(group_name: str, run: Run) -> None:
    wall_time = (run.finished_at - run.started_at).total_seconds()
    sequential_time = sum(tr.duration or 0.0 for tr in run.tasks)

    print(f"\n  Podsumowanie Run-a dla grupy '{group_name}' (run_id={run.id}):")
    print(f"    status Run-a: {run.status.value}")
    for tr in run.tasks:
        print(
            f"    - {tr.task.value:<12} status={tr.status.value:<8} "
            f"proba={tr.attempt} czas={tr.duration:.2f}s (id={tr.task.id})"
        )
    print(f"    Czas rzeczywisty (rownolegle): {wall_time:.2f}s")
    print(f"    Czas gdyby sekwencyjnie:        {sequential_time:.2f}s")
    print(f"    Zysk:                           {sequential_time - wall_time:.2f}s")
    if run.error is not None:
        print(f"    Blad: {run.error}")


def main() -> None:
    w = Workflow(name="demo-workflow")

    # ------------------------------------------------------------------
    # Grupa 1: "etl" - diament (fetch rozgalezia sie na clean i validate,
    # ktore obie musza sie skonczyc zanim wystartuje load)
    # ------------------------------------------------------------------
    fetch = w.add_task(Task(value="fetch", group="etl", function=make_job("fetch", 2)))
    clean = w.add_task(Task(value="clean", group="etl", function=make_job("clean", 30)))
    validate = w.add_task(Task(value="validate", group="etl", function=make_job("validate", 10)))
    load = w.add_task(Task(value="load", group="etl", function=make_job("load", 3)))

    w.add_edge(fetch, clean, directed=True)
    w.add_edge(fetch, validate, directed=True)
    w.add_edge(clean, load, directed=True)
    w.add_edge(validate, load, directed=True)

    # ------------------------------------------------------------------
    # Grupa 2: "reports" - prosty lancuch, budowany automatycznie
    # (kazdy krok czeka na poprzedni)
    # ------------------------------------------------------------------
    w.add_task(Task(value="generate", group="reports", function=make_job("generate", 5)))
    w.add_task(Task(value="format", group="reports", function=make_job("format", 3)))
    w.add_task(Task(value="send", group="reports", function=make_job("send", 2)))
    w.build_group_chain("reports")  # generate -> format -> send

    # ------------------------------------------------------------------
    # Grupa 3: "notifications" - 3 taski calkowicie od siebie niezalezne,
    # wiec caly czas moga isc rownolegle
    # ------------------------------------------------------------------
    w.add_task(Task(value="email", group="notifications", function=make_job("email", 5)))
    w.add_task(Task(value="sms", group="notifications", function=make_job("sms", 3)))
    w.add_task(Task(value="push", group="notifications", function=make_job("push", 2)))

    # ------------------------------------------------------------------
    # Test 1: podglad planu (kolejnosc) i poziomow rownoleglosci, zanim
    # cokolwiek faktycznie odpalimy
    # ------------------------------------------------------------------
    for group in ("etl", "reports", "notifications"):
        order = w.plan_group(group)
        levels = w.plan_levels_group(group)
        levels_str = " -> ".join(str([t.value for t in lvl]) for lvl in levels)
        print(f"Plan '{group}': {[t.value for t in order]}")
        print(f"Poziomy '{group}': {levels_str}")

    # ------------------------------------------------------------------
    # Test 2: faktyczne uruchomienie kazdej grupy przez osobny Run
    # ------------------------------------------------------------------
    for group_name in ("etl", "reports", "notifications"):
        print(f"\n=== Run dla grupy '{group_name}' ===")
        run = Run(workflow=w).execute(group=group_name)
        print_run_summary(group_name, run)

    # ------------------------------------------------------------------
    # Test 3: cykl POWINIEN zostac wykryty (osobny, malutki workflow)
    # ------------------------------------------------------------------
    print("\n=== Test wykrywania cyklu (osobny mini-workflow) ===")
    w_cycle = Workflow(name="broken")
    a = w_cycle.add_task(Task(value="a", group="x"))
    b = w_cycle.add_task(Task(value="b", group="x"))
    c = w_cycle.add_task(Task(value="c", group="x"))
    w_cycle.add_edge(a, b, directed=True)
    w_cycle.add_edge(b, c, directed=True)
    w_cycle.add_edge(c, a, directed=True)  # a <- b <- c <- a : cykl

    try:
        w_cycle.plan_group("x")
        print("BLAD: cykl powinien zostac wykryty, a nie zostal!")
    except CycleError as e:
        print(f"OK, cykl poprawnie wykryty: {e}")

    run_cycle = Run(workflow=w_cycle).execute(group="x")
    print(f"Run na workflow z cyklem: status={run_cycle.status.value}, error={run_cycle.error}")

    # ------------------------------------------------------------------
    # Test 4: task ktory pada (z retry) - sprawdzamy, ze Run:
    #   - probuje ponownie zgodnie z task.retries,
    #   - zatrzymuje sie i NIE odpala tasku zaleznego od tego, ktory padl
    # ------------------------------------------------------------------
    print("\n=== Test Run-a z tasekiem, ktory pada (retries) ===")

    attempts_made = {"count": 0}

    def flaky_job() -> None:
        attempts_made["count"] += 1
        print(f"    proba {attempts_made['count']} tasku 'b'...")
        raise RuntimeError("cos poszlo nie tak w tasku b")

    w_fail = Workflow(name="broken-run")
    a2 = w_fail.add_task(Task(value="a", group="g", function=make_job("a", 2)))
    b2 = w_fail.add_task(Task(value="b", group="g", function=flaky_job, retries=2))
    c2 = w_fail.add_task(Task(value="c", group="g", function=make_job("c", 0.1)))
    w_fail.add_edge(a2, b2, directed=True)
    w_fail.add_edge(b2, c2, directed=True)

    run_fail = Run(workflow=w_fail).execute(group="g")
    print(f"\n  status Run-a: {run_fail.status.value}")
    print(f"  blad: {run_fail.error}")
    for tr in run_fail.tasks:
        print(f"    - {tr.task.value:<8} status={tr.status.value:<8} proba={tr.attempt}")
    wykonane = {tr.task.value for tr in run_fail.tasks}
    print(f"  task 'c' zostal odpalony? {'c' in wykonane} (powinno byc False)")


if __name__ == "__main__":
    main()