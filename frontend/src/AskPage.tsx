import { FormEvent, useState } from "react";
import { Answer, api, Passage } from "./api";
import { CheckIcon, FileIcon, NoAnswerIcon, PinIcon, SearchIcon } from "./icons";

const date = (iso: string) =>
  new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });

function PassageCard({ p, weak }: { p: Passage; weak?: boolean }) {
  const [start, end] = p.highlight ?? [0, 0];
  return (
    <div className={weak ? "source weak" : "source"}>
      <div className="source-head">
        <div className="source-doc">
          <FileIcon size={17} />
          <strong>{p.document}</strong>
          {p.document_version && <span className="pill">version {p.document_version}</span>}
        </div>
        <div className="location"><PinIcon size={14} />{p.location.label}</div>
      </div>
      <blockquote>
        {p.highlight ? (
          <>{p.passage.slice(0, start)}<mark>{p.passage.slice(start, end)}</mark>{p.passage.slice(end)}</>
        ) : p.passage}
      </blockquote>
      <div className="source-foot muted small">
        {p.filename} · uploaded {date(p.uploaded_at)} (upload {p.upload_no})
        {p.document_owner && <> · owner: {p.document_owner}</>}
      </div>
    </div>
  );
}

function Why({ a }: { a: Answer }) {
  const best = a.sources[0] ?? a.closest[0];
  const covered = best ? Math.min(100, Math.round(best.coverage * 100)) : 0;
  const needed = Math.round(a.threshold * 100);
  return (
    <details className="why">
      <summary>How this was decided</summary>
      <p>
        A passage counts as support only if it covers at least {needed}% of
        what the question asks about, and the quoted text is found word for word in the stored document.
        This check is done in code, not by a model.
      </p>
      {best && (
        <>
          <div className={a.supported ? "meter pass" : "meter"} aria-hidden="true">
            <div className="meter-fill" style={{ width: `${covered}%` }} />
            <div className="meter-mark" style={{ left: `${needed}%` }} />
          </div>
          <ul>
            <li>Best passage covers {covered}% of the question (the line marks the {needed}% needed).</li>
            {best.matched_terms.length > 0 && <li>Found: {best.matched_terms.join(", ")}</li>}
            {best.missing_terms.length > 0 && <li>Not found: {best.missing_terms.join(", ")}</li>}
            {best.required_missing.length > 0 && (
              <li>The question turns on “{best.required_missing.join(", ")}”, which this passage does not mention.</li>
            )}
          </ul>
        </>
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
    setAnswer(null);   // never leave an old answer under a new question
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
      <div className="page-head">
        <h1>Ask your documents</h1>
        <p className="muted">
          Every answer is quoted word for word from a document you uploaded. If the documents
          do not cover the question, you are told so.
        </p>
      </div>

      <form className="ask" onSubmit={submit}>
        <div className="ask-field">
          <SearchIcon />
          <input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="For example: How long are backups kept?"
            aria-label="Your question"
            maxLength={500}
            autoFocus
          />
        </div>
        <button className="primary" disabled={loading || !question.trim()}>{loading ? "Searching…" : "Ask"}</button>
      </form>

      {readyCount === 0 && (
        <div className="notice">
          No documents are indexed yet. <button className="link" onClick={goToDocuments}>Upload some documents</button> to get started.
        </div>
      )}
      {error && <div className="error">{error}</div>}

      {loading && (
        <section className="card skeleton" aria-label="Searching"><div /><div /><div /></section>
      )}

      <div aria-live="polite">
        {answer && answer.supported && (
          <section className="card result">
            <div className="verdict ok"><CheckIcon />Supported by your documents</div>
            <div className="result-body">
              <p className="answer">{answer.answer}</p>
              <h3>Source</h3>
              {answer.sources.map((s, i) => <PassageCard key={i} p={s} />)}
              {answer.related.length > 0 && (
                <>
                  <h3>Related passages</h3>
                  {answer.related.map((s, i) => <PassageCard key={i} p={s} weak />)}
                </>
              )}
              <Why a={answer} />
            </div>
          </section>
        )}

        {answer && !answer.supported && (
          <section className="card result">
            <div className="verdict no"><NoAnswerIcon />Not answered by your documents</div>
            <div className="result-body">
              <p className="answer">{answer.answer}</p>
              <p className="muted reason">{answer.reason}</p>
              {answer.closest.length > 0 && (
                <>
                  <h3>Closest passages (not enough to answer)</h3>
                  {answer.closest.map((s, i) => <PassageCard key={i} p={s} weak />)}
                </>
              )}
              <Why a={answer} />
            </div>
          </section>
        )}
      </div>
    </div>
  );
}
