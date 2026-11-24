import type { Config } from "tailwindcss";

const v = (name: string) => `rgb(var(--${name}) / <alpha-value>)`;
const status = (s: string) => ({ fg: v(`${s}-fg`), bg: v(`${s}-bg`), line: v(`${s}-line`), solid: v(`${s}-solid`) });

// Tokens live in src/styles/tokens.css (plan P9.1). This file only maps names.
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: v("canvas"),
        surface: v("surface"),
        subtle: v("subtle"),
        line: { DEFAULT: v("line"), strong: v("line-strong") },
        ink: { DEFAULT: v("ink"), muted: v("ink-muted"), faint: v("ink-faint") },
        primary: { DEFAULT: v("primary"), hover: v("primary-hover"), wash: v("primary-wash"), line: v("primary-line") },
        success: status("success"),
        warning: status("warning"),
        critical: status("critical"),
        info: status("info"),
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
        mono: ['"JetBrains Mono"', "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      fontSize: {
        "headline-lg": ["1.5rem", { lineHeight: "2rem", letterSpacing: "-0.02em", fontWeight: "600" }],
        "headline-md": ["1.25rem", { lineHeight: "1.75rem", letterSpacing: "-0.015em", fontWeight: "600" }],
        "headline-sm": ["1rem", { lineHeight: "1.5rem", letterSpacing: "-0.01em", fontWeight: "600" }],
        "body-lg": ["0.875rem", { lineHeight: "1.375rem" }],
        "body-md": ["0.8125rem", { lineHeight: "1.25rem" }],
        "body-sm": ["0.75rem", { lineHeight: "1.125rem" }],
        "label-lg": ["0.75rem", { lineHeight: "1rem", letterSpacing: "0.02em", fontWeight: "600" }],
        "label-md": ["0.75rem", { lineHeight: "1rem", letterSpacing: "-0.01em" }],
        "label-sm": ["0.6875rem", { lineHeight: "0.875rem" }],
      },
      borderRadius: { sm: "0.25rem", DEFAULT: "0.375rem", md: "0.5rem", lg: "0.75rem" },
      boxShadow: { 1: "var(--shadow-1)", 2: "var(--shadow-2)" },
      spacing: { header: "var(--header-h)", sidebar: "var(--sidebar-w)", "sidebar-collapsed": "var(--sidebar-w-collapsed)" },
      maxWidth: { workbench: "1600px" },
    },
  },
  plugins: [],
} satisfies Config;
