import { useState } from "react";
import { API_URL, Source } from "@/lib/api";

const TYPE_STYLES: Record<Source["content_type"], { label: string; accent: string; badge: string }> = {
  text: { label: "Text", accent: "border-l-[#8ba99f]", badge: "bg-[#e3ece8] text-[#395c56]" },
  table: { label: "Table", accent: "border-l-[#c99a6c]", badge: "bg-[#f3e4d3] text-[#935d43]" },
  figure: { label: "Figure", accent: "border-l-[#b96f53]", badge: "bg-[#f6e2d8] text-[#a15239]" },
};

export default function SourceCard({ source }: { source: Source }) {
  const [expanded, setExpanded] = useState(false);
  const style = TYPE_STYLES[source.content_type];
  const number = source.content_type === "table" ? source.table_number : source.content_type === "figure" ? source.figure_number : undefined;

  return (
    <div className={`overflow-hidden rounded-xl border border-[var(--border-strong)] border-l-4 ${style.accent} bg-[var(--bg-surface)] transition hover:border-[#b7c9c2]`}>
      <button type="button" onClick={() => setExpanded((value) => !value)} className="flex w-full items-center gap-3 px-4 py-2.5 text-left">
        <span className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${style.badge}`}>{style.label}</span>
        <div className="flex min-w-0 flex-1 items-center gap-1.5 text-xs text-[var(--text-muted)]">
          <span className="whitespace-nowrap">Page {source.page_number}</span>
          {number != null && <span className="whitespace-nowrap">#{number}</span>}
          <span className="truncate">{source.filename}</span>
        </div>
        <span className="shrink-0 text-[var(--text-muted)]">{expanded ? "▲" : "▼"}</span>
      </button>

      {expanded && (
        <div className="border-t border-[var(--border-soft)] bg-[var(--bg-surface-2)] px-4 py-3">
          {source.caption && <p className="mb-2 text-sm font-medium text-[var(--text-heading)]">{source.caption}</p>}
          {source.image_url && (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={`${API_URL}${source.image_url}`}
              alt={source.caption || `Figure on page ${source.page_number}`}
              className="mb-3 max-h-64 w-auto rounded-lg border border-[var(--border-strong)] object-contain"
            />
          )}
          {source.content_type === "table" && source.rows && source.rows.length > 0 ? (
            <div className="overflow-x-auto rounded-lg border border-[var(--border-strong)]">
              <table className="w-full border-collapse text-xs">
                <thead className="border-b border-[var(--border-strong)] bg-[var(--bg-surface-2)] text-left font-semibold text-[var(--text-heading)]">
                  <tr>
                    {source.rows[0].map((cell, i) => (
                      <th key={i} className="px-2.5 py-1.5">{cell}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {source.rows.slice(1).map((row, rowIndex) => (
                    <tr key={rowIndex} className="odd:bg-[var(--bg-surface)] even:bg-[var(--bg-surface-2)]">
                      {row.map((cell, cellIndex) => (
                        <td key={cellIndex} className="px-2.5 py-1.5 text-[var(--text-body)]">{cell}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded-lg bg-[var(--bg-surface)] p-3 font-sans text-sm leading-6 text-[var(--text-body)]">
              {source.content}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
