import { useEffect, useState } from "react";
import {
  api,
  STATUS_LABEL,
  type Canvas,
  type OpenItem,
  type Overview as OverviewData,
} from "../api";
import { HistoryList, type Names } from "./HistoryList";

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

const REASONS: { reason: OpenItem["reason"]; title: string }[] = [
  { reason: "failed", title: "Failed" },
  { reason: "running_externally", title: "Running externally" },
  { reason: "planned", title: "Planned" },
  { reason: "warning", title: "With warnings" },
  { reason: "direct", title: "Direct connections (no TS)" },
];

/** WF-10, FR-OV-01 (D45, D53): where an investigation stands after a gap. Shown in the side panel
 * whenever nothing is selected, so it is the first thing seen on opening. Every item selects its
 * record on the canvas. */
export function Overview({
  canvas,
  refreshKey,
  labels,
  names,
  onSelectNode,
  onSelectTransition,
  onSelectBranch,
}: {
  canvas: Canvas;
  refreshKey: number;
  labels: Map<string, string>;
  names: Names;
  onSelectNode: (id: string) => void;
  onSelectTransition: (id: string) => void;
  onSelectBranch: (id: string) => void;
}) {
  const [data, setData] = useState<OverviewData | null>(null);
  const [since, setSince] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    api.overview(since || null).then(
      (found) => current && setData(found),
      (err: unknown) => current && setError(errorText(err)),
    );
    return () => {
      current = false;
    };
  }, [since, refreshKey, canvas]);

  const branchName = (id: string) =>
    canvas.branches.find((b) => b.id === id)?.name || "Unnamed branch";
  const open = (item: OpenItem) =>
    item.kind === "transition"
      ? onSelectTransition(item.id)
      : onSelectNode(item.id);

  if (!data)
    return error ? (
      <p role="alert">{error}</p>
    ) : (
      <p className="muted">Loading the overview…</p>
    );
  const empty = canvas.nodes.length === 0 && canvas.groups.length === 0;

  return (
    <div className="inspector overview" aria-label="Overview">
      <div className="inspector-head">
        <h2>Overview</h2>
      </div>
      <p className="muted small">
        Select a node, transition, group or branch to see it here. Shift-drag or
        Ctrl-click on the canvas selects several nodes.
      </p>
      {error && <p role="alert">{error}</p>}
      {empty ? (
        <p className="muted">
          This investigation is empty. Add a node or import a file to start.
        </p>
      ) : (
        <>
          <section aria-label="Branch status">
            <h3>Branches</h3>
            <table className="members overview-branches">
              <thead>
                <tr>
                  <th>Branch</th>
                  <th>Status</th>
                  <th>Nodes by status</th>
                </tr>
              </thead>
              <tbody>
                {data.branches.map((b) => (
                  <tr key={b.id ?? "none"}>
                    <td>
                      {b.colour && (
                        <span
                          className="swatch"
                          style={{ background: b.colour }}
                        />
                      )}
                      {b.id ? (
                        <button
                          className="link"
                          onClick={() => onSelectBranch(b.id!)}
                        >
                          {b.name || "Unnamed branch"}
                        </button>
                      ) : (
                        <span className="muted">{b.name}</span>
                      )}
                    </td>
                    <td>{b.status ? STATUS_LABEL[b.status] : "—"}</td>
                    <td>
                      {b.node_count === 0
                        ? "no nodes"
                        : Object.entries(b.counts)
                            .map(
                              ([status, n]) =>
                                `${n} ${STATUS_LABEL[status as keyof typeof STATUS_LABEL].toLowerCase()}`,
                            )
                            .join(" · ")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {data.group_count > 0 && (
              <p className="muted small">
                {data.group_count} group{data.group_count === 1 ? "" : "s"}{" "}
                (members are counted with their branch; members with no branch are not listed here).
              </p>
            )}
          </section>

          <section aria-label="Open items">
            <h3>Open items</h3>
            {data.open_items.length === 0 && (
              <p className="muted">
                Nothing is planned, running, failed or flagged.
              </p>
            )}
            {REASONS.map(({ reason, title }) => {
              const listed = data.open_items.filter((i) => i.reason === reason);
              if (listed.length === 0) return null;
              return (
                <div key={reason} className="open-items">
                  <h4>
                    {title} ({listed.length})
                  </h4>
                  <ul aria-label={title}>
                    {listed.map((item) => (
                      <li key={`${item.kind}-${item.id}`}>
                        <button className="link" onClick={() => open(item)}>
                          {item.label}
                        </button>
                        {item.kind === "transition" && reason !== "direct" && (
                          <span className="muted"> (transition)</span>
                        )}
                        {item.detail && (
                          <span className="badge warn">{item.detail}</span>
                        )}
                        {item.branch_ids.length > 0 && (
                          <span className="muted small">
                            {" "}
                            · {item.branch_ids.map(branchName).join(", ")}
                          </span>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              );
            })}
          </section>

          <section aria-label="Step notes">
            <h3>Notes per reaction step</h3>
            {data.steps.length === 0 && (
              <p className="muted">No reaction steps yet.</p>
            )}
            <dl className="step-notes">
              {data.steps.map((step, index) => (
                <div key={step.id}>
                  <dt>
                    {index + 1}. {step.name || "Unnamed step"}
                  </dt>
                  <dd className={step.notes ? "notes-text" : "muted"}>
                    {step.notes || "No notes"}
                  </dd>
                </div>
              ))}
            </dl>
          </section>
        </>
      )}

      <section aria-label="Recent changes">
        <div className="section-head">
          <h3>Recent changes</h3>
          <label className="inline">
            <span>Since</span>
            <input
              type="date"
              aria-label="Changes since"
              value={since}
              onChange={(event) => setSince(event.target.value)}
            />
          </label>
        </div>
        <HistoryList
          entries={data.recent}
          labels={labels}
          names={names}
          onSelect={onSelectNode}
        />
      </section>
    </div>
  );
}
