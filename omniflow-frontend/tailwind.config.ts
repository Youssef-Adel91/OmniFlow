import type { Config } from "tailwindcss";
import plugin from "tailwindcss/plugin";

const config: Config = {
  // Scan all source files for class usage
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],

  theme: {
    extend: {
      // ── OmniFlow Luxury Palette ──────────────────────────────────────────
      colors: {
        // Primary dark — Deep Navy (backgrounds, sidebar)
        navy: {
          50:  "#eef1f6",
          100: "#d4dbe8",
          200: "#a9b7d1",
          300: "#7e93ba",
          400: "#5370a3",
          500: "#2d4d8c",
          600: "#243d70",
          700: "#1a2d54",
          800: "#1a2332",  // ← PRIMARY (sidebar, header)
          900: "#0f141d",
          950: "#080c12",
        },
        // Accent — Royal Gold (CTA buttons, highlights, active states)
        gold: {
          50:  "#fdf9ed",
          100: "#f8f0d0",
          200: "#f1de99",
          300: "#e9c962",
          400: "#e2b53b",
          500: "#c9a961",  // ← PRIMARY GOLD
          600: "#b8942a",
          700: "#9a7920",
          800: "#7d601e",
          900: "#65501d",
        },
        // Background — Warm Cream / Champagne (reduces eye strain)
        cream: {
          50:  "#ffffff",
          100: "#fdfcf8",
          200: "#faf7f0",  // ← PRIMARY BACKGROUND
          300: "#f5efdf",
          400: "#ede3c8",
          500: "#e0d1a8",
          600: "#c9b87a",
          700: "#a99250",
          800: "#7d6b35",
          900: "#524625",
        },
        // Semantic colors
        success: { DEFAULT: "#16a34a", light: "#dcfce7", dark: "#14532d" },
        warning: { DEFAULT: "#ca8a04", light: "#fef9c3", dark: "#713f12" },
        danger:  { DEFAULT: "#dc2626", light: "#fee2e2", dark: "#7f1d1d" },
        info:    { DEFAULT: "#0891b2", light: "#cffafe", dark: "#164e63" },
      },

      // ── Typography ────────────────────────────────────────────────────────
      fontFamily: {
        // Latin (dashboard headings, English UI)
        sans:    ["var(--font-inter)", "system-ui", "sans-serif"],
        // Arabic (RTL layouts, bilingual labels)
        arabic:  ["var(--font-noto-kufi)", "var(--font-inter)", "sans-serif"],
        // Monospace (code snippets, IDs)
        mono:    ["var(--font-geist-mono)", "monospace"],
      },

      fontSize: {
        "2xs": ["0.625rem",  { lineHeight: "1rem"  }],
        xs:    ["0.75rem",   { lineHeight: "1rem"  }],
        sm:    ["0.875rem",  { lineHeight: "1.25rem" }],
        base:  ["1rem",      { lineHeight: "1.5rem" }],
        lg:    ["1.125rem",  { lineHeight: "1.75rem" }],
        xl:    ["1.25rem",   { lineHeight: "1.75rem" }],
        "2xl": ["1.5rem",    { lineHeight: "2rem"  }],
        "3xl": ["1.875rem",  { lineHeight: "2.25rem" }],
        "4xl": ["2.25rem",   { lineHeight: "2.5rem" }],
      },

      // ── Spacing & Layout ──────────────────────────────────────────────────
      spacing: {
        "sidebar-w":    "16rem",   // 256px sidebar width (expanded)
        "sidebar-mini": "4.5rem",  // 72px sidebar width (collapsed)
        "header-h":     "4rem",    // 64px header height
      },

      // ── Border Radius ─────────────────────────────────────────────────────
      borderRadius: {
        "4xl": "2rem",
        "5xl": "2.5rem",
      },

      // ── Box Shadow ────────────────────────────────────────────────────────
      boxShadow: {
        // Subtle lift for cards
        "card":    "0 1px 3px rgba(26,35,50,0.08), 0 1px 2px rgba(26,35,50,0.06)",
        "card-md": "0 4px 6px rgba(26,35,50,0.08), 0 2px 4px rgba(26,35,50,0.06)",
        "card-lg": "0 10px 15px rgba(26,35,50,0.10), 0 4px 6px rgba(26,35,50,0.08)",
        // Gold glow for primary CTA
        "gold":    "0 0 0 3px rgba(201,169,97,0.30)",
        // Navy glow for focus rings
        "focus":   "0 0 0 3px rgba(26,35,50,0.20)",
      },

      // ── Keyframes & Animations ────────────────────────────────────────────
      keyframes: {
        "fade-in": {
          "0%":   { opacity: "0", transform: "translateY(8px)"  },
          "100%": { opacity: "1", transform: "translateY(0)"    },
        },
        "slide-in-right": {
          "0%":   { opacity: "0", transform: "translateX(16px)" },
          "100%": { opacity: "1", transform: "translateX(0)"    },
        },
        "slide-in-left": {
          "0%":   { opacity: "0", transform: "translateX(-16px)"},
          "100%": { opacity: "1", transform: "translateX(0)"    },
        },
        "pulse-gold": {
          "0%, 100%": { boxShadow: "0 0 0 0 rgba(201,169,97,0.4)"  },
          "50%":      { boxShadow: "0 0 0 8px rgba(201,169,97,0.0)" },
        },
        "shimmer": {
          "0%":   { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition:  "200% 0" },
        },
      },
      animation: {
        "fade-in":         "fade-in 0.25s ease-out both",
        "slide-in-right":  "slide-in-right 0.2s ease-out both",
        "slide-in-left":   "slide-in-left 0.2s ease-out both",
        "pulse-gold":      "pulse-gold 2s ease-in-out infinite",
        "shimmer":         "shimmer 2s linear infinite",
      },

      // ── CSS variable bridge ───────────────────────────────────────────────
      // Allows using `bg-background`, `text-foreground` etc. in Shadcn style
      backgroundColor: {
        background: "var(--background)",
        card:       "var(--card)",
        sidebar:    "var(--sidebar)",
      },
      textColor: {
        foreground:       "var(--foreground)",
        "muted-foreground": "var(--muted-foreground)",
        "card-foreground": "var(--card-foreground)",
      },
      borderColor: {
        border: "var(--border)",
        input:  "var(--input)",
      },
    },
  },

  plugins: [
    // RTL variant — generates rtl: prefix for all utilities
    // Usage: <div className="mr-4 rtl:ml-4 rtl:mr-0">
    plugin(({ addVariant }: { addVariant: Function }) => {
      addVariant("rtl", '[dir="rtl"] &');
      addVariant("ltr", '[dir="ltr"] &');
    }),
  ],
};

export default config;
