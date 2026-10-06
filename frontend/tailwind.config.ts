import type { Config } from "tailwindcss";

/** DEV-004 W1: tailwind extension bound to tokens.css variables. */
const config: Config = {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        base: "var(--bg-base)",
        raised: "var(--bg-raised)",
        elevated: "var(--bg-elevated)",
        overlay: "var(--bg-overlay)",
        linesubtle: "var(--border-subtle)",
        linedefault: "var(--border-default)",
        ink: "var(--text-primary)",
        inksecondary: "var(--text-secondary)",
        inkmuted: "var(--text-muted)",
        accent: "var(--accent)",
        accenthover: "var(--accent-hover)",
        accentink: "var(--accent-ink)",
        ok: "var(--success)",
        warn: "var(--warning)",
        err: "var(--error)",
      },
      fontSize: {
        meta: "var(--text-meta)",
        metag: "var(--text-meta-lg)",
        body: "var(--text-body)",
        bodysm: "var(--text-body-sm)",
        bodylg: "var(--text-body-lg)",
        h3: "var(--text-h3)",
        h2: "var(--text-h2)",
        h1: "var(--text-h1)",
      },
      borderRadius: {
        sm: "var(--radius-sm)",
        md: "var(--radius-md)",
        lg: "var(--radius-lg)",
      },
      fontFamily: {
        sans: ["var(--font-sans)"],
      },
    },
  },
  plugins: [],
};

export default config;
