import { FormEvent, useState } from "react";
import { Answer, api, Passage } from "./api";

const date = (iso: string) =>
  new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });

function PassageCard({ p, label }: { p: Passage; label?: string }) {
  const [start, end] = p.highlight ?? [0, 0];
  return (
    <div className="source">
      <div className="source-head">
        <div>
          {label && <span className="source-label">{label}</span>}
          <strong>{p.document}</strong>
          {p.document_version && <span className="pill">version {p.document_version}</span>}
        </div>
        <div className="muted small">{p.location.label}</div>
      </div>
      <blockquote>
        {p.highlight ? (
          <>{p.passage.slice(0, start)}<mark>{p.passage.slice(start, end)}</mark>{p.passage.slice(end)}</>
        ) : p.passage}
      </blockquote>
      <div className="muted small">
        {p.filename} · uploaded {date(p.uploaded_at)} (upload {p.upload_no})
        {p.document_owner && <> · owner: {p.document_owner}</>}
      </div>
    </div>
  );
}

function Why({ a }: { a: Answer }) {
  const best = a.sources[0] ?? a.closest[0];
  return (
    <details className="why">
      <summary>How this was decided</summary>
      <p>
        A passage counts as support only if it covers at least {Math.round(a.threshold * 100)}% of
        what the question asks about, and the quoted text is found word for word in the stored document.
        This check is done in code, not by a model.
      </p>
      {best && (
        <ul>
          <li>Best passage covers {Math.round(best.coverage * 100)}% of the question.</li>
          {best.matched_terms.length > 0 && <li>Found: {best.matched_terms.join(", ")}</li>}
          {best.missing_terms.length > 0 && <li>Not found: {best.missing_terms.join(", ")}</li>}
        </ul>
      )}
      <p className="muted small">
        {a.retriever} retrieval · {a.answerer} answer · {a.documents_indexed} documents · {a.took_ms} ms
      </p>
    </details>
  );
}

export function AskPage({ readyCount, goToDocuments }: { readyCount: number; goToDocuments: () => void }) {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!question.trim() || loading) return;
    setLoading(true);
    setError("");
    try {
      setAnswer(await api.ask(question));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="page">
      <form className="ask" onSubmit={submit}>
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Ask a question about your policies, e.g. How long are backups kept?"
          maxLength={500}
          autoFocus
        />
        <button className="primary" disabled={loading || !question.trim()}>{loading ? "Searching…" : "Ask"}</button>
      </form>

      {readyCount === 0 && (
        <div className="notice">
          No documents are indexed yet. <button className="link" onClick={goToDocuments}>Upload some documents</button> to get started.
        </div>
      )}
      {error && <div className="error">{error}</div>}

      {answer && answer.supported && (
        <section className="card">
          <div className="verdict ok">Supported by your documents</div>
          <p className="answer">{answer.answer}</p>
          <h3>Source</h3>
          {answer.sources.map((s, i) => <PassageCard key={i} p={s} />)}
          {answer.related.length > 0 && (
            <>
              <h3>Related passages</h3>
              {answer.related.map((s, i) => <PassageCard key={i} p={s} />)}
            </>
          )}
          <Why a={answer} />
        </section>
      )}

      {answer && !answer.supported && (
        <section className="card">
          <div className="verdict no">Not answered by your documents</div>
          <p className="answer">{answer.answer}</p>
          <p className="muted">{answer.reason}</p>
          {answer.closest.length > 0 && (
            <>
              <h3>Closest passages (not enough to answer)</h3>
              {answer.closest.map((s, i) => <PassageCard key={i} p={s} />)}
            </>
          )}
          <Why a={answer} />
        </section>
      )}
    </div>
  );
}
