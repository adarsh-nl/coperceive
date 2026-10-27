import { Cluster } from "./components/Cluster";
import { ComingSoon } from "./components/ComingSoon";
import { Footer } from "./components/Footer";
import { Hero } from "./components/Hero";
import { Tagline } from "./components/Tagline";
import { Timeline } from "./components/Timeline";
import { Waitlist } from "./components/Waitlist";

export function App() {
  return (
    <div id="top" className="min-h-screen bg-canvas text-primary">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-full focus:bg-primary focus:px-4 focus:py-2 focus:text-black"
      >
        Skip to main content
      </a>
      <main id="main-content">
        <Hero />
        <Timeline />
        <Tagline />
        <Cluster />
        <ComingSoon />
        <Waitlist />
      </main>
      <Footer />
    </div>
  );
}
