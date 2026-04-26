import type { ArticleIdea } from "../types/api";

export type PaaBranch = {
  id: string;
  question: string;
  children: { id: string; question: string }[];
};

/** Match SerpAPI top-level PAA to expanded `paa_expansion` rows (by question text, then order). */
export function buildPaaMindMapBranches(idea: ArticleIdea): PaaBranch[] {
  const aq = idea.audience_questions || [];
  const ex = idea.paa_expansion || [];
  if (aq.length > 0) {
    const byQ = new Map(
      ex.map((e) => [e.parent_question.trim().toLowerCase(), e.children || []])
    );
    return aq.map((row, i) => {
      const k = row.question.trim().toLowerCase();
      const ch =
        byQ.get(k) ||
        (ex[i]?.parent_question?.trim().toLowerCase() === k ? ex[i].children : undefined) ||
        ex[i]?.children ||
        [];
      return {
        id: `b-${i}`,
        question: row.question,
        children: (ch || []).map((c, j) => ({
          id: `b-${i}-c-${j}`,
          question: c.question
        }))
      };
    });
  }
  if (ex.length > 0) {
    return ex.map((e, i) => ({
      id: `b-${i}`,
      question: e.parent_question,
      children: (e.children || []).map((c, j) => ({ id: `b-${i}-c-${j}`, question: c.question }))
    }));
  }
  return [];
}

const X0 = 8;
const X1 = 200;
const X2 = 430;
const NODE_W0 = 120;
const NODE_W1 = 200;
const NODE_W2 = 200;
const LINE = "#c7d2e0";
const DOT = "#3b82f6";
const H_PAD = 24;

type Box = { x: number; y: number; w: number; h: number; cy: number; key: string };

function NodeBox({
  x,
  y,
  w,
  h,
  label,
  title
}: {
  x: number;
  y: number;
  w: number;
  h: number;
  label: string;
  title: string;
}) {
  return (
    <foreignObject x={x} y={y} width={w} height={h} className="overflow-visible">
      <div
        title={title}
        className="flex h-full min-h-[2.5rem] items-center rounded-lg border border-slate-200/90 bg-white px-2.5 py-1.5 text-left shadow-sm"
        style={{ width: w, minHeight: h, boxSizing: "border-box" }}
      >
        <span className="text-[12px] font-medium leading-snug text-ink [overflow-wrap:anywhere]">{label}</span>
      </div>
    </foreignObject>
  );
}

function bezier(
  x0: number,
  y0: number,
  x1: number,
  y1: number
) {
  const mx = (x0 + x1) / 2;
  return `M ${x0} ${y0} C ${mx} ${y0}, ${mx} ${y1}, ${x1} ${y1}`;
}

export function PaaMindMap({ rootLabel, branches }: { rootLabel: string; branches: PaaBranch[] }) {
  if (!branches.length) {
    return (
      <p className="text-sm text-slate-400">
        No People also ask tree yet. Refresh SERP data after saving a SerpAPI key, or the SERP may not have returned
        questions for this keyword.
      </p>
    );
  }

  const GAP = 10;
  // Vertical pitch for L2 must fit wrapped 12px text in a ~200px-wide box (often 2+ lines).
  const lineH2 = 52;
  const minBlock = 40;

  const l1Meta: { box: Box; l2: Box[] }[] = [];
  let cursorY = H_PAD;

  for (const br of branches) {
    const n2 = br.children.length;
    const stackH = n2 > 0 ? n2 * lineH2 : minBlock;
    const l1H = Math.max(minBlock, stackH);
    const l1Y = cursorY;
    const l1Cx = l1Y + l1H / 2;

    const l2: Box[] = [];
    if (n2 > 0) {
      const firstY = l1Y + (l1H - n2 * lineH2) / 2;
      for (let j = 0; j < n2; j++) {
        const y2 = firstY + j * lineH2;
        const bh = lineH2 - 6;
        l2.push({
          x: X2,
          y: y2,
          w: NODE_W2,
          h: bh,
          cy: y2 + bh / 2,
          key: br.children[j].id
        });
      }
    }

    l1Meta.push({
      box: { x: X1, y: l1Y, w: NODE_W1, h: l1H, cy: l1Cx, key: br.id },
      l2
    });
    cursorY += l1H + GAP;
  }

  const totalH = Math.max(cursorY + H_PAD, 200);
  const rootCy = totalH / 2;
  const rootBox: Box = {
    x: X0,
    y: rootCy - 20,
    w: NODE_W0,
    h: 40,
    cy: rootCy,
    key: "root"
  };

  const svgW = X2 + NODE_W2 + 32;

  return (
    <div className="w-full overflow-x-auto rounded-xl border border-slate-200/80 bg-slate-50/50">
      <svg
        width={svgW}
        height={totalH}
        className="block"
        viewBox={`0 0 ${svgW} ${totalH}`}
        role="img"
        aria-label="People also ask tree for the primary keyword"
      >
        {l1Meta.map((row, i) => {
          const b1 = row.box;
          const p0 = { x: rootBox.x + rootBox.w, y: rootBox.cy };
          const p1 = { x: b1.x, y: b1.cy };
          return (
            <g key={b1.key}>
              <path d={bezier(p0.x, p0.y, p1.x, p1.y)} fill="none" stroke={LINE} strokeWidth={1.2} />
            </g>
          );
        })}

        {l1Meta.map((row) =>
          row.l2.map((b2) => {
            const p0 = { x: row.box.x + row.box.w, y: row.box.cy };
            const p1 = { x: b2.x, y: b2.cy };
            return (
              <path
                key={b2.key}
                d={bezier(p0.x, p0.y, p1.x, p1.y)}
                fill="none"
                stroke={LINE}
                strokeWidth={1.2}
              />
            );
          })
        )}

        <circle cx={rootBox.x + rootBox.w} cy={rootBox.cy} r={3.5} fill={DOT} />
        {l1Meta.map((row) => (
          <g key={row.box.key + "-dots"}>
            <circle cx={row.box.x} cy={row.box.cy} r={3.5} fill={DOT} />
            {row.l2.map((b2) => (
              <circle key={b2.key + "d"} cx={b2.x} cy={b2.cy} r={3.5} fill={DOT} />
            ))}
          </g>
        ))}

        <NodeBox
          x={rootBox.x}
          y={rootBox.y}
          w={rootBox.w}
          h={rootBox.h}
          label={rootLabel}
          title={rootLabel}
        />
        {l1Meta.map((row, i) => {
          const b = row.box;
          return (
            <NodeBox
              key={b.key}
              x={b.x}
              y={b.y + (b.h - 40) / 2}
              w={b.w}
              h={40}
              label={branches[i].question}
              title={branches[i].question}
            />
          );
        })}
        {l1Meta.map((row, i) =>
          row.l2.map((b2, j) => {
            const ch = branches[i].children[j];
            if (!ch) return null;
            return (
              <NodeBox
                key={b2.key}
                x={b2.x}
                y={b2.y}
                w={b2.w}
                h={b2.h}
                label={ch.question}
                title={ch.question}
              />
            );
          })
        )}
      </svg>
    </div>
  );
}
