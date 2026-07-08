"use client";

/**
 * ResizeHandle
 * A thin draggable divider for free-hand resizing of a layout region.
 *
 * The handle reports the new size live via `onChange` (for smooth dragging,
 * the consumer keeps the value in local state) and once more via `onCommit`
 * on pointer up (the consumer persists it to preferences then). Pointer
 * capture keeps the drag alive even if the cursor outruns the 1.5px hit area.
 */

import { useRef, type PointerEvent as ReactPointerEvent } from "react";

export function ResizeHandle({
  axis,
  value,
  min = 120,
  max = 4000,
  invert = false,
  onChange,
  onCommit,
  ariaLabel,
  className = "",
}: {
  /** "x" = drag horizontally to change a width; "y" = vertically for a height. */
  axis: "x" | "y";
  /** current size in px */
  value: number;
  min?: number;
  max?: number;
  /** when true, dragging toward the start (left/up) increases the size */
  invert?: boolean;
  onChange: (px: number) => void;
  onCommit: (px: number) => void;
  ariaLabel?: string;
  className?: string;
}) {
  const start = useRef<{ pos: number; size: number } | null>(null);
  const latest = useRef(value);

  function clamp(px: number) {
    return Math.min(max, Math.max(min, px));
  }

  function onPointerDown(e: ReactPointerEvent<HTMLDivElement>) {
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    start.current = { pos: axis === "x" ? e.clientX : e.clientY, size: value };
    latest.current = value;
  }

  function onPointerMove(e: ReactPointerEvent<HTMLDivElement>) {
    if (!start.current) return;
    const cur = axis === "x" ? e.clientX : e.clientY;
    let delta = cur - start.current.pos;
    if (invert) delta = -delta;
    const next = clamp(start.current.size + delta);
    latest.current = next;
    onChange(next);
  }

  function endDrag(e: ReactPointerEvent<HTMLDivElement>) {
    if (!start.current) return;
    start.current = null;
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {
      /* capture may already be released */
    }
    onCommit(latest.current);
  }

  const base =
    axis === "x"
      ? "w-1.5 cursor-col-resize self-stretch"
      : "h-1.5 cursor-row-resize w-full";

  return (
    <div
      role="separator"
      aria-orientation={axis === "x" ? "vertical" : "horizontal"}
      aria-label={ariaLabel}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      className={`shrink-0 rounded-full bg-transparent transition-colors hover:bg-[var(--tt-info)]/50 ${base} ${className}`}
    />
  );
}
