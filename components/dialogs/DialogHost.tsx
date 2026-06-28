"use client";

import { useAppState } from "@/lib/app-state";
import { SettingsDialog } from "./SettingsDialog";
import { GenerateDialog } from "./GenerateDialog";
import { ProjectKbDialog } from "./ProjectKbDialog";
import { DefectDialog } from "./DefectDialog";
import { UploadDialog } from "./UploadDialog";
import { PackageDialog } from "./PackageDialog";
import { RetrievalDialog } from "./RetrievalDialog";

export function DialogHost() {
  const { dialog, closeDialog } = useAppState();

  switch (dialog) {
    case "settings":
      return <SettingsDialog onClose={closeDialog} />;
    case "generate":
      return <GenerateDialog onClose={closeDialog} />;
    case "kb":
      return <ProjectKbDialog onClose={closeDialog} />;
    case "defects":
      return <DefectDialog onClose={closeDialog} />;
    case "upload":
      return <UploadDialog onClose={closeDialog} />;
    case "package":
      return <PackageDialog onClose={closeDialog} />;
    case "retrieval":
      return <RetrievalDialog onClose={closeDialog} />;
    default:
      return null;
  }
}
