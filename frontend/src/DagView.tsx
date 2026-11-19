import { useId } from "react";
import type { Dag } from "./types";

const width = 170, height = 68, horizontal = 224, vertical = 94, padding = 18;

function positions(dag: Dag) {
  const nodes = [...dag.nodes].sort((a, b) => a.key.localeCompare(b.key));
  const counts = new Map(nodes.map(node => [node.key, 0]));
  const outgoing = new Map(nodes.map(node => [node.key, [] as string[]]));
  const ranks = new Map(nodes.map(node => [node.key, 0]));
  for (const [from, to] of dag.edges) {
    outgoing.get(from)?.push(to);
    counts.set(to, (counts.get(to) ?? 0) + 1);
  }
  const queue = nodes.filter(node => counts.get(node.key) === 0).map(node => node.key);
  for (let index = 0; index < queue.length; index++) {
    const from = queue[index];
    for (const to of outgoing.get(from) ?? []) {
      ranks.set(to, Math.max(ranks.get(to) ?? 0, (ranks.get(from) ?? 0) + 1));
      const count = (counts.get(to) ?? 1) - 1;
      counts.set(to, count);
      if (count === 0) queue.push(to);
    }
  }
  const rows = new Map<number, number>();
  return new Map(nodes.map(node => {
    const rank = ranks.get(node.key) ?? 0;
    const row = rows.get(rank) ?? 0;
    rows.set(rank, row + 1);
    return [node.key, { x: padding + rank * horizontal, y: padding + row * vertical }];
  }));
}

export function DagView({ dag, states }: { dag: Dag; states?: Map<string, string> }) {
  const marker = useId();
  const points = positions(dag);
  const svgWidth = Math.max(250, ...Array.from(points.values(), point => point.x + width + padding));
  const svgHeight = Math.max(112, ...Array.from(points.values(), point => point.y + height + padding));
  return <div className="dag-scroll" tabIndex={0} aria-label="Workflow graph, scroll to see all nodes">
    <svg width={svgWidth} height={svgHeight} role="img" aria-label="Workflow dependency graph">
      <defs><marker id={marker} markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto">
        <path d="M0,0 L9,5 L0,10 z" className="arrow" />
      </marker></defs>
      {dag.edges.map(([from, to]) => {
        const a = points.get(from), b = points.get(to);
        if (!a || !b) return null;
        const x1 = a.x + width, y1 = a.y + height / 2, x2 = b.x - 4, y2 = b.y + height / 2;
        return <path key={from + ":" + to} className="edge" markerEnd={"url(#" + marker + ")"}
          d={"M" + x1 + "," + y1 + " C" + (x1 + 20) + "," + y1 + " " +
            (x2 - 20) + "," + y2 + " " + x2 + "," + y2} />;
      })}
      {dag.nodes.map(node => {
        const point = points.get(node.key);
        if (!point) return null;
        const status = states?.get(node.key);
        const caption = status ? status.replaceAll("_", " ") : node.handler;
        return <g key={node.key} className={"dag-node " + (status?.toLowerCase() ?? "")}
          transform={"translate(" + point.x + "," + point.y + ")"}>
          <title>{node.key + " · " + caption}</title>
          <rect width={width} height={height} rx="8" />
          <text x="12" y="27">{node.key.length > 20 ? node.key.slice(0, 19) + "…" : node.key}</text>
          <text x="12" y="48" className="node-caption">{caption}</text>
        </g>;
      })}
    </svg>
  </div>;
}
