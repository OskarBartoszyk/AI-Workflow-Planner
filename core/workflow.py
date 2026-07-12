from .graph import Node, Edge, Graph, Error, T
from dataclasses import dataclass, field
from typing import Generic, TypeVar, Literal, Any, Callable, ClassVar
import time
import datetime
import uuid
import operator
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
class ValidationIssue:
    """A single problem found by `Workflow.validate()`. `level="error"`
    means the workflow cannot safely run as-is; `level="warning"` means it
    can run, but something looks off and probably deserves a look."""

    level: Literal["error", "warning"]
    code: str
    message: str
    group: str | None = None
    task: "Task[Any] | None" = None

    def __repr__(self) -> str:
        location = f" group={self.group!r}" if self.group is not None else ""
        task_part = f" task={self.task.value!r}" if self.task is not None else ""
        return f"[{self.level.upper()}:{self.code}]{location}{task_part} {self.message}"


class ValidationError(Exception):
    """Raised by `Workflow.validate(strict=True)` when at least one
    error-level ValidationIssue was found."""

    def __init__(self, issues: list[ValidationIssue]):
        self.issues = issues
        errors = [i for i in issues if i.level == "error"]
        message = f"{len(errors)} validation error(s) found:\n" + "\n".join(
            f"  - {i}" for i in errors
        )
        super().__init__(message)


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
    tags: list[str] = field(default_factory=list)

    def __repr__(self) -> str:
        if self.short:
            return f"Task({self.value!r})"
        return (
            f"Task(value={self.value!r}, id={self.id!r}, group={self.group!r}, "
            f"run_status={self.run_status!r}, retries={self.retries!r}, "
            f"cache={self.cache!r}, tags={self.tags!r}, metadata={self.metadata!r})"
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

    # ------------------------------------------------------------------
    # Query API: find tasks by attribute without writing a manual loop.
    # ------------------------------------------------------------------

    _LOOKUP_OPS: ClassVar[dict[str, Callable[[Any, Any], bool]]] = {
        "gt": operator.gt,
        "gte": operator.ge,
        "lt": operator.lt,
        "lte": operator.le,
        "ne": operator.ne,
        "in": lambda value, choices: value in choices,
        "contains": lambda value, item: item in value if value is not None else False,
    }

    @staticmethod
    def _coerce_run_status(value: Any) -> RunStatus:
        if isinstance(value, RunStatus):
            return value
        if isinstance(value, str):
            return RunStatus[value.split(".")[-1].upper()]
        raise TypeError(f"Cannot interpret {value!r} as a RunStatus")

    def find(self, **filters: Any) -> list[Task[T]]:
        """Filter all Task nodes in the workflow by attribute, e.g.:

            w.find(status="FAILED")            # matches task.run_status
            w.find(group="ML")
            w.find(cache=True)
            w.find(tags="gpu")                 # task must have this tag
            w.find(tags=["gpu", "urgent"])     # must have ALL of these tags
            w.find(timeout__gt=10)             # Django-style lookups:
            w.find(retries__gte=1)             #   __gt __gte __lt __lte
                                                #   __ne __in __contains
            w.find(group="ML", status="FAILED")  # filters combine with AND

        `status=...` is special-cased to match `task.run_status` (accepts a
        RunStatus, or a string like "FAILED"/"failed"). Any other plain
        (no "__") key is a straight `getattr(task, key) == value` check.
        Raises AttributeError up front if a filter key doesn't exist on
        Task at all, to catch typos early instead of silently matching 0.
        """
        tasks: list[Task[T]] = [n for n in self.nodes if isinstance(n, Task)]

        for key, expected in filters.items():
            attr, sep, lookup = key.partition("__")

            if attr not in Task.__dataclass_fields__:
                raise AttributeError(f"Task has no field {attr!r} (filter {key!r})")

            if sep:  # e.g. "timeout__gt"
                op_func = self._LOOKUP_OPS.get(lookup)
                if op_func is None:
                    raise ValueError(f"Unknown lookup '__{lookup}' in filter {key!r}")
                tasks = [
                    t for t in tasks
                    if getattr(t, attr) is not None and op_func(getattr(t, attr), expected)
                ]
            elif attr == "status":
                wanted_status = self._coerce_run_status(expected)
                tasks = [t for t in tasks if t.run_status == wanted_status]
            elif attr == "tags":
                wanted_tags = {expected} if isinstance(expected, str) else set(expected)
                tasks = [t for t in tasks if wanted_tags.issubset(set(t.tags))]
            else:
                tasks = [t for t in tasks if getattr(t, attr) == expected]

        return tasks

    def query(self, predicate: Callable[[Task[T]], bool]) -> list[Task[T]]:
        """Filter Task nodes with an arbitrary predicate, for anything
        `find()` can't express directly, e.g.:

            w.query(lambda t: t.timeout is not None and t.timeout > 10 and t.cache)
        """
        return [n for n in self.nodes if isinstance(n, Task) and predicate(n)]

    # ------------------------------------------------------------------
    # Graph manipulation API: the editing primitives a human (or a future
    # UI/playground) needs when reshaping an EXISTING graph, beyond just
    # add_node/remove_node. Task isn't hashable (dataclass eq=True with no
    # explicit __hash__), so everything here compares by identity (`is`)
    # or by `id(...)`, never by `==`/`in`.
    # ------------------------------------------------------------------

    def _has_node(self, task: Task[T]) -> bool:
        return any(n is task for n in self.nodes)

    def replace_node(self, old: Task[T], new: Task[T]) -> Task[T]:
        """Swaps `old` for `new` everywhere in the graph: every edge that
        pointed to/from `old` now points to/from `new` instead. `old` is
        removed from the workflow; `new` is added if not already present."""
        if not self._has_node(old):
            raise ValueError(f"{old!r} is not part of this workflow")
        if not self._has_node(new):
            self.add_node(new)

        for e in self.edges:
            if e.source is old:
                e.source = new
            if e.target is old:
                e.target = new

        self.nodes = [n for n in self.nodes if n is not old]
        return new

    def insert_between(
        self, a: Task[T], b: Task[T], new: Task[T], directed: bool = True, **edge_kwargs: Any
    ) -> Task[T]:
        """Inserts `new` on the path between `a` and `b`: removes the
        direct edge(s) between them and wires a -> new -> b instead."""
        if not self._has_node(new):
            self.add_node(new)
        self.remove_edges_between(a, b)
        self.add_edge(a, new, directed=directed, **edge_kwargs)
        self.add_edge(new, b, directed=directed, **edge_kwargs)
        return new

    def merge(self, task1: Task[T], task2: Task[T], into: Task[T] | None = None) -> Task[T]:
        """Collapses `task1` and `task2` into a single task. Every edge
        that touched either of them now touches `into` instead (self-loops
        and exact-duplicate edges created by the merge are cleaned up
        afterwards).

        If `into` isn't given, a new Task is created whose function runs
        `task1.function` then `task2.function` in sequence, with the union
        of their tags/metadata, placed in task1's group."""
        if not self._has_node(task1) or not self._has_node(task2):
            raise ValueError("both tasks being merged must already be part of this workflow")

        if into is None:
            fn1, fn2 = task1.function, task2.function

            def _combined(
                _fn1: Callable[..., Any] | None = fn1, _fn2: Callable[..., Any] | None = fn2
            ) -> None:
                if _fn1 is not None:
                    _fn1()
                if _fn2 is not None:
                    _fn2()

            into = Task(
                value=f"{task1.value}+{task2.value}",
                group=task1.group,
                function=_combined,
                tags=sorted(set(task1.tags) | set(task2.tags)),
                metadata={**task1.metadata, **task2.metadata},
            )

        if not self._has_node(into):
            self.nodes.append(into)

        for e in self.edges:
            if e.source is task1 or e.source is task2:
                e.source = into
            if e.target is task1 or e.target is task2:
                e.target = into

        # drop self-loops created by the merge (into -> into)
        self.edges = [e for e in self.edges if not (e.source is into and e.target is into)]

        # drop exact duplicate edges (same source/target/direction) created by the merge
        seen: set[tuple[int, int, bool]] = set()
        deduped: list[Edge[T]] = []
        for e in self.edges:
            key = (id(e.source), id(e.target), e.directed)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(e)
        self.edges = deduped

        self.nodes = [n for n in self.nodes if n is not task1 and n is not task2]
        return into

    def clone_node(self, task: Task[T], **overrides: Any) -> Task[T]:
        """Creates a new, independent Task with the same field values as
        `task` (mutable fields like `metadata`/`tags` are copied, not
        shared). The clone starts with NO edges and a fresh (unset) id.
        Pass keyword overrides to change specific fields, e.g.
        `clone_node(t, value="t (retry)")`."""
        field_values = {name: getattr(task, name) for name in Task.__dataclass_fields__}
        field_values["metadata"] = dict(task.metadata)
        field_values["tags"] = list(task.tags)
        field_values["id"] = None
        field_values.update(overrides)
        cloned = Task(**field_values)
        self.add_node(cloned)
        return cloned

    def _downstream(self, start: Task[T]) -> list[Task[T]]:
        """`start` plus every task reachable from it via directed edges."""
        to_visit = [start]
        visited: list[Task[T]] = []
        seen_ids: set[int] = set()
        while to_visit:
            current = to_visit.pop()
            if id(current) in seen_ids:
                continue
            seen_ids.add(id(current))
            visited.append(current)
            for e in self.edges:
                if e.directed and e.source is current:
                    to_visit.append(e.target)
        return visited

    def clone_subgraph(self, start: Task[T], new_group: str | None = None) -> dict[int, Task[T]]:
        """Clones `start` and everything downstream of it (reachable via
        directed edges), including the edges between them, as brand new
        standalone tasks - NOT connected back to the rest of the graph.

        Returns a mapping {id(original_task): cloned_task}. Task isn't
        hashable, so the mapping is keyed by `id(...)`; if you're holding
        the original task variable `t`, look up its clone with
        `mapping[id(t)]`."""
        originals = self._downstream(start)
        clone_by_id: dict[int, Task[T]] = {
            id(original): self.clone_node(
                original, group=new_group if new_group is not None else original.group
            )
            for original in originals
        }

        for e in self.edges:
            if id(e.source) in clone_by_id and id(e.target) in clone_by_id:
                self.add_edge(
                    clone_by_id[id(e.source)], clone_by_id[id(e.target)],
                    directed=e.directed, weight=e.weight, description=e.description,
                )

        return clone_by_id

    def extract_subgraph(self, start: Task[T], name: str | None = None) -> "Workflow[T]":
        """Moves `start` and everything downstream of it OUT of this
        workflow into a brand new `Workflow`. The SAME Task objects move
        (not clones) - any TaskRun history already recorded for them still
        refers to the right task. Edges between extracted tasks move with
        them; an edge connecting an extracted task to one left behind is
        simply dropped."""
        extracted = self._downstream(start)
        extracted_ids = {id(t) for t in extracted}

        moved_edges = [
            e for e in self.edges
            if id(e.source) in extracted_ids and id(e.target) in extracted_ids
        ]
        dropped_edge_ids = {
            id(e) for e in self.edges
            if id(e.source) in extracted_ids or id(e.target) in extracted_ids
        }

        new_workflow: "Workflow[T]" = Workflow(name=name or f"{self.name}-extracted")
        new_workflow.nodes.extend(extracted)
        new_workflow.edges.extend(moved_edges)

        self.nodes = [n for n in self.nodes if id(n) not in extracted_ids]
        self.edges = [e for e in self.edges if id(e) not in dropped_edge_ids]

        return new_workflow

    def move_to_group(self, task: Task[T], new_group: str | None) -> Task[T]:
        """Reassigns `task.group`. Doesn't touch `task.id` - call
        `build_group_chain`/renumber the target group afterwards if you
        want fresh sequential ids there."""
        if not self._has_node(task):
            raise ValueError(f"{task!r} is not part of this workflow")
        task.group = new_group
        return task

    def copy_group(self, group: str | None, new_group: str | None) -> list[Task[T]]:
        """Clones every task currently in `group` into `new_group` (brand
        new Task objects), including the edges between them. Returns the
        new tasks in the same order as the originals."""
        originals = self.tasks_by_group(group)
        clone_by_id: dict[int, Task[T]] = {
            id(original): self.clone_node(original, group=new_group) for original in originals
        }

        for e in self.edges:
            if id(e.source) in clone_by_id and id(e.target) in clone_by_id:
                self.add_edge(
                    clone_by_id[id(e.source)], clone_by_id[id(e.target)],
                    directed=e.directed, weight=e.weight, description=e.description,
                )

        return [clone_by_id[id(t)] for t in originals]

    def rename_group(self, old_group: str | None, new_group: str | None) -> int:
        """Reassigns every task in `old_group` to `new_group`. Returns how
        many tasks were moved. Carries over the auto-numbering counter so
        future `auto_id` numbering in the renamed group continues where
        the old one left off."""
        tasks = self.tasks_by_group(old_group)
        for t in tasks:
            t.group = new_group

        if old_group in self._id_counters:
            self._id_counters[new_group] = max(
                self._id_counters.get(new_group, 0), self._id_counters.pop(old_group)
            )

        return len(tasks)

    @staticmethod
    def merge_graphs(
        *workflows: "Workflow[T]",
        how: Literal["union", "node", "specify"] = "union",
        connections: list[tuple[Task[T], Task[T]]] | None = None,
        spec: list[dict[str, Any]] | None = None,
        name: str | None = None,
    ) -> "Workflow[T]":
        """Combines 2+ existing Workflow objects into one brand new
        Workflow. The SAME Task/Edge objects are reused (not cloned) - the
        merged workflow just references them, so running it also updates
        `task.run_status` etc. on the originals too.

        Every node/edge already inside each given workflow is always
        carried over. `how` controls whether/how NEW edges get added to
        bridge the separate graphs together:

          how="union" (default) - just combine them side by side, no
            bridging edges added. Useful when the graphs should stay
            independent but you want one Workflow to validate/run/observe
            them together (e.g. `merged.plan_levels()` will naturally run
            each original graph in parallel, since nothing connects them).

          how="node" - bridge specific tasks together with a simple
            directed edge each. Pass
            `connections=[(task_a, task_b), (task_c, task_d), ...]`:
            for every pair, an edge task_a -> task_b is added. Each task
            must already belong to one of the given workflows.

          how="specify" - full control over every bridging edge. Pass
            `spec=[{"source": task_a, "target": task_b, "directed": True,
                    "weight": 1, "description": "..."}, ...]` - any Edge
            field can be set per connection (undirected bridges, weights,
            descriptions, ...).

        Raises ValueError if fewer than 2 workflows are given, a required
        parameter for the chosen `how` is missing, or a connection/spec
        entry references a task that isn't part of any given workflow.
        """
        if len(workflows) < 2:
            raise ValueError("merge_graphs needs at least 2 workflows to merge")

        merged: "Workflow[T]" = Workflow(
            name=name or "+".join(wf.name or "workflow" for wf in workflows)
        )
        for wf in workflows:
            merged.nodes.extend(wf.nodes)
            merged.edges.extend(wf.edges)
            for group_key, count in wf._id_counters.items():
                merged._id_counters[group_key] = max(merged._id_counters.get(group_key, 0), count)

        if how == "union":
            if connections or spec:
                raise ValueError(
                    "how='union' doesn't take 'connections'/'spec' - use how='node' or how='specify'"
                )
            return merged

        if how == "node":
            if not connections:
                raise ValueError("how='node' requires connections=[(task_a, task_b), ...]")
            for source, target in connections:
                if not merged._has_node(source) or not merged._has_node(target):
                    raise ValueError(
                        f"connection ({source.value!r} -> {target.value!r}) references a task "
                        f"that isn't part of any of the given workflows"
                    )
                merged.add_edge(source, target, directed=True)
            return merged

        if how == "specify":
            if not spec:
                raise ValueError(
                    'how=\'specify\' requires spec=[{"source":..., "target":..., ...}, ...]'
                )
            for edge_spec in spec:
                edge_spec = dict(edge_spec)  # don't mutate the caller's dict
                source = edge_spec.pop("source")
                target = edge_spec.pop("target")
                if not merged._has_node(source) or not merged._has_node(target):
                    raise ValueError(
                        f"spec entry ({source.value!r} -> {target.value!r}) references a task "
                        f"that isn't part of any of the given workflows"
                    )
                merged.add_edge(source, target, **edge_spec)
            return merged

        raise ValueError(f"Unknown how={how!r}, expected 'union', 'node', or 'specify'")

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

    # ------------------------------------------------------------------
    # Validation: sanity-checks a group (or the whole workflow) BEFORE
    # anything runs, so problems surface as a clear report instead of a
    # confusing failure (or worse - silence) mid-execution.
    # ------------------------------------------------------------------

    def validate_group(self, group: str | None) -> list[ValidationIssue]:
        """Checks one group for common problems: duplicate ids, tasks with
        no function, negative timeouts, missing start node (every task has
        an incoming edge -> pure cycle), unreachable tasks (not reachable
        from any start task -> isolated cycle), and dependency cycles.
        Returns a list of ValidationIssue; an empty list means "all good".
        """
        tasks = self.tasks_by_group(group)
        issues: list[ValidationIssue] = []

        # 1) duplicate ids within the group
        seen_ids: dict[int, Task[T]] = {}
        for t in tasks:
            if t.id is not None:
                if t.id in seen_ids:
                    issues.append(ValidationIssue(
                        "error", "duplicate_id",
                        f"id {t.id!r} is used by both {seen_ids[t.id].value!r} and {t.value!r}",
                        group=group, task=t,
                    ))
                else:
                    seen_ids[t.id] = t

        # 2) tasks with no function attached - they will do nothing when run
        for t in tasks:
            if t.function is None:
                issues.append(ValidationIssue(
                    "warning", "missing_function",
                    f"task {t.value!r} has no function set - it will do nothing when run",
                    group=group, task=t,
                ))

        # 3) negative timeout
        for t in tasks:
            if t.timeout is not None and t.timeout < 0:
                issues.append(ValidationIssue(
                    "error", "invalid_timeout",
                    f"task {t.value!r} has a negative timeout ({t.timeout!r})",
                    group=group, task=t,
                ))

        # 4) structural checks: start node, reachability, cycles
        task_ids = {id(t) for t in tasks}
        in_degree: dict[int, int] = {id(t): 0 for t in tasks}
        adjacency: dict[int, list[Task[T]]] = {id(t): [] for t in tasks}
        for e in self.edges:
            if not e.directed:
                continue
            if id(e.source) in task_ids and id(e.target) in task_ids:
                adjacency[id(e.source)].append(e.target)
                in_degree[id(e.target)] += 1

        start_tasks = [t for t in tasks if in_degree[id(t)] == 0]
        if tasks and not start_tasks:
            issues.append(ValidationIssue(
                "error", "no_start_node",
                f"group {group!r} has {len(tasks)} task(s) but none of them has zero "
                f"incoming edges - every task depends on something, so nothing can start "
                f"(the whole group is likely one big cycle)",
                group=group,
            ))

        reachable: set[int] = {id(t) for t in start_tasks}
        queue = list(start_tasks)
        while queue:
            current = queue.pop()
            for nxt in adjacency[id(current)]:
                if id(nxt) not in reachable:
                    reachable.add(id(nxt))
                    queue.append(nxt)
        for t in tasks:
            if id(t) not in reachable:
                issues.append(ValidationIssue(
                    "error", "unreachable_task",
                    f"task {t.value!r} is not reachable from any start task in group "
                    f"{group!r} (likely sitting in its own isolated cycle)",
                    group=group, task=t,
                ))

        try:
            self._topo_sort(tasks)
        except CycleError as exc:
            issues.append(ValidationIssue("error", "cycle_detected", str(exc), group=group))

        return issues

    def validate(self, strict: bool = False) -> list[ValidationIssue]:
        """Runs `validate_group` for every group found in the workflow.
        If `strict=True` and any error-level issue was found, raises
        ValidationError instead of just returning the list."""
        seen_groups: list[str | None] = []
        for n in self.nodes:
            if n.group not in seen_groups:
                seen_groups.append(n.group)

        issues: list[ValidationIssue] = []
        for group in seen_groups:
            issues.extend(self.validate_group(group))

        if strict and any(i.level == "error" for i in issues):
            raise ValidationError(issues)

        return issues

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
    _listeners: dict[str, list[Callable[..., Any]]] = field(default_factory=dict, repr=False)

    def __repr__(self) -> str:
        return (
            f"Run(id={self.id!r}, status={self.status.value!r}, "
            f"tasks={len(self.tasks)!r}, error={self.error!r})"
        )

    def on(self, event: str, callback: Callable[..., Any]) -> "Run[T]":
        """Register `callback` to be called whenever `event` fires. Returns
        self, so calls can be chained: Run(workflow=w).on(...).on(...).execute().

        Available events:
          run_started(run)
          run_finished(run)
          validation_failed(issues: list[ValidationIssue])
          level_started(level: list[Task])
          level_finished(level: list[Task], task_runs: list[TaskRun])
          task_started(task_run)
          task_succeeded(task_run)
          task_failed(task_run)
          task_retrying(task_run)   - fired after a failed attempt that will
                                       still be retried (attempt < retries+1)
        """
        self._listeners.setdefault(event, []).append(callback)
        return self

    def _emit(self, event: str, *args: Any, **kwargs: Any) -> None:
        """Calls every callback registered for `event`. A listener that
        raises is logged and skipped - a bug in a listener must never take
        down the actual workflow run."""
        for callback in self._listeners.get(event, []):
            try:
                callback(*args, **kwargs)
            except Exception as exc:
                print(f"[Run {self.id}] listener for '{event}' raised: {exc!r}")

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
            self._emit("task_started", task_run)

            try:
                if task.function is not None:
                    task.function()
                task_run.status = RunStatus.SUCCESS
                task.run_status = RunStatus.SUCCESS
                task_run.error = None
                task_run.finished_at = datetime.datetime.now()
                task.end = task_run.finished_at
                self._emit("task_succeeded", task_run)
                break
            except Exception as exc:
                task_run.status = RunStatus.FAILED
                task.run_status = RunStatus.FAILED
                task_run.error = Error(str(exc))
                task_run.finished_at = datetime.datetime.now()
                task.end = task_run.finished_at
                if attempt < attempts_allowed:
                    self._emit("task_retrying", task_run)
                else:
                    self._emit("task_failed", task_run)

        return task_run

    def execute(self, group: str | None = None, validate: bool = True) -> "Run[T]":
        """Runs `self.workflow` (or just `group` within it) level by level,
        in parallel within each level. Stops after a level that contains a
        failed task (so nothing depending on it runs), and marks the whole
        Run as FAILED. Returns self, so you can do `run = Run(workflow=w).execute()`.

        If `validate=True` (default), `workflow.validate_group()` /
        `workflow.validate()` runs FIRST. Any error-level ValidationIssue
        (duplicate id, missing start node, unreachable task, cycle,
        negative timeout, ...) stops the Run before a single task is
        touched - you get `run.status == RunStatus.FAILED` with all the
        issues listed in `run.error`, instead of a confusing mid-run
        failure. Warning-level issues (e.g. a task with no function) do
        not block execution.
        """
        if self.workflow is None:
            raise ValueError("Run.workflow is not set - nothing to execute.")

        self.status = RunStatus.RUNNING
        self.started_at = datetime.datetime.now()
        self.tasks = []
        self.error = None
        self._emit("run_started", self)

        if validate:
            issues = (
                self.workflow.validate_group(group)
                if group is not None
                else self.workflow.validate()
            )
            errors = [i for i in issues if i.level == "error"]
            if errors:
                self.status = RunStatus.FAILED
                self.error = Error("; ".join(str(i) for i in errors))
                self.finished_at = datetime.datetime.now()
                self._emit("validation_failed", issues)
                self._emit("run_finished", self)
                return self

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
            self._emit("run_finished", self)
            return self

        failed = False
        for level in levels:
            self._emit("level_started", level)
            with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                task_runs = list(pool.map(self._execute_one, level))
            self.tasks.extend(task_runs)
            self._emit("level_finished", level, task_runs)

            level_failures = [tr for tr in task_runs if tr.status == RunStatus.FAILED]
            if level_failures:
                failed = True
                self.error = level_failures[0].error
                break  # do not start the next level - its deps failed

        self.status = RunStatus.FAILED if failed else RunStatus.SUCCESS
        self.finished_at = datetime.datetime.now()
        self._emit("run_finished", self)
        return self