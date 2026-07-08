"use client";

import { useEffect, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  footer?: ReactNode;
  width?: number;
}

/**
 * Portaled modal dialog with a clean header (title + close button).
 *
 * Intentionally framer-motion-free. A motion/AnimatePresence overlay rendered
 * through a portal gets promoted to its own compositor layer (will-change) and
 * can fail to paint until a reflow is forced — the overlay stays in the DOM,
 * hit-testable and opacity:1, but never composites. A plain mounted overlay
 * paints reliably (dialogs appear instantly) and is cheaper to render.
 */
export function Modal({
  open,
  onClose,
  title,
  children,
  footer,
  width = 720,
}: ModalProps) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    // Lock background scroll while the dialog is open.
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [open, onClose]);

  if (typeof document === "undefined" || !open) return null;

  return createPortal(
    <div
      className="tt-overlay fixed inset-0 z-50 flex items-center justify-center p-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="tt-dialog tt-dialog-enter flex max-h-[90vh] w-full flex-col overflow-hidden shadow-2xl"
        style={{ maxWidth: width }}
      >
        {/* Dialog header (title + close) */}
        <header className="flex shrink-0 items-center justify-between border-b border-[var(--tt-outline-soft)] px-6 py-3.5 select-none">
          <h2 className="text-[14px] font-semibold tracking-tight text-[var(--tt-text-primary)]">
            {title}
          </h2>
          <button
            onClick={onClose}
            aria-label="Close"
            className="flex h-7 w-7 items-center justify-center rounded-md text-[var(--tt-text-muted)] transition-colors hover:bg-[var(--tt-outline-soft)] hover:text-[var(--tt-text-primary)]"
          >
            <X className="h-4 w-4" />
          </button>
        </header>
        <div className="flex-1 overflow-auto px-6 py-5">{children}</div>
        {footer && (
          <footer className="flex items-center justify-end gap-2 border-t border-[var(--tt-outline-soft)] px-6 py-4">
            {footer}
          </footer>
        )}
      </div>
    </div>,
    document.body
  );
}
