"use client";

import React, { FormEvent, useEffect, useState } from "react";
import { api, BrowserInspection } from "../lib/api";

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

export default function Home() {
  const [user, setUser] = useState<User | null>(null);
  const [categories, setCategories] = useState<Category[]>([]);
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [browserRun, setBrowserRun] = useState<BrowserInspection | null>(null);

  async function loadData(current: User) {
    setUser(current);
    const [nextCategories, nextPolicies] = await Promise.all([
      api<Category[]>("/api/categories"),
      api<Policy[]>("/api/age-policies"),
    ]);
    setCategories(nextCategories);
    setPolicies(nextPolicies);
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
