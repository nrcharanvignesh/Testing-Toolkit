"use client";

import { useEffect, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: string;
  subtitle?: string;
  children: ReactNode;
  footer?: ReactNode;
  width?: number;
}

/**
 * Portaled modal dialog.
 *
 * Intentionally framer-motion-free. A motion/AnimatePresence overlay rendered
 * through a portal gets promoted to its own compositor layer (will-change) and
 * can fail to paint until a reflow is forced — the overlay stays in the DOM,
 * hit-testable and opacity:1, but never composites. A plain mounted overlay
 * paints reliably, matches the desktop (Qt dialogs appear instantly), and is
 * cheaper to render.
 */
export function Modal({
  open,
  onClose,
  title,
  subtitle,
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
        <header className="flex items-start justify-between gap-4 border-b border-[#1e2128] px-6 py-4">
          <div className="flex flex-col gap-0.5">
            <h2 className="text-base font-bold tracking-tight text-white">
              {title}
            </h2>
            {subtitle && (
              <p className="text-xs text-muted-foreground">{subtitle}</p>
            )}
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            className="tt-btn-ghost -mr-2 -mt-1 h-8 w-8 shrink-0 !p-0"
          >
            <X className="h-4 w-4" />
          </button>
        </header>
        <div className="flex-1 overflow-auto px-6 py-5">{children}</div>
        {footer && (
          <footer className="flex items-center justify-end gap-2 border-t border-[#1e2128] px-6 py-4">
            {footer}
          </footer>
        )}
      </div>
    </div>,
    document.body
  );
}
