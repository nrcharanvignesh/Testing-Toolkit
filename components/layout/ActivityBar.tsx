"use client";

import {
  Folder,
  LayoutGrid,
  Wrench,
  HelpCircle,
  Settings,
  Brain,
  ChevronRight,
  type LucideIcon,
} from "lucide-react";
import { useAppState } from "@/lib/app-state";
import { Dropdown } from "@/components/ui/dropdown";

function RailButton({
  icon: Icon,
  label,
  onClick,
  disabled,
}: {
  icon: LucideIcon;
  label: string;
  onClick?: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      title={label}
      aria-label={label}
      disabled={disabled}
      className="tt-btn-ghost h-8 w-8 shrink-0 !rounded-lg !border-transparent !p-0 disabled:opacity-40"
    >
      <Icon className="h-[18px] w-[18px]" strokeWidth={2} />
    </button>
  );
}

export function ActivityBar() {
  const {
    setNavVisible,
    openDialog,
    currentProject,
    setLogVisible,
  } = useAppState();

  return (
    <div className="tt-rail flex w-11 shrink-0 flex-col items-center gap-1 py-2">
      <RailButton icon={Folder} label="Projects" onClick={() => setNavVisible(true)} />
      <RailButton icon={LayoutGrid} label="Boards" onClick={() => setNavVisible(true)} />

      <div className="flex-1" />

      <Dropdown
        align="left"
        items={[
          { label: "Bulk Defects...", onClick: () => openDialog("defects") },
          { label: "Retrieval preview...", onClick: () => openDialog("retrieval") },
        ]}
        trigger={({ toggle, ref }) => (
          <button
            ref={ref}
            onClick={toggle}
            title="Tools"
            aria-label="Tools"
            className="tt-btn-ghost h-8 w-8 shrink-0 !rounded-lg !border-transparent !p-0"
          >
            <Wrench className="h-[18px] w-[18px]" strokeWidth={2} />
          </button>
        )}
      />
      <Dropdown
        align="left"
        items={[
          { label: "View log", onClick: () => setLogVisible(true) },
          { label: "About", separatorBefore: true, onClick: () => openDialog("settings") },
        ]}
        trigger={({ toggle, ref }) => (
          <button
            ref={ref}
            onClick={toggle}
            title="Help"
            aria-label="Help"
            className="tt-btn-ghost h-8 w-8 shrink-0 !rounded-lg !border-transparent !p-0"
          >
            <HelpCircle className="h-[18px] w-[18px]" strokeWidth={2} />
          </button>
        )}
      />
      <RailButton icon={Settings} label="Settings" onClick={() => openDialog("settings")} />
      <RailButton
        icon={Brain}
        label="Project KB"
        onClick={() => openDialog("kb")}
        disabled={!currentProject}
      />
      <RailButton
        icon={ChevronRight}
        label="Show navigator"
        onClick={() => setNavVisible(true)}
      />
    </div>
  );
}
