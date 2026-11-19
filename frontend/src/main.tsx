import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";
function App() {
  const [status, setStatus] = useState("Checking API…");
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    fetch("/health/ready", { signal: controller.signal })
      .then(async response => {
        if (!response.ok) throw new Error("API unavailable");
        const body: unknown = await response.json();
        if (!body || typeof body !== "object" || !("status" in body) || body.status !== "ready")
          throw new Error("Unexpected health response");
        setStatus("API ready");
      })
      .catch(error => { if (error.name !== "AbortError") setStatus("API unavailable"); });
    return () => controller.abort();
  }, [refresh]);
  return <main><p className="eyebrow">FLOWFORGE / FOUNDATION</p>
    <h1>Workflow orchestration</h1>
    <p>The infrastructure foundation is running. Workflow execution follows in the next milestones.</p>
    <section aria-label="API connection"><h2>System connection</h2>
    <p role="status">{status}</p><button onClick={() => {
      setStatus("Checking API…"); setRefresh(x => x + 1);
    }}>Check again</button></section></main>;
}
createRoot(document.getElementById("root")!).render(<App />);
