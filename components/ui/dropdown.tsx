"use client";

import {
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

export interface MenuItem {
  label: string;
  onClick: () => void;
  separatorBefore?: boolean;
}

interface DropdownProps {
  trigger: (props: {
    open: boolean;
    toggle: () => void;
    ref: React.RefObject<HTMLButtonElement | null>;
  }) => ReactNode;
  items: MenuItem[];
  align?: "left" | "right";
  direction?: "down" | "up";
}

export function Dropdown({
  trigger,
  items,
  align = "left",
  direction = "down",
}: DropdownProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLButtonElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (
        menuRef.current?.contains(e.target as Node) ||
        ref.current?.contains(e.target as Node)
      )
        return;
      setOpen(false);
    };
    window.addEventListener("mousedown", onDown);
    return () => window.removeEventListener("mousedown", onDown);
  }, [open]);

  return (
    <div className="relative">
      {trigger({ open, toggle: () => setOpen((o) => !o), ref })}
      {open && (
        <div
          ref={menuRef}
          className={`tt-dialog absolute z-50 min-w-[180px] overflow-hidden p-1.5 shadow-2xl ${
            align === "right" ? "right-0" : "left-0"
          } ${direction === "up" ? "bottom-full mb-1" : "top-full mt-1"}`}
        >
          {items.map((item, i) => (
            <div key={item.label}>
              {item.separatorBefore && i > 0 && (
                <div className="my-1 h-px bg-[var(--tt-outline-soft)]" />
              )}
              <button
                onClick={() => {
                  setOpen(false);
                  item.onClick();
                }}
                className="block w-full rounded-md px-3 py-1.5 text-left text-sm text-[var(--tt-text-secondary)] transition-colors hover:bg-[var(--tt-action)] hover:text-white"
              >
                {item.label}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
