"use client";

import { useEffect, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Minus, Square, X } from "lucide-react";

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  footer?: ReactNode;
  width?: number;
}

/**
 * Portaled modal dialog with a desktop OS-style teal title bar (icon + title +
 * window controls), matching the PyQt dialogs in the desktop app.
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
        {/* Teal OS-style title bar */}
        <header className="tt-titlebar flex h-8 shrink-0 items-center justify-between pl-3 pr-1 select-none">
          <div className="flex items-center gap-2">
            <span className="h-3 w-3 rounded-[3px] bg-[#0e7e7e]" aria-hidden />
            <span className="text-[12.5px] font-semibold tracking-tight">
              {title}
            </span>
          </div>
          <div className="flex items-center gap-0.5">
            <span className="tt-titlebar-btn" aria-hidden>
              <Minus className="h-3 w-3" />
            </span>
            <span className="tt-titlebar-btn" aria-hidden>
              <Square className="h-2.5 w-2.5" />
            </span>
            <button
              onClick={onClose}
              aria-label="Close"
              className="tt-titlebar-btn is-close"
            >
              <X className="h-3 w-3" />
            </button>
          </div>
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
