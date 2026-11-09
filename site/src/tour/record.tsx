import React from "react";
import ReactDOM from "react-dom/client";
import "../index.css";
import { ProductTour } from "./ProductTour.tsx";

const raw = new URLSearchParams(window.location.search).get("chapter");
const parsed = raw == null ? undefined : Number(raw);
const chapter = parsed != null && Number.isFinite(parsed) ? parsed : undefined;

const root = document.getElementById("root");
if (!root) throw new Error("Root element not found");

ReactDOM.createRoot(root).render(
  <React.StrictMode>
    <ProductTour chapter={chapter} controls={false} autoplay record />
  </React.StrictMode>,
);
