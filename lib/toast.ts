/**
 * Minimal toast notification — top-center, auto-dismiss.
 * No dependencies, no React context. Call from anywhere.
 */

let container: HTMLElement | null = null;

function getContainer(): HTMLElement {
  if (container && document.body.contains(container)) return container;
  container = document.createElement("div");
  container.id = "tt-toast-container";
  Object.assign(container.style, {
    position: "fixed",
    top: "16px",
    left: "50%",
    transform: "translateX(-50%)",
    zIndex: "9999",
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    gap: "8px",
    pointerEvents: "none",
  });
  document.body.appendChild(container);
  return container;
}

export function showToast(message: string, durationMs: number = 10_000): void {
  const el = document.createElement("div");
  Object.assign(el.style, {
    background: "var(--tt-surface-elevated, #1e293b)",
    color: "var(--tt-text-primary, #f1f5f9)",
    border: "1px solid var(--tt-outline-soft, #334155)",
    borderRadius: "8px",
    padding: "10px 20px",
    fontSize: "13px",
    fontFamily: "inherit",
    boxShadow: "0 4px 12px rgba(0,0,0,0.3)",
    pointerEvents: "auto",
    opacity: "0",
    transition: "opacity 0.3s ease",
    maxWidth: "400px",
    textAlign: "center",
  });
  el.textContent = message;
  getContainer().appendChild(el);

  // Fade in
  requestAnimationFrame(() => { el.style.opacity = "1"; });

  // Auto-dismiss
  setTimeout(() => {
    el.style.opacity = "0";
    setTimeout(() => el.remove(), 300);
  }, durationMs);
}
