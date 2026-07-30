"use client";

import React, { FormEvent, useEffect, useState } from "react";
import { api, AIClassification, BrowserInspection } from "../lib/api";

type User = { id: string; email: string; role: "admin" | "viewer" };
type Category = { id: string; name: string; slug: string; description: string };
type Policy = {
  id: string;
  name: string;
  minimum_age: number;
  maximum_age: number;
  description: string;
  is_active: boolean;
};
type AISettings = {
  enabled: boolean;
  provider: string;
  model: string;
  confidence_threshold: number;
  conflict_threshold: number;
  screenshot_enabled: boolean;
  retry_limit: number;
  daily_request_limit: number;
  monthly_cost_limit: number;
};
type Dataset = {
  id: string;
  name: string;
  version: string;
  checksum: string;
  published: boolean;
  example_count: number;
};
type EvaluationRun = {
  id: string;
  status: string;
  total_count: number;
  processed_count: number;
  metrics: Record<string, unknown>;
};
type Pilot = {
  id: string;
  size: number;
  dry_run: boolean;
  status: string;
  estimate: Record<string, number>;
  estimate_hash: string;
};
type ClassifierVersion = {
  id: string;
  version: string;
  is_active: boolean;
  change_notes: string;
};
type ReviewCase = {
  id: string;
  status: string;
  reason: string;
  locked: boolean;
};

export default function Home() {
  const [user, setUser] = useState<User | null>(null);
  const [categories, setCategories] = useState<Category[]>([]);
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [browserRun, setBrowserRun] = useState<BrowserInspection | null>(null);
  const [aiRun, setAiRun] = useState<AIClassification | null>(null);
  const [aiSettings, setAiSettings] = useState<AISettings | null>(null);
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [evaluationRuns, setEvaluationRuns] = useState<EvaluationRun[]>([]);
  const [pilots, setPilots] = useState<Pilot[]>([]);
  const [classifierVersions, setClassifierVersions] = useState<
    ClassifierVersion[]
  >([]);
  const [reviewCases, setReviewCases] = useState<ReviewCase[]>([]);

  async function loadData(current: User) {
    setUser(current);
    const [nextCategories, nextPolicies] = await Promise.all([
      api<Category[]>("/api/categories"),
      api<Policy[]>("/api/age-policies"),
    ]);
    setCategories(nextCategories);
    setPolicies(nextPolicies);
    if (current.role === "admin") {
      setAiSettings(await api<AISettings>("/api/ai/settings"));
      const [nextDatasets, nextRuns, nextPilots, nextVersions, nextReviews] =
        await Promise.all([
          api<Dataset[]>("/api/evaluation/datasets"),
          api<EvaluationRun[]>("/api/evaluation/runs"),
          api<Pilot[]>("/api/pilots"),
          api<ClassifierVersion[]>("/api/classifier/versions"),
          api<ReviewCase[]>("/api/manual-reviews"),
        ]);
      setDatasets(nextDatasets);
      setEvaluationRuns(nextRuns);
      setPilots(nextPilots);
      setClassifierVersions(nextVersions);
      setReviewCases(nextReviews);
    }
  }

  useEffect(() => {
    api<User>("/api/auth/me")
      .then(loadData)
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);

  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    const data = new FormData(event.currentTarget);
    try {
      const current = await api<User>("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({
          email: data.get("email"),
          password: data.get("password"),
        }),
      });
      await loadData(current);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Login failed");
    }
  }

  async function logout() {
    await api<void>("/api/auth/logout", { method: "POST" });
    setUser(null);
  }

  async function requestBrowser(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    const data = new FormData(event.currentTarget);
    try {
      const response = await api<{ browser: BrowserInspection }>(
        `/api/browser/websites/${encodeURIComponent(String(data.get("website_id")))}/reinspect`,
        {
          method: "POST",
          body: JSON.stringify({
            capture_screenshot: data.get("capture_screenshot") === "on",
          }),
        },
      );
      setBrowserRun(response.browser);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Browser inspection failed",
      );
    }
  }

  async function requestAI(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    const data = new FormData(event.currentTarget);
    try {
      const response = await api<{ ai: AIClassification }>(
        `/api/ai/websites/${encodeURIComponent(String(data.get("website_id")))}/classify`,
        { method: "POST", body: JSON.stringify({ trigger: "admin" }) },
      );
      setAiRun(response.ai);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "AI request failed");
    }
  }

  async function saveAISettings(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!aiSettings) return;
    try {
      setAiSettings(
        await api<AISettings>("/api/ai/settings", {
          method: "PUT",
          body: JSON.stringify(aiSettings),
        }),
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "AI settings failed");
    }
  }

  async function importDataset(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
      const item = await api<Dataset>("/api/evaluation/datasets/import", {
        method: "POST",
        body: JSON.stringify({
          name: data.get("name"),
          version: data.get("version"),
          source_format: data.get("source_format"),
          content: data.get("content"),
          change_notes: data.get("change_notes"),
          publish: true,
        }),
      });
      setDatasets([item, ...datasets]);
      event.currentTarget.reset();
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Dataset import failed",
      );
    }
  }

  async function createDryRun(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
      const item = await api<Pilot>("/api/pilots", {
        method: "POST",
        body: JSON.stringify({
          size: Number(data.get("size")),
          rank_start: Number(data.get("rank_start")),
          capacity_limit: Number(data.get("capacity_limit")),
          dry_run: true,
        }),
      });
      setPilots([item, ...pilots]);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Pilot estimate failed",
      );
    }
  }

  if (loading) return <main className="center">Loading…</main>;
  if (!user) {
    return (
      <main className="center">
        <form className="card login" onSubmit={login}>
          <p className="eyebrow">WEBAGE</p>
          <h1>Admin sign in</h1>
          <label>
            Email
            <input name="email" type="email" autoComplete="username" required />
          </label>
          <label>
            Password
            <input
              name="password"
              type="password"
              autoComplete="current-password"
              required
            />
          </label>
          {error && <p role="alert">{error}</p>}
          <button type="submit">Sign in</button>
        </form>
      </main>
    );
  }
  return (
    <main className="shell">
      <header>
        <div>
          <p className="eyebrow">WEBAGE / ADMIN</p>
          <h1>Policy overview</h1>
        </div>
        <div className="account">
          <span>{user.email}</span>
          <span className="badge">{user.role}</span>
          <button className="secondary" onClick={logout}>
            Sign out
          </button>
        </div>
      </header>
      <section className="stats">
        <article className="card">
          <strong>{categories.length}</strong>
          <span>Categories</span>
        </article>
        <article className="card">
          <strong>{policies.length}</strong>
          <span>Age policies</span>
        </article>
        <article className="card">
          <strong>
            {policies.filter((policy) => policy.is_active).length}
          </strong>
          <span>Active policies</span>
        </article>
      </section>
      <section className="grid">
        <article className="card">
          <h2>Categories</h2>
          <ul>
            {categories.map((category) => (
              <li key={category.id}>
                <div>
                  <strong>{category.name}</strong>
                  <p>{category.description}</p>
                </div>
                <code>{category.slug}</code>
              </li>
            ))}
          </ul>
        </article>
        <article className="card">
          <h2>Age policies</h2>
          <ul>
            {policies.map((policy) => (
              <li key={policy.id}>
                <div>
                  <strong>{policy.name}</strong>
                  <p>{policy.description || "Configured audience range"}</p>
                </div>
                <span className="range">
                  {policy.minimum_age}–{policy.maximum_age}
                </span>
              </li>
            ))}
          </ul>
        </article>
      </section>
      {user.role === "admin" && (
        <section className="card milestone">
          <h2>Classifier evaluation</h2>
          <p>
            Published labeled datasets are immutable. Corrections require a new
            version linked to the prior dataset.
          </p>
          <form onSubmit={importDataset}>
            <label>
              Dataset name
              <input name="name" required maxLength={120} />
            </label>
            <label>
              Version
              <input
                name="version"
                required
                pattern="[A-Za-z0-9][A-Za-z0-9._-]*"
              />
            </label>
            <label>
              Format
              <select name="source_format">
                <option value="csv">CSV</option>
                <option value="jsonl">JSONL</option>
              </select>
            </label>
            <label>
              Change notes
              <input name="change_notes" maxLength={1000} />
            </label>
            <label>
              Labeled data
              <textarea name="content" rows={6} required maxLength={5000000} />
            </label>
            <button type="submit">Publish dataset version</button>
          </form>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Dataset</th>
                  <th>Version</th>
                  <th>Examples</th>
                  <th>Checksum</th>
                </tr>
              </thead>
              <tbody>
                {datasets.map((item) => (
                  <tr key={item.id}>
                    <td>{item.name}</td>
                    <td>{item.version}</td>
                    <td>{item.example_count}</td>
                    <td>
                      <code>{item.checksum.slice(0, 12)}</code>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <h3>Evaluation runs</h3>
          <ul>
            {evaluationRuns.map((run) => (
              <li key={run.id}>
                <span>{run.status}</span>
                <span>
                  {run.processed_count}/{run.total_count}
                </span>
                <span>
                  Primary accuracy:{" "}
                  {String(run.metrics.primary_category_accuracy ?? "pending")}
                </span>
              </li>
            ))}
          </ul>
          <h3>Classifier versions</h3>
          <ul>
            {classifierVersions.map((version) => (
              <li key={version.id}>
                <strong>{version.version}</strong>
                <span>
                  {version.is_active ? "active" : "immutable history"}
                </span>
                <span>{version.change_notes}</span>
              </li>
            ))}
          </ul>
          <h3>Manual review queue</h3>
          <ul>
            {reviewCases.map((item) => (
              <li key={item.id}>
                <strong>{item.status}</strong>
                <span>{item.reason}</span>
                <span>{item.locked ? "locked" : "open"}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
      {user.role === "admin" && (
        <section className="card milestone">
          <h2>Controlled pilot dry-run</h2>
          <p>
            Estimate capacity, runtime, cost, and storage without dispatching
            classification work.
          </p>
          <form onSubmit={createDryRun}>
            <label>
              Pilot size
              <select name="size" defaultValue="100">
                <option value="100">100</option>
                <option value="1000">1,000</option>
                <option value="10000">10,000</option>
              </select>
            </label>
            <label>
              Starting Tranco rank
              <input name="rank_start" type="number" min="1" defaultValue="1" />
            </label>
            <label>
              Queue capacity
              <input
                name="capacity_limit"
                type="number"
                min="1"
                max="1000"
                defaultValue="100"
              />
            </label>
            <button type="submit">Calculate dry-run</button>
          </form>
          <ul>
            {pilots.map((pilot) => (
              <li key={pilot.id}>
                <div>
                  <strong>{pilot.size.toLocaleString()} domains</strong>
                  <p>
                    {pilot.status} · {pilot.dry_run ? "dry-run" : "live"}
                  </p>
                </div>
                <span>
                  {pilot.estimate.estimated_runtime_seconds ?? 0}s ·{" "}
                  {pilot.estimate.expected_ai_calls ?? 0} AI calls
                </span>
              </li>
            ))}
          </ul>
          {error && <p role="alert">{error}</p>}
        </section>
      )}
      {user.role === "admin" && (
        <section className="card">
          <h2>AI classification fallback</h2>
          <p>
            AI is used only for ambiguous results. Database age policies remain
            authoritative and low-confidence results require review.
          </p>
          {aiSettings && (
            <form onSubmit={saveAISettings}>
              <label>
                <input
                  type="checkbox"
                  checked={aiSettings.enabled}
                  onChange={(event) =>
                    setAiSettings({
                      ...aiSettings,
                      enabled: event.target.checked,
                    })
                  }
                />
                AI enabled
              </label>
              <label>
                Provider
                <input
                  value={aiSettings.provider}
                  onChange={(event) =>
                    setAiSettings({
                      ...aiSettings,
                      provider: event.target.value,
                    })
                  }
                />
              </label>
              <label>
                Model
                <input
                  value={aiSettings.model}
                  onChange={(event) =>
                    setAiSettings({ ...aiSettings, model: event.target.value })
                  }
                />
              </label>
              <button type="submit">Save AI settings</button>
            </form>
          )}
          <form onSubmit={requestAI}>
            <label>
              Website ID
              <input name="website_id" required pattern="[0-9a-fA-F-]{36}" />
            </label>
            <button type="submit">Request AI recommendation</button>
          </form>
          {aiRun && (
            <div aria-live="polite">
              <strong>Status: {aiRun.status}</strong>
              <p>
                {aiRun.provider} / {aiRun.model} · confidence{" "}
                {aiRun.confidence ?? "unavailable"}
              </p>
              {aiRun.prompt_injection_suspected && (
                <p role="alert">Potential prompt injection detected</p>
              )}
              <ul>
                {aiRun.evidence.map((item, index) => (
                  <li key={index}>{item}</li>
                ))}
              </ul>
              <p>
                {aiRun.promoted
                  ? "Promoted after validation and policy application"
                  : "Not promoted"}
              </p>
            </div>
          )}
        </section>
      )}
      {user.role === "admin" && (
        <section className="card">
          <h2>Browser reinspection</h2>
          <p>
            Browser work runs on isolated workers and does not replace a valid
            classification unless it completes successfully.
          </p>
          <form onSubmit={requestBrowser}>
            <label>
              Website ID
              <input name="website_id" required pattern="[0-9a-fA-F-]{36}" />
            </label>
            <label>
              <input name="capture_screenshot" type="checkbox" />
              Capture screenshot when server policy permits
            </label>
            <button type="submit">Request browser inspection</button>
          </form>
          {browserRun && (
            <div aria-live="polite">
              <strong>Status: {browserRun.status}</strong>
              <p>{browserRun.rendered_title}</p>
              <p>{browserRun.rendered_text_sample}</p>
            </div>
          )}
          {error && <p role="alert">{error}</p>}
        </section>
      )}
    </main>
  );
}
