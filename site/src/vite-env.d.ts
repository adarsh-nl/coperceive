/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_WAITLIST_ENDPOINT?: string;
}

/** Latest GitHub release, slimming to name and URL, injected by vite.config.ts. */
declare const __COPERCEIVE_RELEASE__: {
  tag_name?: string;
  assets: { name: string; browser_download_url: string }[];
} | null;
