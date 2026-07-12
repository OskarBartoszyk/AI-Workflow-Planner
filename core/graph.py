from dataclasses import dataclass, field
from typing import Generic, TypeVar, Literal

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
    
