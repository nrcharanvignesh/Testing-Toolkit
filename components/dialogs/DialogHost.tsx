"use client";

import { useAppState } from "@/lib/app-state";
import { SettingsDialog } from "./SettingsDialog";
import { GenerateDialog } from "./GenerateDialog";
import { ProjectKbDialog } from "./ProjectKbDialog";
import { UploadDialog } from "./UploadDialog";
import { PackageDialog } from "./PackageDialog";
import { DefectDialog } from "./DefectDialog";
import { RetrievalDialog } from "./RetrievalDialog";
import { ChatDialog } from "./ChatDialog";
import { CredentialsDialog } from "./CredentialsDialog";
import { AboutDialog } from "./AboutDialog";
import { ViewLogDialog } from "./ViewLogDialog";

export function DialogHost() {
  const { dialog, closeDialog } = useAppState();

  switch (dialog) {
    case "settings":
      return <SettingsDialog onClose={closeDialog} />;
    case "generate":
      return <GenerateDialog onClose={closeDialog} />;
    case "kb":
      return <ProjectKbDialog onClose={closeDialog} />;
    case "upload":
      return <UploadDialog onClose={closeDialog} />;
    case "package":
      return <PackageDialog onClose={closeDialog} />;
    case "defect":
      return <DefectDialog onClose={closeDialog} />;
    case "retrieval":
      return <RetrievalDialog onClose={closeDialog} />;
    case "chat":
      return <ChatDialog onClose={closeDialog} />;
    case "credentials":
      return <CredentialsDialog onClose={closeDialog} />;
    case "about":
      return <AboutDialog onClose={closeDialog} />;
    case "viewlog":
      return <ViewLogDialog onClose={closeDialog} />;
    default:
      return null;
  }
}
