const base = import.meta.env.BASE_URL;

export function Footer() {
  return (
    <footer className="border-t border-hairline px-6 py-10 text-sm text-tertiary sm:px-10">
      <div className="mx-auto flex max-w-6xl flex-col gap-3">
        <p>Apache-2.0</p>
        <ul className="flex flex-col gap-1 sm:flex-row sm:gap-6">
          <li>
            <a className="inline-flex min-h-11 items-center hover:text-primary" href={`${base}licenses/OFL-Geist.txt`}>
              Geist, OFL-1.1
            </a>
          </li>
          <li>
            <a className="inline-flex min-h-11 items-center hover:text-primary" href={`${base}licenses/OFL-Geist-Mono.txt`}>
              Geist Mono, OFL-1.1
            </a>
          </li>
        </ul>
      </div>
    </footer>
  );
}
