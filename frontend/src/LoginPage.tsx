import { FormEvent, useState } from "react";
import { api, Unauthorised } from "./api";

export function LoginPage({ onLogin }: { onLogin: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError("");
    try {
      await api.login(username, password);
      onLogin();
    } catch (e) {
      setError(e instanceof Unauthorised ? "Wrong username or password." : "Could not sign in. Is the server running?");
    }
  };

  return (
    <div className="login">
      <form className="card" onSubmit={submit}>
        <h1>Policy Q&amp;A</h1>
        <p className="muted">Ask questions about your policies and see exactly where each answer comes from.</p>
        <label>Username<input value={username} onChange={(e) => setUsername(e.target.value)} autoFocus /></label>
        <label>Password<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} /></label>
        {error && <div className="error">{error}</div>}
        <button className="primary" type="submit">Sign in</button>
      </form>
    </div>
  );
}
