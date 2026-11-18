import { DragEvent, useRef, useState } from "react";
import { api, Doc, Me, UploadResult } from "./api";
import { UploadIcon } from "./icons";

const STAGES = ["queued", "extracting", "chunking", "indexing", "ready"];

const when = (iso: string) =>
  new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

// A small tile showing the file type, taken from the file name's extension.
function TypeTile({ filename }: { filename: string }) {
  const pdf = filename.toLowerCase().endsWith(".pdf");
  return <span className={pdf ? "type pdf" : "type md"}>{pdf ? "PDF" : "MD"}</span>;
}

function Status({ doc }: { doc: Doc }) {
  if (doc.status === "failed") {
    return (
      <div>
        <span className="badge failed">Failed</span>
        <div className="small error-text">{doc.error}</div>
        {doc.previous_version_live && <div className="small muted">The previous version is still searchable.</div>}
      </div>
    );
  }
  if (doc.status === "ready") return <span className="badge ready">Ready</span>;
  const step = STAGES.indexOf(doc.status);
  return (
    <div>
      <span className="badge working">{doc.status}</span>
      <div className="progress"><div style={{ width: `${((step + 1) / STAGES.length) * 100}%` }} /></div>
      {doc.previous_version_live && <div className="small muted">Previous version stays searchable until this is ready.</div>}
      {(doc.attempts ?? 0) > 1 && <div className="small muted">Attempt {doc.attempts} (recovered after an interruption).</div>}
    </div>
  );
}

export function DocumentsPage({ docs, live, me }: { docs: Doc[]; live: boolean; me: Me }) {
  const [results, setResults] = useState<UploadResult[]>([]);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const upload = async (files: File[]) => {
    if (!files.length) return;
    setUploading(true);
    try {
      setResults((await api.upload(files)).results);
    } catch (e) {
      setResults([{ outcome: "rejected", filename: "", message: e instanceof Error ? e.message : "Upload failed." }]);
    } finally {
      setUploading(false);
      if (input.current) input.current.value = "";
    }
  };

  const drop = (event: DragEvent) => {
    event.preventDefault();
    setDragging(false);
    upload(Array.from(event.dataTransfer.files));
  };

  return (
    <div className="page">
      <div className="page-head">
        <h1>Documents</h1>
        <p className="muted">Upload PDF or Markdown files. Status updates live.</p>
      </div>

      <div
        className={dragging ? "dropzone dragging" : "dropzone"}
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={drop}
        onClick={() => input.current?.click()}
      >
        <input ref={input} type="file" multiple hidden accept={me.accepted_types.join(",")}
               onChange={(e) => upload(Array.from(e.target.files ?? []))} />
        <div className="dropzone-icon"><UploadIcon size={22} /></div>
        <strong>{uploading ? "Uploading…" : "Drop files here or click to choose"}</strong>
        <div className="muted small">
          {me.accepted_types.join(", ")} · up to {me.max_upload_mb} MB each · uploading a changed file replaces the old version
        </div>
      </div>

      {results.length > 0 && (
        <ul className="results">
          {results.map((r, i) => (
            <li key={i} className={r.outcome}>
              <strong>{r.filename}</strong> {r.message}
            </li>
          ))}
        </ul>
      )}

      <div className="list-head">
        <h2>Documents ({docs.length})</h2>
        <span className={live ? "live on" : "live"}>{live ? "Live" : "Reconnecting…"}</span>
      </div>

      {docs.length === 0 ? (
        <div className="notice">Nothing uploaded yet.</div>
      ) : (
        <div className="table-wrap"><table>
          <thead>
            <tr><th>Document</th><th>Version</th><th>Status</th><th className="num">Passages</th><th>Uploaded</th><th /></tr>
          </thead>
          <tbody>
            {docs.map((d) => (
              <tr key={d.id}>
                <td>
                  <div className="doc-name">
                    <TypeTile filename={d.filename} />
                    <div>
                      <strong>{d.title ?? d.filename}</strong>
                      <div className="muted small">
                        {d.doc_owner && <>{d.doc_owner} · </>}<span className="mono">{d.filename}</span>
                      </div>
                    </div>
                  </div>
                </td>
                <td>{d.doc_version ? <span className="ver-tag">v{d.doc_version}</span> : <span className="muted">–</span>}</td>
                <td><Status doc={d} /></td>
                <td className="num">{d.status === "ready" ? d.chunk_count : "–"}</td>
                <td className="small when">{when(d.uploaded_at)}<div className="muted">upload {d.version_no}</div></td>
                <td>
                  <button className="link danger" onClick={() => {
                    if (window.confirm(`Remove ${d.filename}? It will no longer be searchable.`)) api.remove(d.id);
                  }}>Remove</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
    </div>
  );
}
