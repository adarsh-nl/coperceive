/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        canvas: "#000000",
        glass: "rgb(28 28 30 / 0.78)",
        hairline: {
          DEFAULT: "rgba(255, 255, 255, 0.08)",
          bright: "rgba(255, 255, 255, 0.14)",
        },
        primary: "#f5f5f7",
        secondary: "#a1a1a6",
        tertiary: "#86868b",
        vehicle: "#e67828",
        uav: "#2878dc",
        infrastructure: "#b7b1a6",
        lanes: "#7dcea0",
        ok: "#5dd39e",
        warn: "#f0b64a",
        bad: "#ff6259",
      },
      fontFamily: {
        sans: ['"Geist Variable"', '-apple-system', 'BlinkMacSystemFont', '"Segoe UI"', 'Roboto', 'sans-serif'],
        mono: ['"Geist Mono Variable"', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'Monaco', 'Consolas', 'monospace'],
      },
      backdropBlur: {
        '22': '22px',
      },
    },
  },
  plugins: [],
}
