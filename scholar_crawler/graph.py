"""The citation graph hiding in collected records, made explicit and exportable.

A ``--cites X`` listing means every record on it cites the work whose citing-works id is
``X``, and each record's own ``cited_by_url`` carries that id for itself. So the edges of a
citation graph are already in the JSONL a crawl wrote: no extra request, and collections made
before this module existed still yield their graph.

Nothing here contacts Scholar. It reads records and reports what they already say about
who cites whom.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from .models import record_key
from .urls import parse_cluster_id

Record = dict[str, Any]

def citing_works_id(record: Record) -> str | None:
    """Read the id that identifies this work's citing-works listing.

    :param record: a stored record.
    :returns: the numeric cites id, or None when the record carries no citing-works link.
    """
    url = record.get("cited_by_url")
    if not isinstance(url, str) or not url:
        return None
    try:
        return parse_cluster_id(url)
    except ValueError:  # a citing-works link rendered without an id
        return None


def cited_target(record: Record) -> str | None:
    """Read which work this record was collected as a citation of.

    :param record: a stored record.
    :returns: the numeric cites id from a ``cites:`` listing, or None when the record came
        from a keyword search, a versions listing or a profile.
    """
    query = record.get("query")
    if not isinstance(query, str) or not query.startswith("cites:"):
        return None
    try:
        return parse_cluster_id(query.removeprefix("cites:"))
    except ValueError:  # a label written without an id
        return None


@dataclass(slots=True)
class Node:
    """One work in the graph.

    :param key: the record key this node stands for.
    :param label: the work's title, or its id when the work was never collected.
    :param year: publication year, when known.
    :param citations: Scholar's global citation count, when known.
    :param depth: expansion level the record was collected at, when recorded.
    :param stub: True when only the id is known because the work itself was not collected.
    """

    key: str
    label: str
    year: int | None = None
    citations: int | None = None
    depth: int | None = None
    stub: bool = False


@dataclass(slots=True)
class Graph:
    """A citation graph over collected records.

    :param nodes: works, keyed by record key.
    :param edges: ``(citing, cited)`` record keys, deduplicated.
    :param stubs: keys of works known only as the target of an edge.
    """

    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[tuple[str, str]] = field(default_factory=list)
    stubs: set[str] = field(default_factory=set)

    def in_degree(self) -> dict[str, int]:
        """Count how often each work is cited from inside this collection.

        :returns: citation counts within the graph, keyed by record key.
        """
        counted: dict[str, int] = defaultdict(int)
        for _citing, cited in self.edges:
            counted[cited] += 1
        return dict(counted)

    def out_degree(self) -> dict[str, int]:
        """Count how many works inside this collection each record cites.

        :returns: outgoing counts, keyed by record key.
        """
        counted: dict[str, int] = defaultdict(int)
        for citing, _cited in self.edges:
            counted[citing] += 1
        return dict(counted)

    def components(self) -> list[list[str]]:
        """Group the graph into connected components, ignoring edge direction.

        :returns: components, largest first, each a list of record keys.
        """
        neighbours: dict[str, set[str]] = {key: set() for key in self.nodes}
        for citing, cited in self.edges:
            neighbours[citing].add(cited)
            neighbours[cited].add(citing)
        seen: set[str] = set()
        found = []
        for key in self.nodes:
            if key in seen:
                continue
            stack = [key]
            group = []
            seen.add(key)
            while stack:
                current = stack.pop()
                group.append(current)
                for neighbour in neighbours[current]:
                    if neighbour not in seen:
                        seen.add(neighbour)
                        stack.append(neighbour)
            found.append(group)
        return sorted(found, key=len, reverse=True)


def _node_of(record: Record) -> Node:
    """Build a graph node from a stored record.

    :param record: a stored record.
    :returns: the node, titled by the record and carrying its year and citation count.
    """
    citations = record.get("cited_by_count")
    year = record.get("year")
    depth = (record.get("extra") or {}).get("follow_depth")
    return Node(
        key=record_key(record),
        label=" ".join(str(record.get("title") or "untitled").split()),
        year=year if isinstance(year, int) else None,
        citations=citations if isinstance(citations, int) else None,
        depth=depth if isinstance(depth, int) else None,
    )


def build_graph(observations: list[Record], nodes: list[Record] | None = None) -> Graph:
    """Recover the citation graph from collected records.

    Edges come from every observation, including duplicates of one work: a record collected
    under three different ``--cites`` listings carries three edges, and merging keeps only one
    of its ``query`` values. Nodes come from ``nodes`` when given, so a filtered set can be
    drawn with the edges of the whole collection.

    :param observations: every record read from disk, duplicates included.
    :param nodes: the records to draw as nodes; defaults to ``observations``.
    :returns: the graph, with a stub node for every cited work that was not collected.
    """
    drawn = observations if nodes is None else nodes
    graph = Graph()
    for record in drawn:
        node = _node_of(record)
        graph.nodes.setdefault(node.key, node)
    by_cites_id = {
        cites_id: record_key(record)
        for record in drawn
        if (cites_id := citing_works_id(record)) is not None
    }
    seen: set[tuple[str, str]] = set()
    for record in observations:
        target = cited_target(record)
        if target is None:
            continue
        citing = record_key(record)
        if citing not in graph.nodes:  # a record filtered out of the drawn set
            continue
        cited = by_cites_id.get(target)
        if cited is None:
            cited = f"cites:{target}"
            if cited not in graph.nodes:
                graph.nodes[cited] = Node(key=cited, label=f"uncollected work {target}", stub=True)
                graph.stubs.add(cited)
        if citing == cited:  # Scholar lists some works among their own citing works
            continue
        edge = (citing, cited)
        if edge not in seen:
            seen.add(edge)
            graph.edges.append(edge)
    return graph


def render_network(graph: Graph, *, top: int = 10) -> list[str]:
    """Report what the graph says about the collection.

    :param graph: the citation graph.
    :param top: how many of the most-cited works to list.
    :returns: printable lines.
    """
    collected = [key for key in graph.nodes if key not in graph.stubs]
    if not graph.edges:
        return [
            f"no citation edges in {len(collected)} records: "
            "edges come from --cites listings, which this collection has none of"
        ]
    inside = graph.in_degree()
    outside = graph.out_degree()
    components = graph.components()
    isolated = [key for key in collected if not inside.get(key) and not outside.get(key)]
    lines = [
        f"{len(collected)} records and {len(graph.stubs)} uncollected works, {len(graph.edges)} edges",
        f"{len(components)} component(s), largest {len(components[0])} works; "
        f"{len(isolated)} record(s) neither cite nor are cited here",
    ]
    ranked = sorted(inside.items(), key=lambda item: item[1], reverse=True)[:top]
    if not ranked:
        return lines
    lines.append("most cited from inside this collection:")
    for key, count in ranked:
        node = graph.nodes[key]
        total = f"{node.citations:,}" if node.citations is not None else "?"
        lines.append(f"  {count:>4} here  {total:>9} on Scholar  {node.label[:64]}")
    return lines
