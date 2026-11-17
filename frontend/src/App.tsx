import { useEffect, useState } from "react";
import { api, Doc, Me, Unauthorised } from "./api";
import { AskPage } from "./AskPage";
import { DocumentsPage } from "./DocumentsPage";
import { LoginPage } from "./LoginPage";

type Tab = "ask" | "documents";

export function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [checking, setChecking] = useState(true);
  const [tab, setTab] = useState<Tab>("ask");
  const [docs, setDocs] = useState<Doc[]>([]);
  const [live, setLive] = useState(false);

  const loadMe = () =>
    api.me().then(setMe).catch(() => setMe(null)).finally(() => setChecking(false));
  useEffect(() => { loadMe(); }, []);

  // Any API call that finds the session gone sends us back to the sign-in page.
  useEffect(() => {
    const expired = () => setMe(null);
    window.addEventListener("session-expired", expired);
    return () => window.removeEventListener("session-expired", expired);
  }, []);

  // One live connection for the whole app: the server pushes the document list
  // whenever a status changes. The browser reconnects by itself if it drops.
  useEffect(() => {
    if (!me) return;
    const source = new EventSource("/api/documents/events");
    source.onopen = () => setLive(true);
    source.onmessage = (event) => setDocs(JSON.parse(event.data).documents);
    source.onerror = () => {
      setLive(false);
      api.me().catch((e) => e instanceof Unauthorised && setMe(null));
    };
    return () => source.close();
  }, [me]);

  if (checking) return null;
  if (!me) return <LoginPage onLogin={loadMe} />;

  const ready = docs.filter((d) => d.status === "ready" || d.previous_version_live).length;
  const busy = docs.filter((d) => ["queued", "extracting", "chunking", "indexing"].includes(d.status)).length;

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">Policy Q&amp;A</div>
        <nav>
          <button className={tab === "ask" ? "tab active" : "tab"} onClick={() => setTab("ask")}>
            Ask
          </button>
          <button className={tab === "documents" ? "tab active" : "tab"} onClick={() => setTab("documents")}>
            Documents
            {busy > 0 && <span className="pill busy">{busy} indexing</span>}
          </button>
        </nav>
        <div className="topbar-right">
          <span className="muted small">{me.retriever} + {me.answerer}</span>
          <button className="link" onClick={() => api.logout().then(() => setMe(null))}>Sign out</button>
        </div>
      </header>
      <main>
        {/* Both pages stay mounted so a question is not lost when checking uploads. */}
        <div hidden={tab !== "ask"}><AskPage readyCount={ready} goToDocuments={() => setTab("documents")} /></div>
        <div hidden={tab !== "documents"}><DocumentsPage docs={docs} live={live} me={me} /></div>
      </main>
    </div>
  );
}
