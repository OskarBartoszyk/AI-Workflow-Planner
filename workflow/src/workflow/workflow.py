from dataclasses import dataclass, field
from typing import Generic, TypeVar, Literal, Any, Callable, ClassVar
import time
import datetime
import uuid
import operator
import json
from enum import Enum
from concurrent.futures import ThreadPoolExecutor


T = TypeVar("T")

StatusLiteral = Literal["Done", "Inprogress", "completed"]


@dataclass(slots=True, repr=False)
class Error:
    """Own Error message"""
    description: str = ""

    def __repr__(self) -> str:
        if self.description:
            return f"Error({self.description!r})"
        return "Error"


@dataclass(slots=True, repr=False)
class Node(Generic[T]):
    value: T
    description: str | None = None
    id: int | None = None
    group: str | None = None
    auto_id: bool = False  # If set to True, id is incremented automatically,
    # also increments separately per group of nodes. id assigned in creation queue order
    status: str | StatusLiteral | Error | None = None
    completed: bool | None = None
    short: bool = False  # if True show only value, if False show full description

    def __repr__(self) -> str:
        if self.short:
            return f"Node({self.value!r})"
        return (
            f"Node(value={self.value!r}, description={self.description!r}, "
            f"id={self.id!r}, group={self.group!r}, auto_id={self.auto_id!r}, "
            f"status={self.status!r}, completed={self.completed!r}, short={self.short!r})"
        )


@dataclass(slots=True, repr=False)
class Edge(Generic[T]):
    source: Node[T]
    target: Node[T]
    description: str | None = None
    source_desc: str | None = None
    target_desc: str | None = None
    weight: int | float | None = 0
    directed: bool = False
    short: bool = False
    short_all: bool = False

    @staticmethod
    def _force_short_node(node: "Node[T]") -> str:
        return f"Node({node.value!r})"

    def __repr__(self) -> str:
        if self.short_all:
            src = self._force_short_node(self.source)
            tgt = self._force_short_node(self.target)
            return (
                f"Source:{src} - Target:{tgt}, "
                f"Weight:{self.weight!r}, Directed:{self.directed!r}"
            )
        if self.short:
            return (
                f"Source:{self.source!r} - Target:{self.target!r}, "
                f"Weight:{self.weight!r}, Directed:{self.directed!r}"
            )
        return (
            f"Edge(source={self.source!r}, target={self.target!r}, "
            f"description={self.description!r}, source_desc={self.source_desc!r}, "
            f"target_desc={self.target_desc!r}, weight={self.weight!r}, "
            f"directed={self.directed!r}, short={self.short!r})"
        )


@dataclass(slots=True, repr=False)
class Graph(Generic[T]):
    name: str | None = None
    nodes: list[Node[T]] = field(default_factory=list)
    edges: list[Edge[T]] = field(default_factory=list)
    short: bool = False
    _id_counters: dict[str | None, int] = field(default_factory=dict, repr=False)

    def add_node(self, node: Node[T]) -> Node[T]:
        if node in self.nodes:
            return node
        if node.auto_id and node.id is None:
            key = node.group
            next_id = self._id_counters.get(key, 0) + 1
            self._id_counters[key] = next_id
            node.id = next_id
        self.nodes.append(node)
        return node

    def add_edge(self, source: Node[T], target: Node[T], **kwargs) -> Edge[T]:
        if source not in self.nodes:
            self.add_node(source)
        if target not in self.nodes:
            self.add_node(target)
        edge = Edge(source=source, target=target, **kwargs)
        self.edges.append(edge)
        return edge

    def neighbors(self, node: Node[T]) -> list[Node[T]]:
        result: list[Node[T]] = []
        for e in self.edges:
            if e.source is node:
                result.append(e.target)
            elif not e.directed and e.target is node:
                result.append(e.source)
        return result

    def find_by_id(self, node_id: int, group: str | None = None) -> Node[T] | None:
        for n in self.nodes:
            if n.id == node_id and n.group == group:
                return n
        return None

    def remove_edge(self, edge: Edge[T]) -> bool:
        try:
            self.edges.remove(edge)
            return True
        except ValueError:
            return False

    def remove_edges_between(self, source, target, directed_only=False) -> int:
        before = len(self.edges)
        def matches(e):
            if e.source is source and e.target is target:
                return True
            if not directed_only and not e.directed:
                return e.source is target and e.target is source
            return False
        self.edges = [e for e in self.edges if not matches(e)]
        return before - len(self.edges)

    def remove_node(self, node: Node[T], cascade: bool = True) -> bool:
        if node not in self.nodes:
            return False
        connected = [e for e in self.edges if e.source is node or e.target is node]
        if connected and not cascade:
            raise ValueError(
                f"Cannot remove node {node!r} - it has {len(connected)} "
                f"connected edges. Use cascade=True."
            )
        for e in connected:
            self.edges.remove(e)
        self.nodes.remove(node)
        return True

    def __repr__(self) -> str:
        if self.short:
            return f"Graph({self.name!r}, nodes={len(self.nodes)}, edges={len(self.edges)})"
        nodes_repr = ",\n    ".join(repr(n) for n in self.nodes)
        edges_repr = ",\n    ".join(repr(e) for e in self.edges)
        return (
            f"Graph(name={self.name!r},\n"
            f"  nodes=[\n    {nodes_repr}\n  ],\n"
            f"  edges=[\n    {edges_repr}\n  ]\n"
            f")"
        )
    

class RunStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


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

    history: list["Run[T]"] = field(default_factory=list, repr=False)
    history_path: str | None = None

    def add_task(self, task: Task[T]) -> Task[T]:
        return self.add_node(task)

    def tasks_by_group(self, group: str | None) -> list[Task[T]]:
        """All Task nodes belonging to `group`, in the order they were added."""
        return [n for n in self.nodes if isinstance(n, Task) and n.group == group]

    # ------------------------------------------------------------------
    # Run history: every `Run` executed against this workflow records
    # itself in `self.history` automatically (see Run.execute()) - but
    # that's in-memory only, gone the moment the process ends.
    #
    # Set `workflow.history_path = "runs.jsonl"` for REAL persistence:
    # every finished Run then also appends itself as one JSON line to
    # that file, automatically, with no extra call needed - so history
    # survives process restarts. `save_history()`/`load_history()` below
    # are for a manual one-shot export/import of `self.history` instead.
    # ------------------------------------------------------------------

    def save_history(self, path: str) -> None:
        """Serializes every recorded Run (see `self.history`) to JSON and
        writes it to `path`, as one JSON array (overwrites `path`). This
        is a read-only audit trail: Task references and live functions
        aren't serializable, so this can't be used to reconstruct/replay
        a Run - just to inspect what happened, later, from outside the
        process. For automatic, ongoing persistence across restarts, set
        `workflow.history_path` instead."""
        with open(path, "w", encoding="utf-8") as f:
            json.dump([run.to_dict() for run in self.history], f, ensure_ascii=False, indent=2)

    @staticmethod
    def load_history(path: str) -> list[dict[str, Any]]:
        """Reads back a JSON array file written by `save_history`, as
        plain dicts (not live Run/TaskRun objects)."""
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def load_history_file(path: str) -> list[dict[str, Any]]:
        """Reads a JSONL file written via `workflow.history_path`
        auto-persist (one JSON object per line, one per finished Run) into
        a list of plain dicts. Safe to call even while another process is
        still appending to the same file - only fully-written lines are
        parsed, and a trailing partial line is silently ignored."""
        records: list[dict[str, Any]] = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    break  # trailing/partial line being written concurrently - stop here
        return records

    def clear_history(self) -> int:
        """Drops all recorded Runs from `self.history`. Returns how many
        were removed. Does not touch anything already saved to disk (via
        `save_history` or `history_path` auto-persist)."""
        count = len(self.history)
        self.history = []
        return count

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

    def to_dict(self) -> dict[str, Any]:
        """Plain-dict (JSON-serializable) view of this TaskRun, for
        Workflow.save_history() / any external logging/dashboard."""
        return {
            "task": self.task.value,
            "task_id": self.task.id,
            "group": self.task.group,
            "status": self.status.value,
            "attempt": self.attempt,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration": self.duration,
            "error": self.error.description if self.error else None,
        }


@dataclass(slots=True, repr=False)
class RunPreview(Generic[T]):
    """The result of `Run.dry_run()`: what WOULD happen if you called
    `execute()` now, computed without touching a single task. Print it
    (or show it in a UI) to get human sign-off before actually running."""

    workflow_name: str | None
    group: str | None
    levels: list[list[Task[T]]] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def is_safe_to_run(self) -> bool:
        return not any(i.level == "error" for i in self.issues)

    def __repr__(self) -> str:
        lines = [f"Podglad Run-a: workflow={self.workflow_name!r} group={self.group!r}"]
        if self.issues:
            lines.append("  Problemy walidacji:")
            for issue in self.issues:
                lines.append(f"    {issue}")
        else:
            lines.append("  Brak problemow walidacji.")
        lines.append("  Kolejnosc wykonania (poziomy rownoleglosci):")
        for idx, level in enumerate(self.levels, start=1):
            lines.append(f"    Poziom {idx}: {[t.value for t in level]}")
        lines.append(f"  Bezpieczne do uruchomienia: {self.is_safe_to_run}")
        return "\n".join(lines)


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

    def to_dict(self) -> dict[str, Any]:
        """Plain-dict (JSON-serializable) view of this Run, for
        Workflow.save_history() / any external logging/dashboard. Cannot
        be turned back into a live Run (Task references and functions
        aren't serializable) - it's a read-only record."""
        return {
            "id": self.id,
            "workflow": self.workflow.name if self.workflow else None,
            "status": self.status.value,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration": (
                (self.finished_at - self.started_at).total_seconds()
                if self.started_at and self.finished_at
                else None
            ),
            "error": self.error.description if self.error else None,
            "tasks": [tr.to_dict() for tr in self.tasks],
        }

    def dry_run(self, group: str | None = None) -> "RunPreview[T]":
        """Builds the plan WITHOUT executing anything: runs
        `workflow.validate()`/`validate_group()` and computes the
        level-by-level execution order, returning a `RunPreview` a human
        can inspect (print it, or check `.is_safe_to_run`) before deciding
        whether to actually call `execute()`."""
        if self.workflow is None:
            raise ValueError("Run.workflow is not set - nothing to preview.")

        issues = (
            self.workflow.validate_group(group) if group is not None else self.workflow.validate()
        )
        try:
            levels = (
                self.workflow.plan_levels_group(group)
                if group is not None
                else self.workflow.plan_levels()
            )
        except CycleError:
            levels = []

        return RunPreview(
            workflow_name=self.workflow.name, group=group, levels=levels, issues=issues
        )

    def on(self, event: str, callback: Callable[..., Any]) -> "Run[T]":
        """Register `callback` to be called whenever `event` fires. Returns
        self, so calls can be chained: Run(workflow=w).on(...).on(...).execute().

        Available events:
          run_started(run)
          run_finished(run)
          run_cancelled(run)        - fired instead of run_finished when a
                                       human declines a confirm=True dry-run
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

    def _persist_to_history_file(self) -> None:
        """If `self.workflow.history_path` is set, appends this Run as one
        JSON line to that file. Called automatically at every exit point
        of `execute()` - cancelled, validation failed, cycle detected, or
        finished normally - so history survives even if the process ends
        right after. A failure here is logged, not raised: a broken disk/
        permission problem must never take down the actual workflow run."""
        path = self.workflow.history_path if self.workflow is not None else None
        if not path:
            return
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(self.to_dict(), ensure_ascii=False) + "\n")
        except Exception as exc:
            print(f"[Run {self.id}] failed to persist history to {path!r}: {exc!r}")

    def execute(
        self, group: str | None = None, validate: bool = True, confirm: bool = False
    ) -> "Run[T]":
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

        If `confirm=True`, `dry_run()` runs first, gets printed, and the
        terminal asks the human to type 'y' before anything executes. If
        they decline, the Run ends as `RunStatus.CANCELLED` (fires
        `run_cancelled` instead of `run_finished`) without touching a
        single task. Meant for CLI/notebook use - for a real frontend,
        call `dry_run()` yourself and wire the confirmation to a UI button
        instead.

        Every Run is appended to `self.workflow.history` the moment it
        starts (see `Workflow.history`) - and since it's the same object,
        its recorded status keeps updating in place as the Run progresses.
        If `self.workflow.history_path` is set, the Run is ALSO appended
        to that file on disk (one JSON line per Run) the moment it ends -
        automatically, no extra call needed - so history survives even if
        the process exits right after.
        """
        if self.workflow is None:
            raise ValueError("Run.workflow is not set - nothing to execute.")

        if confirm:
            preview = self.dry_run(group=group)
            print(preview)
            answer = input("Uruchomic ten workflow? [y/N]: ").strip().lower()
            if answer != "y":
                self.status = RunStatus.CANCELLED
                self.started_at = datetime.datetime.now()
                self.finished_at = self.started_at
                self.workflow.history.append(self)
                self._persist_to_history_file()
                self._emit("run_cancelled", self)
                return self

        self.status = RunStatus.RUNNING
        self.started_at = datetime.datetime.now()
        self.tasks = []
        self.error = None
        self.workflow.history.append(self)
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
                self._persist_to_history_file()
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
            self._persist_to_history_file()
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
        self._persist_to_history_file()
        self._emit("run_finished", self)
        return self