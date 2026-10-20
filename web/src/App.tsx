import { Label } from "@radix-ui/react-label";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";
import { apiBase, authHeaders, clientFor, errorDetail, isJob, type Job } from "./api/client";
import ConnectScreen from "./desktop/ConnectScreen";
import { isTauriEnvironment } from "./desktop/local";
import Landing, { readTheme, writeTheme } from "./landing/Landing";
import { IconGear } from "./viewer/controls";
import type { ThemeName } from "./viewer/appearance";
import Viewer from "./viewer/Viewer";

function useHashRoute(): "home" | "viewer" {
  const [route, setRoute] = useState<"home" | "viewer">(() =>
    window.location.hash.replace(/^#/, "") === "/viewer" ? "viewer" : "home",
  );
  useEffect(() => {
    const onHash = () => setRoute(window.location.hash.replace(/^#/, "") === "/viewer" ? "viewer" : "home");
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  return route;
}

const TOKEN_KEY = "coperceive-token";

function storedToken(): string {
  return sessionStorage.getItem(TOKEN_KEY) ?? "";
}

export default function App() {
  const queryClient = useQueryClient();
  const route = useHashRoute();
  const [isTauri] = useState(isTauriEnvironment);
  const isRemoteOrHttp =
    typeof window !== "undefined" &&
    (window.location.protocol === "http:" || window.location.protocol === "https:");
  const [connected, setConnected] = useState(
    () => !isTauriEnvironment() || isRemoteOrHttp || Boolean(storedToken()),
  );
  const [token, setToken] = useState(storedToken);
  const [theme, setTheme] = useState<ThemeName>(readTheme);
  const [argument, setArgument] = useState("hello from the data desk");
  const [filePath, setFilePath] = useState("");
  const [fileText, setFileText] = useState("");
  const [fileError, setFileError] = useState("");
  const [streamNote, setStreamNote] = useState("Event stream idle.");
  const [tab, setTab] = useState<"viewer" | "desk">("viewer");
  const [slot, setSlot] = useState<HTMLDivElement | null>(null);
  const [actions, setActions] = useState<HTMLDivElement | null>(null);

  useEffect(() => {
    sessionStorage.setItem(TOKEN_KEY, token);
  }, [token]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  const health = useQuery({
    queryKey: ["health", token],
    queryFn: async () => {
      const result = await clientFor(token).GET("/api/health");
      if (!result.response.ok || !result.data) {
        throw new Error(await errorDetail(result.response));
      }
      return result.data;
    },
  });

  const jobs = useQuery({
    queryKey: ["jobs", token],
    queryFn: async () => {
      const result = await clientFor(token).GET("/api/jobs");
      if (!result.response.ok || !result.data) {
        throw new Error(await errorDetail(result.response));
      }
      return result.data.filter(isJob);
    },
    refetchInterval: 2000,
  });

  const createJob = useMutation({
    mutationFn: async (args: string[]) => {
      const result = await clientFor(token).POST("/api/jobs", {
        body: { name: "echo", args },
        headers: { "X-Coperceive-Request": "1" },
      });
      if (!result.response.ok) {
        throw new Error(await errorDetail(result.response));
      }
      return result.data;
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["jobs", token] });
    },
  });

  useEffect(() => {
    const controller = new AbortController();
    const headers = authHeaders(token);
    void (async () => {
      try {
        const response = await fetch(`${apiBase()}/api/events`, {
          headers,
          signal: controller.signal,
        });
        if (!response.ok || !response.body) {
          setStreamNote(`Event stream refused: ${await errorDetail(response)}`);
          return;
        }
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        setStreamNote("Event stream open.");
        for (;;) {
          const chunk = await reader.read();
          if (chunk.done) {
            setStreamNote("Event stream closed.");
            break;
          }
          buffer += decoder.decode(chunk.value, { stream: true });
          if (buffer.includes("data:")) {
            setStreamNote("Event stream delivered a job snapshot.");
            await queryClient.invalidateQueries({ queryKey: ["jobs", token] });
          }
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          const message = error instanceof Error ? error.message : "stream failed";
          setStreamNote(message);
        }
      }
    })();
    return () => controller.abort();
  }, [queryClient, token]);

  async function readFile(event: FormEvent) {
    event.preventDefault();
    setFileError("");
    setFileText("");
    const response = await fetch(
      `${apiBase()}/api/files?path=${encodeURIComponent(filePath)}`,
      { headers: authHeaders(token) },
    );
    if (!response.ok) {
      setFileError(await errorDetail(response));
      return;
    }
    setFileText(await response.text());
  }

  const jobRows: Job[] = jobs.data ?? [];

  if (isTauri && !connected) {
    return (
      <ConnectScreen
        onConnected={(session) => {
          setToken(session.token);
          setConnected(true);
        }}
      />
    );
  }

  if (route === "home") {
    return (
      <Landing
        token={token}
        theme={theme}
        onTheme={(next) => {
          writeTheme(next);
          setTheme(next);
        }}
        onOpen={() => {
          window.location.hash = "#/viewer";
        }}
      />
    );
  }

  return (
    <div className={tab === "viewer" ? "shell" : "shell shell-desk"}>
      <header className="topbar">
        <div className="top-cluster">
          <a className="brand" href="#/">
            <span className="brand-mark" aria-hidden="true" />
            coperceive
          </a>
          <div className="top-slot" ref={setSlot} />
        </div>
        <div className="tabs" role="tablist" aria-label="Sections">
          <button
            type="button"
            role="tab"
            aria-selected={tab === "viewer"}
            className={tab === "viewer" ? "tab selected" : "tab"}
            onClick={() => setTab("viewer")}
          >
            Viewer
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "desk"}
            className={tab === "desk" ? "tab selected" : "tab"}
            onClick={() => setTab("desk")}
          >
            Desk
          </button>
        </div>
        <div className="top-cluster top-end">
          <div className="top-actions" ref={setActions} />
          <details className="settings">
            <summary className="icon ghost" aria-label="Settings">
              <IconGear />
            </summary>
          <div className="settings-pop">
            <label htmlFor="token-global">
              Bearer token
              <input
                id="token-global"
                type="password"
                autoComplete="off"
                value={token}
                onChange={(event) => setToken(event.target.value)}
                placeholder="Empty when the server has no token"
              />
            </label>
            <p className="muted">Stored in this tab only. A non-loopback bind refuses to start without a token.</p>
          </div>
          </details>
        </div>
      </header>
      {tab === "viewer" ? (
        <Viewer token={token} slot={slot} actions={actions} onTheme={setTheme} />
      ) : null}

      {tab === "desk" ? <div className="desk"><h1>Local data desk</h1><p className="muted">This desk runs the allow-listed <code>echo</code> job.</p><div className="grid">
        <section className="card">
          <h2>Jobs</h2>
          <form
            className="stack"
            onSubmit={(event) => {
              event.preventDefault();
              createJob.mutate([argument]);
            }}
          >
            <Label htmlFor="echo-arg">Text for echo</Label>
            <input
              id="echo-arg"
              value={argument}
              onChange={(event) => setArgument(event.target.value)}
            />
            <button type="submit" disabled={createJob.isPending}>
              {createJob.isPending ? "Running echo…" : "Run echo"}
            </button>
          </form>
          {createJob.isError ? (
            <p className="alert" role="alert">
              {createJob.error.message}
            </p>
          ) : null}
          {jobs.isLoading ? <p className="muted">Loading jobs…</p> : null}
          {jobs.isError ? (
            <p className="alert" role="alert">
              Could not list jobs. {jobs.error.message}
            </p>
          ) : null}
          {jobs.isSuccess && jobRows.length === 0 ? (
            <p className="muted">No jobs yet. Echo is the only command this build will run.</p>
          ) : null}
          {jobRows.length > 0 ? (
            <ul className="jobs">
              {jobRows.map((job) => (
                <li key={job.id}>
                  <strong>#{job.id}</strong> <span className="mono">{job.argv}</span>{" "}
                  <span className={job.status === "ok" ? "ok" : "bad"}>{job.status}</span>
                  <pre className="output">{job.output}</pre>
                </li>
              ))}
            </ul>
          ) : null}
          <p className="muted">{streamNote} The list also refreshes every two seconds.</p>
        </section>

        <div className="stack">
          <section className="card">
            <h2>This process</h2>
            {health.isLoading ? <p className="muted">Checking the API…</p> : null}
            {health.isError ? (
              <p className="alert" role="alert">
                The API did not answer. {health.error.message}
              </p>
            ) : null}
            {health.isSuccess ? (
              <p className="ok">
                API is up. Root path: <span className="mono">{health.data.root_path || "/"}</span>
              </p>
            ) : null}
            <Label htmlFor="token">Bearer token</Label>
            <input
              id="token"
              type="password"
              autoComplete="off"
              value={token}
              onChange={(event) => setToken(event.target.value)}
              placeholder="Leave empty when the server has no token"
            />
            <p className="muted">
              Stored in this tab only. A non-loopback bind refuses to start without a token.
            </p>
          </section>

          <section className="card">
            <h2>Read a file under the server root</h2>
            <form className="stack" onSubmit={readFile}>
              <Label htmlFor="file-path">Relative path</Label>
              <input
                id="file-path"
                value={filePath}
                onChange={(event) => setFilePath(event.target.value)}
                placeholder="note.txt"
              />
              <button type="submit">Read</button>
            </form>
            {!filePath && !fileText && !fileError ? (
              <p className="muted">Enter a path inside the directory passed to coperceive serve.</p>
            ) : null}
            {fileError ? (
              <p className="alert" role="alert">
                {fileError}
              </p>
            ) : null}
            {fileText ? <pre className="output">{fileText}</pre> : null}
          </section>
        </div>
      </div></div> : null}
    </div>
  );
}
