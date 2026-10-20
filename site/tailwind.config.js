/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        canvas: {
          DEFAULT: "#0a0d14",
          subtle: "#0e131d",
          muted: "#131926",
        },
        surface: {
          DEFAULT: "#151c2a",
          hover: "#1b2336",
          active: "#212b42",
        },
        border: {
          DEFAULT: "#232e42",
          muted: "#1a2233",
          bright: "#3b4a6b",
        },
        agent: {
          vehicle: "#22d3ee", // cyan
          drone: "#c084fc",   // violet
          lane: "#7dcea0",    // mint green
          road: "#c5d0d8",    // silver slate
          alert: "#f87171",   // coral red
        },
      },
      fontFamily: {
        sans: ['-apple-system', 'BlinkMacSystemFont', '"Segoe UI"', 'Roboto', 'Helvetica', 'Arial', 'sans-serif'],
        mono: ['"IBM Plex Mono"', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'Monaco', 'Consolas', 'monospace'],
      },
    },
  },
  plugins: [],
}
