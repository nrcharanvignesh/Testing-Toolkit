"use client";

import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  FolderTree,
  LayoutGrid,
  RefreshCw,
  Sparkles,
  Brain,
  Settings,
  type LucideIcon,
} from "lucide-react";

interface TourStep {
  icon: LucideIcon;
  title: string;
  body: string;
}

const STEPS: TourStep[] = [
  {
    icon: Sparkles,
    title: "Welcome to Testing Toolkit",
    body: "Here's a 30-second tour of the app. You can skip it any time — it only shows on first launch.",
  },
  {
    icon: FolderTree,
    title: "Projects",
    body: "The left navigator lists your Azure DevOps projects. Pick one to load its boards. Use Refresh to re-pull the list after changes in ADO.",
  },
  {
    icon: LayoutGrid,
    title: "Boards & work items",
    body: "Selecting a project loads its boards. Click a board to see its work items in the grid, then select items to generate test cases for them.",
  },
  {
    icon: Brain,
    title: "Project knowledge base",
    body: "Upload reference docs to the Project KB (brain icon). The toolkit indexes them and grounds generated test cases in your project's context.",
  },
  {
    icon: RefreshCw,
    title: "Keeping the app fresh",
    body: "The refresh button at the top of the navigator pulls and installs the latest patches, then reloads automatically — no reinstall needed. Heads-up: if you ever see the app reload itself on its own, that's just it updating to the newest version in the background — nothing is wrong.",
  },
  {
    icon: Settings,
    title: "Settings & sections",
    body: "Tune models and credentials in Settings. Every navigator section is collapsible, and your layout is remembered the next time you open the app.",
  },
];

export function GuidedTour({ onDone }: { onDone: () => void }) {
  const [step, setStep] = useState(0);
  const current = STEPS[step];
  const isLast = step === STEPS.length - 1;
  const Icon = current.icon;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-6">
      <motion.div
        initial={{ opacity: 0, scale: 0.97 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.2 }}
        className="tt-dialog w-full max-w-md p-7"
      >
        <div className="flex items-center justify-between">
          <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Quick tour · {step + 1} of {STEPS.length}
          </span>
          <button
            className="text-xs text-muted-foreground hover:text-white"
            onClick={onDone}
          >
            Skip tour
          </button>
        </div>

        <div className="mt-5 flex flex-col items-center text-center">
          <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-primary/10">
            <Icon className="h-7 w-7 text-primary" strokeWidth={1.75} />
          </div>
          <AnimatePresence mode="wait">
            <motion.div
              key={step}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.18 }}
            >
              <h2 className="mt-4 text-lg font-bold tracking-tight text-white text-balance">
                {current.title}
              </h2>
              <p className="mt-2 text-sm leading-relaxed text-muted-foreground text-pretty">
                {current.body}
              </p>
            </motion.div>
          </AnimatePresence>
        </div>

        {/* Progress dots */}
        <div className="mt-6 flex items-center justify-center gap-1.5">
          {STEPS.map((_, i) => (
            <span
              key={i}
              className={`h-1.5 rounded-full transition-all ${
                i === step ? "w-5 bg-primary" : "w-1.5 bg-[var(--tt-outline)]"
              }`}
            />
          ))}
        </div>

        <div className="mt-6 flex items-center justify-between gap-2">
          <button
            className="tt-btn-ghost text-sm disabled:opacity-40"
            onClick={() => setStep((s) => Math.max(0, s - 1))}
            disabled={step === 0}
          >
            Back
          </button>
          <button
            className="tt-btn-success text-sm"
            onClick={() => (isLast ? onDone() : setStep((s) => s + 1))}
          >
            {isLast ? "Get started" : "Next"}
          </button>
        </div>
      </motion.div>
    </div>
  );
}
