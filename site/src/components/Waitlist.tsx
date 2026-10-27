import { useState, type FormEvent } from "react";

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

type Status = "idle" | "invalid" | "soon" | "sending" | "joined" | "error";

function endpoint(): string {
  return (import.meta.env.VITE_WAITLIST_ENDPOINT ?? "").trim();
}

export function Waitlist() {
  const statusId = "waitlist-status";
  const [email, setEmail] = useState("");
  const [status, setStatus] = useState<Status>("idle");

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = email.trim();
    if (!EMAIL.test(value)) {
      setStatus("invalid");
      return;
    }
    const url = endpoint();
    if (!url) {
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
      setStatus(response.ok ? "joined" : "error");
    } catch {
      setStatus("error");
    }
  }

  const message =
    status === "invalid"
      ? "Enter an email address."
      : status === "soon"
        ? "Waitlist opens soon."
        : status === "sending"
          ? "Sending…"
          : status === "joined"
            ? "You’re on the list."
            : status === "error"
              ? "Could not join. Try again."
              : "";

  return (
    <section id="waitlist" data-pin="false" data-story="waitlist" className="border-t border-hairline">
      <div className="mx-auto max-w-6xl px-6 py-24 sm:px-10">
        <h2 className="text-4xl font-semibold tracking-tight sm:text-5xl">Join the waitlist.</h2>
        <form noValidate className="mt-10 flex max-w-md flex-col gap-3" onSubmit={(event) => void onSubmit(event)}>
          <label htmlFor="email" className="text-sm text-secondary">
            Email
          </label>
          <input
            id="email"
            name="email"
            type="email"
            inputMode="email"
            autoComplete="email"
            spellCheck={false}
            value={email}
            onChange={(event) => {
              setEmail(event.target.value);
              if (status === "invalid") setStatus("idle");
            }}
            aria-invalid={status === "invalid"}
            aria-describedby={statusId}
            className="min-h-11 rounded-full border border-hairline bg-black px-4 text-primary"
          />
          <button
            type="submit"
            className="inline-flex min-h-11 items-center justify-center self-start rounded-full bg-primary px-5 text-sm font-medium text-black transition-colors duration-150 hover:bg-white"
          >
            Join the waitlist
          </button>
          <p id={statusId} className="min-h-5 text-sm text-secondary" aria-live="polite">
            {message}
          </p>
        </form>
      </div>
    </section>
  );
}
