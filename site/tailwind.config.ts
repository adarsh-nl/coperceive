import type { Config } from "tailwindcss";
import { tokens } from "./src/tokens";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: tokens.canvas,
        glass: tokens.glass,
        hairline: {
          DEFAULT: tokens.hairline,
          bright: tokens.hairlineBright,
        },
        primary: tokens.text.primary,
        secondary: tokens.text.secondary,
        tertiary: tokens.text.tertiary,
        vehicle: tokens.vehicle,
        "vehicle-alt": tokens.vehicleAlt,
        vehicle2: tokens.vehicle2,
        drone: tokens.drone,
        cursor: tokens.cursor,
        hit: tokens.hit,
        miss: tokens.miss,
        fp: tokens.fp,
        lanes: tokens.lanes,
      },
      fontFamily: {
        sans: [
          '"Geist Variable"',
          "-apple-system",
          "BlinkMacSystemFont",
          '"Segoe UI"',
          "Roboto",
          "sans-serif",
        ],
        mono: [
          '"Geist Mono Variable"',
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Monaco",
          "Consolas",
          "monospace",
        ],
      },
      backdropBlur: {
        "22": "22px",
      },
    },
  },
  plugins: [],
} satisfies Config;
