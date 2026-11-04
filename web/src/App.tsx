import { Label } from "@radix-ui/react-label";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";
import { apiBase, authHeaders, clientFor, errorDetail, isJob, type Job } from "./api/client";
import { BrandWordmark } from "./desktop/Brand";
import ConnectScreen from "./desktop/ConnectScreen";
import { isTauriEnvironment, isViewerWindow, showLibrary } from "./desktop/local";
import { DesktopPreferences } from "./desktop/SettingsView";
import UpdateBanner from "./desktop/UpdateBanner";
import { CrashNotice } from "./desktop/CrashNotice";
import { DiagnosticsDialog } from "./desktop/DiagnosticsDialog";
import { startupCrash, type StartupCrash } from "./desktop/local";
import { installGlobalErrorRecorders } from "./desktop/globalErrors";
import Landing, { writeTheme } from "./landing/Landing";
import { IconGear } from "./viewer/controls";
import { systemTheme, type ThemeName } from "./viewer/appearance";
import Viewer from "./viewer/Viewer";

function useHashRoute(): "home" | "viewer" | "connect" {
  const isDesktop = typeof window !== "undefined" && (Boolean(window.__COPERCEIVE_DESKTOP__) || isTauriEnvironment());
  const isConnect = (raw: string) => raw === "/connect" || raw.startsWith("/connect/") || raw.startsWith("/connect?");
  const [route, setRoute] = useState<"home" | "viewer" | "connect">(() => {
    const raw = typeof window !== "undefined" ? window.location.hash.replace(/^#/, "") : "";
    // The viewer window has no IPC: it only ever shows the viewer.
    if (isViewerWindow()) return "viewer";
    if (isConnect(raw)) return "connect";
    if (raw === "/viewer" || raw.startsWith("/viewer?")) return "viewer";
    if (isDesktop) return "viewer";
    return "home";
  });
  useEffect(() => {
    const onHash = () => {
      const raw = window.location.hash.replace(/^#/, "");
      if (isViewerWindow()) {
        // A Library link here means the main window's Library; stay on the viewer.
        // `/viewer?scene=…` is the app choosing a scene in this viewer.
        if (raw !== "/viewer" && !raw.startsWith("/viewer?")) {
          void showLibrary().catch(() => {});
          window.history.replaceState(null, "", "#/viewer");
        }
        setRoute("viewer");
        return;
      }
      if (isConnect(raw)) {
        setRoute("connect");
        return;
      }
      if (raw === "/viewer" || raw.startsWith("/viewer?")) {
        setRoute("viewer");
        return;
      }
      const isDesktopNow = typeof window !== "undefined" && (Boolean(window.__COPERCEIVE_DESKTOP__) || isTauriEnvironment());
      if (isDesktopNow) {
        setRoute("connect");
        return;
      }
      setRoute("home");
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  return route;
}

const TOKEN_KEY = "coperceive-token";

function storedToken(): string {
  try {
    return (typeof sessionStorage !== "undefined" ? sessionStorage.getItem(TOKEN_KEY) ?? "" : "");
  } catch {
    return "";
  }
}

/**
 * The unified toolbar (macOS): with the native title bar hidden, the top bar
 * leaves room for the traffic lights, except in full screen where macOS hides
 * them. Marks <html data-titlebar="overlay" data-fullscreen="…">.
 */
function useTitlebarOverlay() {
  useEffect(() => {
    const root = document.documentElement;
    const apply = () => {
      if (window.__COPERCEIVE_TITLEBAR__ === "overlay") {
        root.dataset.titlebar = "overlay";
      }
      const full = window.innerWidth >= window.screen.width && window.innerHeight >= window.screen.height;
      root.dataset.fullscreen = full ? "true" : "false";
    };
    apply();
    window.addEventListener("resize", apply);
    window.addEventListener("coperceive-titlebar", apply);
    return () => {
      window.removeEventListener("resize", apply);
      window.removeEventListener("coperceive-titlebar", apply);
    };
  }, []);
}

export default function App() {
  useTitlebarOverlay();
  const isDesktop = typeof window !== "undefined" && (Boolean(window.__COPERCEIVE_DESKTOP__) || isTauriEnvironment());
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
  const [theme, setTheme] = useState<ThemeName>(() => {
    if (typeof window !== "undefined" && window.__COPERCEIVE_INITIAL_STATE__?.theme_override) {
      const saved = window.__COPERCEIVE_INITIAL_STATE__.theme_override;
      if (saved === "dark" || saved === "light") {
        return saved;
      }
    }
    return systemTheme();
  });
  const [hasThemeOverride, setHasThemeOverride] = useState<boolean>(() => {
    return typeof window !== "undefined" && Boolean(window.__COPERCEIVE_INITIAL_STATE__?.theme_override);
  });

  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mql = window.matchMedia("(prefers-color-scheme: dark)");
    const handler = (e: MediaQueryListEvent) => {
      if (!hasThemeOverride) {
        const next = e.matches ? "dark" : "light";
        setTheme(next);
        document.documentElement.dataset.theme = next;
      }
    };
    mql.addEventListener("change", handler);
    return () => mql.removeEventListener("change", handler);
  }, [hasThemeOverride]);
  const [argument, setArgument] = useState("hello from the data desk");
  const [filePath, setFilePath] = useState("");
  const [fileText, setFileText] = useState("");
  const [fileError, setFileError] = useState("");
  const [streamNote, setStreamNote] = useState("Event stream idle.");
  const [tab, setTab] = useState<"viewer" | "desk">("viewer");
  const [slot, setSlot] = useState<HTMLDivElement | null>(null);
  const [actions, setActions] = useState<HTMLDivElement | null>(null);
  const [crash, setCrash] = useState<StartupCrash | null>(null);
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false);

  useEffect(() => {
    if (!isTauri) return;
    void startupCrash().then((record) => {
      if (!record) return;
      let dismissed = false;
      try {
        dismissed = localStorage.getItem(`coperceive-dismissed-crash-${record.timestamp_unix}`) === "1";
      } catch {
        dismissed = false;
      }
      if (!dismissed) setCrash(record);
    });
    const open = () => setDiagnosticsOpen(true);
    window.addEventListener("coperceive-open-diagnostics", open);
    return () => window.removeEventListener("coperceive-open-diagnostics", open);
  }, [isTauri]);

  useEffect(() => installGlobalErrorRecorders(), []);

  useEffect(() => {
    // Main window: the viewer window's wordmark asks for the Library.
    if (!isTauri || isViewerWindow()) return;
    let unlisten: (() => void) | undefined;
    let cancelled = false;
    void (async () => {
      const { listen } = await import("@tauri-apps/api/event");
      const stop = await listen("navigate-library", () => {
        window.location.hash = "#/connect";
      });
      if (cancelled) stop();
      else unlisten = stop;
    })();
    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, [isTauri]);

  useEffect(() => {
    if (isDesktop && !window.location.hash) {
      window.location.hash = connected ? "#/viewer" : "#/connect";
    }
  }, [isDesktop, connected]);

  useEffect(() => {
    if (token && typeof sessionStorage !== "undefined") {
      try {
        sessionStorage.setItem(TOKEN_KEY, token);
      } catch {}
    }
  }, [token]);

  useEffect(() => {
    if (!token && typeof sessionStorage !== "undefined") {
      try {
        const stored = sessionStorage.getItem(TOKEN_KEY);
        if (stored) {
          setToken(stored);
        }
      } catch {}
    }
  }, [token]);

  useEffect(() => {
    const checkToken = () => {
      try {
        const stored = sessionStorage.getItem(TOKEN_KEY);
        if (stored && stored !== token) {
          setToken(stored);
        }
      } catch {}
    };
    window.addEventListener("storage", checkToken);
    window.addEventListener("coperceive-token-sync", checkToken);
    return () => {
      window.removeEventListener("storage", checkToken);
      window.removeEventListener("coperceive-token-sync", checkToken);
    };
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

  if ((isTauri && !connected) || route === "connect") {
    return (
      <ConnectScreen
        onConnected={(session) => {
          setToken(session.token);
          setConnected(true);
          window.location.hash = "#/viewer";
        }}
      />
    );
  }

  if (route === "home") {
    if (isDesktop) {
      return (
        <ConnectScreen
          onConnected={(session) => {
            setToken(session.token);
            setConnected(true);
            window.location.hash = "#/viewer";
          }}
        />
      );
    }
    return (
      <Landing
        token={token}
        theme={theme}
        onTheme={(next) => {
          setHasThemeOverride(true);
          writeTheme(next);
          setTheme(next);
          if (typeof window !== "undefined" && window.__COPERCEIVE_CTRL_PORT__) {
            const port = window.__COPERCEIVE_CTRL_PORT__;
            const secret = window.__COPERCEIVE_CTRL_SECRET__ || "";
            void fetch(`http://127.0.0.1:${port}/state`, {
              method: "POST",
              headers: {
                "Content-Type": "application/json",
                Authorization: `Bearer ${secret}`,
              },
              body: JSON.stringify({ theme_override: next }),
            }).catch(() => {});
          }
        }}
        onOpen={() => {
          window.location.hash = "#/viewer";
        }}
      />
    );
  }

  return (
    <div className={tab === "viewer" || isDesktop ? "shell" : "shell shell-desk"}>
      {isDesktop && <UpdateBanner />}
      {crash ? (
        <CrashNotice
          crash={crash}
          onShare={() => setDiagnosticsOpen(true)}
          onDismiss={() => {
            try {
              localStorage.setItem(`coperceive-dismissed-crash-${crash.timestamp_unix}`, "1");
            } catch {
              // The notice can still be dismissed for this session.
            }
            setCrash(null);
          }}
        />
      ) : null}
      {diagnosticsOpen ? (
        <DiagnosticsDialog
          context={{
            active_route: typeof window !== "undefined" ? window.location.hash || "#/" : "#/",
            quick_copy_diagnostics: true,
            last_ui_error:
              (window as unknown as { __COPERCEIVE_LAST_ERROR__?: unknown }).__COPERCEIVE_LAST_ERROR__ ?? null,
          }}
          onClose={() => setDiagnosticsOpen(false)}
        />
      ) : null}
      {/* Empty toolbar space moves the window and double-click zooms it. */}
      <header className="topbar" data-tauri-drag-region>
        <div className="top-cluster" data-tauri-drag-region>
          <a
            className="brand"
            href={isViewerWindow() ? "#/viewer" : isDesktop ? "#/connect" : "#/"}
            onClick={(e) => {
              if (isViewerWindow()) {
                // No navigation here: the main window shows its Library instead.
                e.preventDefault();
                void showLibrary().catch((err: unknown) => {
                  console.warn("show-library failed", err);
                });
              } else if (isDesktop) {
                e.preventDefault();
                window.location.hash = "#/connect";
              }
            }}
            aria-label={isDesktop ? "Return to Library" : "coperceive"}
          >
            <BrandWordmark />
          </a>
          <div className="top-slot" ref={setSlot} data-tauri-drag-region />
        </div>
        {!isDesktop && (
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
        )}
        <div className="top-cluster top-end" data-tauri-drag-region>
          <div className="top-actions" ref={setActions} />
          <details className="settings">
            <summary className="icon ghost" aria-label="Settings">
              <IconGear />
            </summary>
            <div className="settings-pop">
              {!isDesktop ? (
                <>
                  <label htmlFor="token-global">
                    Bearer token
                    <input
                      id="token-global"
                      type="password"
                      autoCapitalize="off"
                      autoCorrect="off"
                      spellCheck={false}
                      autoComplete="off"
                      value={token}
                      onChange={(event) => setToken(event.target.value)}
                      placeholder="Empty when the server has no token"
                    />
                  </label>
                  <p className="muted">Stored in this tab only. A non-loopback bind refuses to start without a token.</p>
                </>
              ) : (
                <DesktopPreferences />
              )}
            </div>
          </details>
        </div>
      </header>
      {tab === "viewer" || isDesktop ? (
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
              autoCapitalize="off"
              autoCorrect="off"
              spellCheck={false}
              autoComplete="off"
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
              autoCapitalize="off"
              autoCorrect="off"
              spellCheck={false}
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
                autoCapitalize="off"
                autoCorrect="off"
                spellCheck={false}
                autoComplete="off"
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
