import type { Config } from "tailwindcss";

// Arivu visual language — warm dark, gold accent, dialect-coded color
// pills. Matches arivu.live homepage.
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        warmdark: "#0E0D0B",
        warmsoft: "#1A1814",
        gold: "#E8A33D",
        terracotta: "#C25E3F",
        sage: "#7C9A6A",
        muted: "#8A8378",
      },
      fontFamily: {
        display: ["Fraunces", "serif"],
        sans: ["'Instrument Sans'", "system-ui", "sans-serif"],
        mono: ["'JetBrains Mono'", "ui-monospace", "monospace"],
        kn: ["'Noto Sans Kannada'", "sans-serif"],
      },
    },
  },
  plugins: [],
};
export default config;
