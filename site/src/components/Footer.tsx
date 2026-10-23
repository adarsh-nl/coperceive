import { GITHUB_REPO } from "../cta";

const base = import.meta.env.BASE_URL;

export function Footer() {
  return (
    <footer className="border-t border-hairline px-6 py-12 text-sm text-tertiary sm:px-10">
      <div className="mx-auto flex max-w-6xl flex-col gap-6">
        <p translate="no" className="text-primary">
          coperceive
        </p>
        <ul className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:gap-x-6">
          <li>
            <a className="min-h-11 inline-flex items-center hover:text-primary" href={`${GITHUB_REPO}/blob/main/LICENSE`}>
              Apache-2.0
            </a>
          </li>
          <li>
            <a
              className="inline-flex min-h-11 items-center hover:text-primary"
              href={`${GITHUB_REPO}/blob/main/THIRD_PARTY_NOTICES.md`}
            >
              Griffin dataset, MIT
            </a>
          </li>
          <li>
            <a className="inline-flex min-h-11 items-center hover:text-primary" href={`${base}licenses/OFL-Geist.txt`}>
              Geist, OFL-1.1
            </a>
          </li>
          <li>
            <a
              className="inline-flex min-h-11 items-center hover:text-primary"
              href={`${base}licenses/OFL-Geist-Mono.txt`}
            >
              Geist Mono, OFL-1.1
            </a>
          </li>
        </ul>
        <p>CARLA map assets, CC-BY. The map is drawn in the Town03-000 clip. OpenDRIVE bytes are not committed.</p>
        <p>Animated scenes are synthetic and illustrative.</p>
        <p>
          Adarsh, University of Twente.{" "}
          <a className="inline-flex min-h-11 items-center hover:text-primary" href={`${GITHUB_REPO}/blob/main/CITATION.cff`}>
            CITATION.cff
          </a>
        </p>
      </div>
    </footer>
  );
}
