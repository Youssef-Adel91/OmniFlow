"use client";

/**
 * Shared native <select> wrapper -- the one dropdown used across the app.
 *
 * Uses theme tokens (--menu-bg / --foreground / --border) so the closed
 * control and the opened option list are readable in light AND dark mode
 * (never a white box with light text). Native <select> keeps full keyboard
 * and screen-reader support; the chevron is decorative. Caller `style` /
 * `className` still win, so pages with their own look keep it.
 */
import { forwardRef, type SelectHTMLAttributes } from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

export type SelectProps = SelectHTMLAttributes<HTMLSelectElement>;

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { className, style, children, ...rest },
  ref,
) {
  return (
    <span className="relative inline-flex items-center">
      <select
        ref={ref}
        className={cn(
          "appearance-none rounded-[10px] border border-[var(--border)] bg-[var(--menu-bg)]",
          "text-[var(--foreground)] text-sm ps-3 pe-8 py-2 cursor-pointer",
          "hover:bg-[var(--menu-hover)] transition-colors duration-150",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]",
          "disabled:opacity-60 disabled:cursor-not-allowed",
          className,
        )}
        style={{ paddingInlineEnd: "2rem", ...style }}
        {...rest}
      >
        {children}
      </select>
      <ChevronDown
        aria-hidden
        className="pointer-events-none absolute end-2.5 w-4 h-4 text-[var(--muted-foreground)]"
      />
    </span>
  );
});
