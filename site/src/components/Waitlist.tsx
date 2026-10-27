import { useRef, useState, type FormEvent } from "react";

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

type Status = "idle" | "invalid" | "soon" | "sending" | "joined" | "error";

/** One line per state. Calm and specific, no exclamation marks (landing-page-design B8). */
const MESSAGE: Record<Status, string> = {
  idle: "",
  invalid: "Enter an email address, like name@example.com.",
  soon: "Waitlist opens soon. Nothing was sent.",
  sending: "Sending…",
  joined: "You’re on the list.",
  error: "Could not join. Check your connection and try again.",
};

function endpoint(): string {
  return (import.meta.env.VITE_WAITLIST_ENDPOINT ?? "").trim();
}

export function Waitlist() {
  const statusId = "waitlist-status";
  const inputRef = useRef<HTMLInputElement>(null);
  const [email, setEmail] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const sending = status === "sending";
  const problem = status === "invalid" || status === "error";

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (sending) return;
    const value = email.trim();
    if (!EMAIL.test(value)) {
      setStatus("invalid");
      inputRef.current?.focus();
      return;
    }
    const url = endpoint();
    if (!url) {
      // No service is configured: no request, nothing stored.
      setStatus("soon");
      return;
    }
    setStatus("sending");
    try {
      const response = await fetch(url, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email: value }),
      });
      if (response.ok) {
        setStatus("joined");
        setEmail("");
      } else {
        setStatus("error");
      }
    } catch {
      setStatus("error");
    }
  }

  return (
    <section id="waitlist" data-pin="false" data-story="waitlist" className="border-t border-hairline">
      <div className="mx-auto max-w-6xl px-6 py-24 sm:px-10">
        <h2 className="text-4xl font-semibold tracking-tight sm:text-5xl">Join the waitlist.</h2>
        <form
          noValidate
          aria-busy={sending}
          data-status={status}
          className="mt-10 flex max-w-md flex-col gap-3"
          onSubmit={(event) => void onSubmit(event)}
        >
          <label htmlFor="email" className="text-sm text-secondary">
            Email
          </label>
          <div className="flex flex-col gap-3 sm:flex-row">
            <input
              ref={inputRef}
              id="email"
              name="email"
              type="email"
              inputMode="email"
              autoComplete="email"
              spellCheck={false}
              value={email}
              disabled={sending}
              onChange={(event) => {
                setEmail(event.target.value);
                if (status !== "sending" && status !== "idle") setStatus("idle");
              }}
              aria-invalid={status === "invalid"}
              aria-describedby={statusId}
              className={`min-h-11 flex-1 rounded-full border bg-black px-4 text-primary transition-colors duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] placeholder:text-tertiary disabled:opacity-60 ${
                status === "invalid" ? "border-cursor" : "border-hairline-bright hover:border-white/30"
              }`}
              placeholder="name@example.com"
            />
            <button
              type="submit"
              disabled={sending}
              className="inline-flex min-h-11 items-center justify-center self-start whitespace-nowrap rounded-full bg-primary px-5 text-base font-semibold text-black transition-[background-color,transform] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] hover:bg-white active:scale-[0.98] disabled:cursor-wait disabled:opacity-60 sm:self-auto"
            >
              Join the waitlist
            </button>
          </div>
          <p
            id={statusId}
            role="status"
            aria-live="polite"
            className={`min-h-5 text-sm ${problem ? "text-cursor" : status === "joined" ? "text-primary" : "text-secondary"}`}
          >
            {MESSAGE[status]}
          </p>
        </form>
      </div>
    </section>
  );
}
