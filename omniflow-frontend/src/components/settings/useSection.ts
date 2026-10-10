"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { sameJson } from "@/lib/api/settings";

/**
 * Draft/saved state for one settings section: tracks "dirty", reports it to the shell (unsaved-changes guard),
 * and wraps the save call with saving/error/saved flags.
 */
export function useSection<T>(saved: T | null, save: (draft: T) => Promise<T>, onDirty: (dirty: boolean) => void, errorText: (e: unknown) => string) {
  const [draft, setDraft] = useState<T | null>(saved);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const savedRef = useRef(saved);

  useEffect(() => { savedRef.current = saved; setDraft(saved); }, [saved]);

  const dirty = useMemo(() => draft !== null && saved !== null && !sameJson(draft, saved), [draft, saved]);
  useEffect(() => { onDirty(dirty); }, [dirty, onDirty]);
  useEffect(() => () => onDirty(false), [onDirty]);

  const submit = useCallback(async () => {
    if (draft === null) return;
    setSaving(true); setError(null);
    try {
      const out = await save(draft);
      savedRef.current = out;
      setDraft(out);
      setSavedAt(Date.now());
    } catch (e) {
      setError(errorText(e));
    } finally {
      setSaving(false);
    }
  }, [draft, save, errorText]);

  const reset = useCallback(() => { setDraft(savedRef.current); setError(null); }, []);
  return { draft, setDraft, dirty, saving, error, savedAt, submit, reset };
}
