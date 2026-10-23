import { Footer } from "./components/Footer";
import { GetStarted } from "./components/GetStarted";
import { Header } from "./components/Header";
import { Hero } from "./components/Hero";
import { Ledger } from "./components/Ledger";
import { Occlusion } from "./components/Occlusion";
import { Perspectives } from "./components/Perspectives";
import { RealData } from "./components/RealData";
import { Specs } from "./components/Specs";
import { TwoAgents } from "./components/TwoAgents";

export function App() {
  return (
    <div id="top" className="min-h-screen bg-canvas text-primary">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-full focus:bg-primary focus:px-4 focus:py-2 focus:text-black"
      >
        Skip to main content
      </a>
      <Header />
      <main id="main-content">
        <Hero />
        <TwoAgents />
        <Occlusion />
        <Perspectives />
        <Ledger />
        <RealData />
        <Specs />
        <GetStarted />
      </main>
      <Footer />
    </div>
  );
}
