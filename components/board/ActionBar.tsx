"use client";

import { TC_TYPES, TC_BUTTON_LABEL } from "@/lib/agent-client";
import { useAppState } from "@/lib/app-state";
import { COLOR_MUTED } from "@/lib/board-utils";

export function ActionBar() {
  const {
    selected,
    currentProject,
    openDialog,
    setGenerateCtx,
    logVisible,
    setLogVisible,
  } = useAppState();

  const count = selected.size;
  const hasSelection = count > 0;

  return (
    <div className="tt-card flex items-center gap-2 px-3 py-2">
      {TC_TYPES.map((t) => (
        <button
          key={t}
          className="tt-btn-success !px-4 !py-1.5 text-sm"
          disabled={!hasSelection}
          title={`Generate ${TC_BUTTON_LABEL[t]} test cases for the ticked work items`}
          onClick={() => {
            setGenerateCtx({ tcType: t });
            openDialog("generate");
          }}
        >
          {TC_BUTTON_LABEL[t]}
        </button>
      ))}

      <button
        className="tt-btn-primary !px-4 !py-1.5 text-sm"
        title="Bundle the ticked work items into PDFs, or open the PDF Packager if no items are selected"
        onClick={() => openDialog("package")}
      >
        Package PDFs
      </button>
      <button
        className="tt-btn-primary !px-4 !py-1.5 text-sm"
        disabled={!currentProject}
        title="Push reviewed test cases to ADO"
        onClick={() => openDialog("upload")}
      >
        Upload to ADO
      </button>

      <div className="flex-1" />

      <span
        className="text-xs"
        style={{
          color: count ? "var(--tt-success)" : COLOR_MUTED,
          fontWeight: count ? 600 : 400,
        }}
      >
        {count ? `${count} work item(s) selected` : "No work items selected"}
      </span>

      <button
        className="tt-btn-ghost !px-3 !py-1.5 text-xs"
        onClick={() => setLogVisible(!logVisible)}
      >
        {logVisible ? "Hide" : "Show"}
      </button>
    </div>
  );
}
