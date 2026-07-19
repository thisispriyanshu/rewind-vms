import React, { useEffect } from "react";

export default function Toast({ toast, onDone }) {
  useEffect(() => {
    if (!toast) return undefined;
    const t = setTimeout(onDone, 5000);
    return () => clearTimeout(t);
  }, [toast, onDone]);

  if (!toast) return null;
  return <div className={`toast ${toast.kind || ""}`}>{toast.text}</div>;
}
