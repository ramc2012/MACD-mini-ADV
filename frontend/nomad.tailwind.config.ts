import type { Config } from "tailwindcss";

// Identical theme tokens to the v1 frontend so any component lifted from
// v1 keeps its visual identity. When the desk-ui primitives are mature
// we can tune the scale here without affecting v1.
const config: Config = {
  important: ".nomad-port",
  corePlugins: {preflight:false},
  content: [
    "./src/nomad/**/*.{ts,tsx}",
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        bg: {
          primary: "rgb(var(--bg-primary) / <alpha-value>)",
          secondary: "rgb(var(--bg-secondary) / <alpha-value>)",
          tertiary: "rgb(var(--bg-tertiary) / <alpha-value>)",
          card: "rgb(var(--bg-card) / <alpha-value>)",
          hover: "rgb(var(--bg-hover) / <alpha-value>)",
          border: "rgb(var(--bg-border) / <alpha-value>)",
          active: "rgb(var(--bg-active) / <alpha-value>)",
        },
        accent: {
          green: "rgb(var(--accent-green) / <alpha-value>)",
          red: "rgb(var(--accent-red) / <alpha-value>)",
          amber: "rgb(var(--accent-amber) / <alpha-value>)",
          blue: "rgb(var(--accent-blue) / <alpha-value>)",
          purple: "rgb(var(--accent-purple) / <alpha-value>)",
          cyan: "rgb(var(--accent-cyan) / <alpha-value>)",
        },
        text: {
          primary: "rgb(var(--text-primary) / <alpha-value>)",
          secondary: "rgb(var(--text-secondary) / <alpha-value>)",
          muted: "rgb(var(--text-muted) / <alpha-value>)",
        },
        paper: "rgb(var(--accent-green) / <alpha-value>)",
        live: "rgb(var(--accent-amber) / <alpha-value>)",
      },
      fontFamily: {
        mono: ["JetBrains Mono", "Fira Code", "monospace"],
        sans: ["Inter", "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};

export default config;
