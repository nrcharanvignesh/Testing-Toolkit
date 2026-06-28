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
}

export function Dropdown({ trigger, items, align = "left" }: DropdownProps) {
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
          className={`tt-dialog absolute z-50 mt-1 min-w-[180px] overflow-hidden p-1.5 shadow-2xl ${
            align === "right" ? "right-0" : "left-0"
          }`}
        >
          {items.map((item, i) => (
            <div key={item.label}>
              {item.separatorBefore && i > 0 && (
                <div className="my-1 h-px bg-[#1e2128]" />
              )}
              <button
                onClick={() => {
                  setOpen(false);
                  item.onClick();
                }}
                className="block w-full rounded-md px-3 py-1.5 text-left text-sm text-[#bfc4cc] transition-colors hover:bg-[#0071e3] hover:text-white"
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
