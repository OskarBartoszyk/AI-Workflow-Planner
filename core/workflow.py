from graph import Node, Edge, Graph, Error, T
from dataclasses import dataclass, field
from typing import Generic, TypeVar, Literal, Any, Callable
import time
import datetime
import uuid
from enum import Enum
from concurrent.futures import ThreadPoolExecutor


# testing func
def CallableFunc():
    for i in range(5):
        print("Thinking...")
        time.sleep(1)
        print("Doing task...")
    print("Done")


class RunStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class CycleError(Exception):
    """Raised when a task graph contains a dependency cycle and therefore
    has no valid execution order."""


@dataclass(slots=True, repr=False)
class Task(Node[T]):
    function: Callable[..., Any] | None = None
    start: datetime.datetime | None = None
    end: datetime.datetime | None = None
    timeout: float | None = None
    retries: int = 0
    cache: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    run_status: RunStatus = RunStatus.PENDING

    def __repr__(self) -> str:
        if self.short:
            return f"Task({self.value!r})"
        return (
            f"Task(value={self.value!r}, id={self.id!r}, group={self.group!r}, "
            f"run_status={self.run_status!r}, retries={self.retries!r}, "
            f"cache={self.cache!r}, metadata={self.metadata!r})"
        )


@dataclass(slots=True, repr=False)
class Workflow(Graph[T]):
    """Graph specialized for orchestrating Task nodes.

    Node/Task groups can be auto-numbered and auto-linked into a sequential
    chain with `build_group_chain` (or all groups at once with
    `build_all_groups`).
    """

    def add_task(self, task: Task[T]) -> Task[T]:
        return self.add_node(task)

    def tasks_by_group(self, group: str | None) -> list[Task[T]]:
        """All Task nodes belonging to `group`, in the order they were added."""
        return [n for n in self.nodes if isinstance(n, Task) and n.group == group]

    def build_group_chain(
        self,
        group: str | None,
        directed: bool = True,
        renumber: bool = True,
        **edge_kwargs: Any,
    ) -> list[Task[T]]:
        """Order the tasks of `group` (by insertion order), (re)number them
        sequentially (id = 1, 2, 3, ... within that group) and connect each
        task to the next one with an edge, forming a linear chain.

        Returns the ordered list of tasks.
        """
        group_tasks = self.tasks_by_group(group)

        if renumber:
            self._id_counters[group] = 0
            for task in group_tasks:
                next_id = self._id_counters.get(group, 0) + 1
                self._id_counters[group] = next_id
                task.id = next_id
                task.auto_id = True

        for prev_task, next_task in zip(group_tasks, group_tasks[1:]):
            self.remove_edges_between(prev_task, next_task)
            self.add_edge(prev_task, next_task, directed=directed, **edge_kwargs)

        return group_tasks

    def build_all_groups(
        self,
        directed: bool = True,
        renumber: bool = True,
        **edge_kwargs: Any,
    ) -> dict[str | None, list[Task[T]]]:
        """Run `build_group_chain` for every distinct group found in `self.nodes`."""
        seen_groups: list[str | None] = []
        for n in self.nodes:
            if n.group not in seen_groups:
                seen_groups.append(n.group)

        return {
            group: self.build_group_chain(
                group, directed=directed, renumber=renumber, **edge_kwargs
            )
            for group in seen_groups
        }

    # ------------------------------------------------------------------
    # Planner: figures out a valid execution order from the dependency
    # graph itself (directed edges = "source must run before target"),
    # instead of assuming a fixed chain.
    # ------------------------------------------------------------------

    def _topo_sort(self, tasks: list[Task[T]]) -> list[Task[T]]:
        """Kahn's algorithm scoped to `tasks`. Only directed edges between
        two tasks that are both in `tasks` count as dependencies. Ties
        (several tasks ready at once) are broken by `id`, falling back to
        insertion order, so the result is deterministic."""
        task_ids = {id(t) for t in tasks}
        in_degree: dict[int, int] = {id(t): 0 for t in tasks}
        adjacency: dict[int, list[Task[T]]] = {id(t): [] for t in tasks}

        for e in self.edges:
            if not e.directed:
                continue
            if id(e.source) in task_ids and id(e.target) in task_ids:
                adjacency[id(e.source)].append(e.target)
                in_degree[id(e.target)] += 1

        insertion_order = {id(t): i for i, t in enumerate(tasks)}

        def sort_key(t: Task[T]):
            return (t.id if t.id is not None else float("inf"), insertion_order[id(t)])

        ready = sorted((t for t in tasks if in_degree[id(t)] == 0), key=sort_key)
        ordered: list[Task[T]] = []

        while ready:
            current = ready.pop(0)
            ordered.append(current)
            for nxt in adjacency[id(current)]:
                in_degree[id(nxt)] -= 1
                if in_degree[id(nxt)] == 0:
                    ready.append(nxt)
            ready.sort(key=sort_key)

        if len(ordered) != len(tasks):
            stuck = [t for t in tasks if t not in ordered]
            raise CycleError(
                f"Dependency cycle detected, cannot compute execution order. "
                f"Tasks stuck in the cycle: {[t.value for t in stuck]!r}"
            )

        return ordered

    def plan_group(self, group: str | None) -> list[Task[T]]:
        """Return the tasks of `group` in a valid execution order, derived
        from their directed dependency edges (not just insertion order).
        Raises CycleError if the group's dependencies form a cycle."""
        return self._topo_sort(self.tasks_by_group(group))

    def plan(self) -> list[Task[T]]:
        """Return ALL tasks in the workflow in a valid execution order,
        derived from directed dependency edges across the whole graph
        (groups are ignored for ordering purposes here).
        Raises CycleError if the workflow has a dependency cycle."""
        all_tasks = [n for n in self.nodes if isinstance(n, Task)]
        return self._topo_sort(all_tasks)

    def _topo_levels(self, tasks: list[Task[T]]) -> list[list[Task[T]]]:
        """Like `_topo_sort`, but groups tasks into 'levels': every task in
        the same level has no dependency on any other task in that same
        level, so they can all be started at the same time. Level N can
        only start once every task in level N-1 has finished."""
        task_ids = {id(t) for t in tasks}
        in_degree: dict[int, int] = {id(t): 0 for t in tasks}
        adjacency: dict[int, list[Task[T]]] = {id(t): [] for t in tasks}

        for e in self.edges:
            if not e.directed:
                continue
            if id(e.source) in task_ids and id(e.target) in task_ids:
                adjacency[id(e.source)].append(e.target)
                in_degree[id(e.target)] += 1

        insertion_order = {id(t): i for i, t in enumerate(tasks)}
        sort_key = lambda t: insertion_order[id(t)]

        current_level = sorted((t for t in tasks if in_degree[id(t)] == 0), key=sort_key)
        levels: list[list[Task[T]]] = []
        seen = 0

        while current_level:
            levels.append(current_level)
            seen += len(current_level)
            next_level: list[Task[T]] = []
            for task in current_level:
                for nxt in adjacency[id(task)]:
                    in_degree[id(nxt)] -= 1
                    if in_degree[id(nxt)] == 0:
                        next_level.append(nxt)
            current_level = sorted(next_level, key=sort_key)

        if seen != len(tasks):
            stuck = [t for t in tasks if t not in [x for lvl in levels for x in lvl]]
            raise CycleError(
                f"Dependency cycle detected, cannot compute execution order. "
                f"Tasks stuck in the cycle: {[t.value for t in stuck]!r}"
            )

        return levels

    def plan_levels_group(self, group: str | None) -> list[list[Task[T]]]:
        """Same as `plan_group`, but instead of one flat list returns a list
        of 'levels' (batches). All tasks within a level have no dependency
        on each other and can be run in parallel; each level waits for the
        previous one to fully finish."""
        return self._topo_levels(self.tasks_by_group(group))

    def plan_levels(self) -> list[list[Task[T]]]:
        """Same as `plan`, but returns levels (batches) for parallel
        execution instead of one flat sequential list."""
        all_tasks = [n for n in self.nodes if isinstance(n, Task)]
        return self._topo_levels(all_tasks)

    def run_parallel(self, group: str | None = None, max_workers: int | None = None) -> None:
        """Actually execute the tasks (calls `task.function()`), level by
        level. Within a level, tasks run in parallel threads; the next
        level only starts once the whole current level has finished.
        Sets `task.run_status` and `task.start` / `task.end` along the way.

        If `group` is given, only that group's tasks are executed;
        otherwise the whole workflow runs.
        """
        levels = self.plan_levels_group(group) if group is not None else self.plan_levels()

        def _run_one(task: Task[T]) -> None:
            task.run_status = RunStatus.RUNNING
            task.start = datetime.datetime.now()
            try:
                if task.function is not None:
                    task.function()
                task.run_status = RunStatus.SUCCESS
            except Exception:
                task.run_status = RunStatus.FAILED
                raise
            finally:
                task.end = datetime.datetime.now()

        for level in levels:
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                list(pool.map(_run_one, level))


@dataclass(slots=True, repr=False)
class TaskRun(Generic[T]):
    """A single recorded execution attempt of one Task, produced by `Run`.
    Kept separate from `Task` itself so one `Task` definition can be
    executed by several different `Run`s over time without them
    overwriting each other's history."""

    task: Task[T]
    status: RunStatus = RunStatus.PENDING
    attempt: int = 1
    started_at: datetime.datetime | None = None
    finished_at: datetime.datetime | None = None
    error: Error | None = None

    @property
    def duration(self) -> float | None:
        if self.started_at is None or self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()

    def __repr__(self) -> str:
        return (
            f"TaskRun(task={self.task.value!r}, status={self.status.value!r}, "
            f"attempt={self.attempt!r}, duration={self.duration!r})"
        )


@dataclass(slots=True, repr=False)
class Run(Generic[T]):
    """Executes an entire Workflow (or a single group of it) and keeps a
    record of what happened: overall status, timing, and one `TaskRun`
    per executed task (including retries)."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    workflow: "Workflow[T] | None" = None
    status: RunStatus = RunStatus.PENDING
    started_at: datetime.datetime | None = None
    finished_at: datetime.datetime | None = None
    tasks: list[TaskRun[T]] = field(default_factory=list)
    error: Error | None = None
    max_workers: int | None = None

    def __repr__(self) -> str:
        return (
            f"Run(id={self.id!r}, status={self.status.value!r}, "
            f"tasks={len(self.tasks)!r}, error={self.error!r})"
        )

    def _execute_one(self, task: Task[T]) -> TaskRun[T]:
        """Run a single task, retrying up to `task.retries` extra times on
        failure. Returns the TaskRun record for its (last) attempt."""
        attempts_allowed = max(task.retries, 0) + 1
        task_run = TaskRun(task=task)

        for attempt in range(1, attempts_allowed + 1):
            task_run.attempt = attempt
            task_run.status = RunStatus.RUNNING
            task_run.started_at = datetime.datetime.now()
            task.run_status = RunStatus.RUNNING
            task.start = task_run.started_at

            try:
                if task.function is not None:
                    task.function()
                task_run.status = RunStatus.SUCCESS
                task.run_status = RunStatus.SUCCESS
                task_run.error = None
                break
            except Exception as exc:
                task_run.status = RunStatus.FAILED
                task.run_status = RunStatus.FAILED
                task_run.error = Error(str(exc))
            finally:
                task_run.finished_at = datetime.datetime.now()
                task.end = task_run.finished_at

        return task_run

    def execute(self, group: str | None = None) -> "Run[T]":
        """Runs `self.workflow` (or just `group` within it) level by level,
        in parallel within each level. Stops after a level that contains a
        failed task (so nothing depending on it runs), and marks the whole
        Run as FAILED. Returns self, so you can do `run = Run(workflow=w).execute()`.
        """
        if self.workflow is None:
            raise ValueError("Run.workflow is not set - nothing to execute.")

        self.status = RunStatus.RUNNING
        self.started_at = datetime.datetime.now()
        self.tasks = []
        self.error = None

        try:
            levels = (
                self.workflow.plan_levels_group(group)
                if group is not None
                else self.workflow.plan_levels()
            )
        except CycleError as exc:
            self.status = RunStatus.FAILED
            self.error = Error(str(exc))
            self.finished_at = datetime.datetime.now()
            return self

        failed = False
        for level in levels:
            with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                task_runs = list(pool.map(self._execute_one, level))
            self.tasks.extend(task_runs)

            level_failures = [tr for tr in task_runs if tr.status == RunStatus.FAILED]
            if level_failures:
                failed = True
                self.error = level_failures[0].error
                break  # do not start the next level - its deps failed

        self.status = RunStatus.FAILED if failed else RunStatus.SUCCESS
        self.finished_at = datetime.datetime.now()
        return self