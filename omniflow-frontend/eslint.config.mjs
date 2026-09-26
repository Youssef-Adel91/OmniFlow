import nextVitals from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";

export default [
  ...nextVitals,
  ...nextTypescript,
  {
    rules: {
      "@typescript-eslint/no-unused-vars": ["warn", { argsIgnorePattern: "^_" }],
      "@typescript-eslint/no-explicit-any": "warn",
      "@typescript-eslint/no-empty-object-type": "warn",
      "react/no-unescaped-entities": "off",
      "@next/next/no-img-element": "warn",
      // Existing pages load data in effects; track this performance migration
      // without requiring a rewrite of their loading states during the upgrade.
      "react-hooks/set-state-in-effect": "warn",
    },
  },
  { ignores: [".next/**", "playwright-report/**", "test-results/**"] },
];
