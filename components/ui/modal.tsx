"use client";

import { useEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";

const FOCUSABLE =
  'a[href],button:not([disabled]),textarea:not([disabled]),input:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])';

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
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    // Remember what had focus so we can restore it when the dialog closes
    // (WCAG 2.4.3). Move initial focus into the dialog on open.
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const dialog = dialogRef.current;
    const focusFirst = () => {
      const focusables = dialog?.querySelectorAll<HTMLElement>(FOCUSABLE);
      (focusables && focusables.length > 0 ? focusables[0] : dialog)?.focus();
    };
    // Defer to after paint so the portal content exists.
    const raf = requestAnimationFrame(focusFirst);

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
        return;
      }
      // Trap Tab within the dialog so keyboard focus cannot reach the inert
      // background behind the modal.
      if (e.key === "Tab" && dialog) {
        const nodes = Array.from(
          dialog.querySelectorAll<HTMLElement>(FOCUSABLE)
        ).filter((el) => el.offsetParent !== null || el === document.activeElement);
        if (nodes.length === 0) {
          e.preventDefault();
          dialog.focus();
          return;
        }
        const first = nodes[0];
        const last = nodes[nodes.length - 1];
        const active = document.activeElement as HTMLElement | null;
        if (e.shiftKey && (active === first || !dialog.contains(active))) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && active === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    window.addEventListener("keydown", onKey);
    // Lock background scroll while the dialog is open.
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
      // Restore focus to the trigger element.
      previouslyFocused?.focus?.();
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
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        className="tt-dialog tt-dialog-enter flex max-h-[90vh] w-full flex-col overflow-hidden shadow-2xl outline-none"
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
