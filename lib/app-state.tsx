"use client";

/**
 * app-state.tsx
 * Central UI state mirroring the desktop MainWindow: project/board selection,
 * the loaded board view, the selection set, the log/progress panel feed, KB
 * indexing status, and nav/log panel visibility.
 */

import {
  createContext,
  useContext,
  useCallback,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  agent,
  agentLogLevel,
  displayProjectName,
  type Board,
  type BoardView,
  type SettingsResponse,
} from "./agent-client";
import {
  getPreferences,
  setPanelPref,
  setPendingReindexPref,
} from "./preferences";

export type KbState = "none" | "indexing" | "ready" | "error";
export type DialogId =
  | "settings"
  | "generate"
  | "kb"
  | "upload"
  | "package"
  | "about"
  | "viewlog"
  | null;

export interface LogLine {
  id: number;
  level: "INFO" | "SUCCESS" | "WARN" | "ERROR";
  text: string;
  ts: number;
}

interface GenerateContext {
  tcType: "" | "implementation" | "sit" | "uat";
  xlsxPath?: string;
}

interface AppStateValue {
  // settings
  settings: SettingsResponse | null;
  setSettings: (s: SettingsResponse | null) => void;
  prefix: string;

  // projects
  projects: string[];
  projectsLoading: boolean;
  currentProject: string;
  reloadProjects: () => Promise<void>;
  selectProject: (full: string) => void;
  displayName: (full: string) => string;

  // boards
  boards: Board[];
  boardsLoading: boolean;
  currentBoard: Board | null;
  reloadBoards: () => Promise<void>;
  selectBoard: (b: Board) => void;

  // board view
  boardView: BoardView | null;
  boardLoading: boolean;

  // selection
  selected: Set<number>;
  setSelected: (s: Set<number>) => void;
  toggleSelected: (id: number, on: boolean) => void;

  // log panel
  log: LogLine[];
  pushLog: (level: LogLine["level"], text: string) => void;
  clearLog: () => void;
  logVisible: boolean;
  setLogVisible: (v: boolean) => void;

  // kb
  kbState: KbState;
  kbMessage: string;
  /** 0..1 index progress for the global bar, or null when indeterminate. */
  kbProgress: number | null;
  /** True when docs were added/removed since the last index (needs reindex). */
  kbDirty: boolean;
  /** Flag the current project's KB as needing a (re)index. */
  markKbDirty: () => void;
  /** Clear the dirty flag (e.g. after a manual rebuild completes). */
  clearKbDirty: () => void;
  /** Index a project's KB at app level so it survives dialogs closing. */
  indexKb: (project: string) => Promise<void>;
  /** Re-index every project's KB sequentially (used after a reinstall). */
  reindexAllKbs: () => Promise<void>;

  // nav
  navVisible: boolean;
  setNavVisible: (v: boolean) => void;

  // dialogs
  dialog: DialogId;
  openDialog: (d: DialogId) => void;
  closeDialog: () => void;
  generateCtx: GenerateContext;
  setGenerateCtx: (c: GenerateContext) => void;
}

const AppStateContext = createContext<AppStateValue | null>(null);

let _logId = 0;

export function AppStateProvider({
  initialSettings,
  children,
}: {
  initialSettings: SettingsResponse | null;
  children: ReactNode;
}) {
  const [settings, setSettings] = useState<SettingsResponse | null>(
    initialSettings
  );
  const [projects, setProjects] = useState<string[]>([]);
  const [projectsLoading, setProjectsLoading] = useState(false);
  const [currentProject, setCurrentProject] = useState("");

  const [boards, setBoards] = useState<Board[]>([]);
  const [boardsLoading, setBoardsLoading] = useState(false);
  const [currentBoard, setCurrentBoard] = useState<Board | null>(null);

  const [boardView, setBoardView] = useState<BoardView | null>(null);
  const [boardLoading, setBoardLoading] = useState(false);

  const [selected, setSelected] = useState<Set<number>>(new Set());

  const [log, setLog] = useState<LogLine[]>([]);
  // Visibility defaults come from saved preferences (first launch hides all).
  const [logVisible, setLogVisibleState] = useState<boolean>(
    () => getPreferences().panels.log
  );
  const setLogVisible = useCallback((v: boolean) => {
    setLogVisibleState(v);
    setPanelPref("log", v);
  }, []);

  const [kbState, setKbState] = useState<KbState>("none");
  const [kbMessage, setKbMessage] = useState("KB: no project selected");
  const [kbProgress, setKbProgress] = useState<number | null>(null);
  const [kbDirty, setKbDirty] = useState(false);

  const markKbDirty = useCallback(() => setKbDirty(true), []);
  const clearKbDirty = useCallback(() => setKbDirty(false), []);

  // Guard against overlapping index passes. If a new index is requested while
  // one is running (e.g. more files uploaded mid-index), we don't start a
  // second concurrent pass — we flag a rerun so exactly one more pass runs
  // afterwards to pick up the newly added documents.
  const indexingRef = useRef(false);
  const rerunIndexRef = useRef(false);

  const [navVisible, setNavVisibleState] = useState<boolean>(
    () => getPreferences().panels.nav
  );
  const setNavVisible = useCallback((v: boolean) => {
    setNavVisibleState(v);
    setPanelPref("nav", v);
  }, []);

  const [dialog, setDialog] = useState<DialogId>(null);
  const [generateCtx, setGenerateCtx] = useState<GenerateContext>({
    tcType: "",
  });

  const prefix = settings?.project_prefix ?? "";

  const pushLog = useCallback((level: LogLine["level"], text: string) => {
    setLog((prev) => {
      const next = [
        ...prev,
        { id: ++_logId, level, text, ts: Date.now() },
      ];
      // keep last 500 lines
      return next.length > 500 ? next.slice(next.length - 500) : next;
    });
  }, []);

  const clearLog = useCallback(() => setLog([]), []);

  const displayName = useCallback(
    (full: string) => displayProjectName(full, prefix),
    [prefix]
  );

  const reloadProjects = useCallback(async () => {
    setProjectsLoading(true);
    // Log panel stays hidden by default; the user opens it via Show / the rail.
    pushLog("INFO", "Loading projects...");
    try {
      const names = await agent.listProjects();
      const sorted = [...names].sort((a, b) =>
        a.toLowerCase().localeCompare(b.toLowerCase())
      );
      setProjects(sorted);
      pushLog("SUCCESS", `Loaded ${sorted.length} project(s).`);
    } catch (e) {
      pushLog("ERROR", `Load projects failed: ${(e as Error).message}`);
    } finally {
      setProjectsLoading(false);
    }
  }, [pushLog]);

  const reloadBoards = useCallback(
    async (projectOverride?: string) => {
      const project = projectOverride ?? currentProject;
      if (!project) return;
      setBoardsLoading(true);
      pushLog("INFO", "Loading boards...");
      try {
        const all = await agent.listBoards(project);
        const stories = all.filter((b) =>
          (b.name || "").toLowerCase().includes("stories")
        );
        const flat = stories.length ? stories : all;
        const seen = new Set<string>();
        const deduped: Board[] = [];
        for (const b of flat) {
          if (stories.length) {
            if (seen.has(b.team_name)) continue;
            seen.add(b.team_name);
          }
          deduped.push(b);
        }
        setBoards(deduped);
        pushLog("SUCCESS", `${deduped.length} board(s) loaded.`);
        if (deduped.length) {
          const storiesBoard =
            deduped.find((b) =>
              (b.name || "").toLowerCase().includes("stories")
            ) ?? deduped[0];
          selectBoardInternal(storiesBoard, project);
        }
      } catch (e) {
        pushLog("ERROR", `Load boards failed: ${(e as Error).message}`);
      } finally {
        setBoardsLoading(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [currentProject, pushLog]
  );

  const loadBoardView = useCallback(
    async (project: string, board: Board) => {
      setBoardLoading(true);
      setBoardView(null);
      setSelected(new Set());
      pushLog("INFO", `Loading board '${board.label}'...`);
      try {
        const view = await agent.boardView(project, board);
        setBoardView(view);
        pushLog(
          "SUCCESS",
          `${view.rows.length} work item(s) in ${view.columns.length} column(s).`
        );
      } catch (e) {
        pushLog("ERROR", `Load board failed: ${(e as Error).message}`);
      } finally {
        setBoardLoading(false);
      }
    },
    [pushLog]
  );

  const selectBoardInternal = useCallback(
    (b: Board, project: string) => {
      setCurrentBoard(b);
      loadBoardView(project, b);
    },
    [loadBoardView]
  );

  const selectBoard = useCallback(
    (b: Board) => {
      if (currentProject) selectBoardInternal(b, currentProject);
    },
    [currentProject, selectBoardInternal]
  );

  const runKbIndexOnce = useCallback(
    async (project: string) => {
      setKbState("indexing");
      setKbMessage("KB indexing... starting");
      setKbProgress(null);
      const start = Date.now();
      const fmtDuration = (secs: number) => {
        const s = Math.max(0, Math.floor(secs));
        if (s < 60) return `${s}s`;
        const m = Math.floor(s / 60);
        return `${m}m ${String(s % 60).padStart(2, "0")}s`;
      };
      try {
        const status = await agent.kbStatus(project);
        if (!status.documents || status.documents.length === 0) {
          setKbState("none");
          setKbMessage("KB: no files uploaded");
          setKbProgress(null);
          return;
        }
        const res = await agent.kbIndex(project, {
          onLog: (line) => pushLog(agentLogLevel(line), line),
          onProgress: (p) => {
            const { current: done, total, stage } = p;
            if (!total || total <= 0) {
              setKbMessage("KB indexing... scanning");
              setKbProgress(null);
              return;
            }
            if (done >= total) {
              setKbMessage("KB indexing... finalizing");
              setKbProgress(1);
              return;
            }
            const elapsed = (Date.now() - start) / 1000;
            const pct = Math.round((100 * done) / Math.max(total, 1));
            const remaining = done > 0 ? (elapsed / done) * (total - done) : 0;
            const timing =
              done > 0
                ? `${fmtDuration(elapsed)} / ${fmtDuration(remaining)} - ${pct}%`
                : `${fmtDuration(elapsed)} / -- - ${pct}%`;
            const name = stage && stage !== "indexing" ? ` (${stage})` : "";
            setKbProgress(done / total);
            setKbMessage(`KB indexing ${done}/${total}${name} | ${timing}`);
          },
        });
        if (res.n_chunks > 0) {
          setKbState("ready");
          setKbMessage(
            `KB ready (${res.n_documents} docs, ${res.n_chunks} chunks)`
          );
        } else {
          setKbState("none");
          setKbMessage("KB: no files uploaded");
        }
        setKbProgress(null);
        setKbDirty(false);
      } catch {
        setKbState("error");
        setKbMessage("KB index error (see log)");
        setKbProgress(null);
      }
    },
    [pushLog]
  );

  // Orchestrator: ensures only one index pass runs at a time. If another index
  // is requested mid-pass, it schedules a single rerun afterwards so documents
  // added during the current pass still get indexed.
  const kickKbIndex = useCallback(
    async (project: string) => {
      if (!project) return;
      if (indexingRef.current) {
        rerunIndexRef.current = true;
        return;
      }
      indexingRef.current = true;
      try {
        do {
          rerunIndexRef.current = false;
          await runKbIndexOnce(project);
        } while (rerunIndexRef.current);
      } finally {
        indexingRef.current = false;
      }
    },
    [runKbIndexOnce]
  );

  // App-level KB index trigger. Lives in the provider (not the dialog) so an
  // in-flight index keeps running after the KB window is closed.
  const indexKb = useCallback(
    async (project: string) => {
      if (!project) return;
      await kickKbIndex(project);
    },
    [kickKbIndex]
  );

  const reindexAllKbs = useCallback(async () => {
    setLogVisible(true);
    pushLog(
      "INFO",
      "Reinstall: caches cleared, artifacts retained — re-indexing all knowledge bases..."
    );
    let names: string[] = [];
    try {
      names = await agent.listProjects();
    } catch (e) {
      pushLog("ERROR", `Reindex aborted — could not list projects: ${(e as Error).message}`);
      return;
    }
    let done = 0;
    let indexed = 0;
    for (const project of names) {
      done += 1;
      const label = displayName(project);
      try {
        const status = await agent.kbStatus(project);
        if (!status.documents || status.documents.length === 0) continue;
        setKbState("indexing");
        setKbMessage(`Reindexing ${done}/${names.length}: ${label}`);
        pushLog("INFO", `[${done}/${names.length}] Indexing KB for ${label}...`);
        const res = await agent.kbIndex(
          project,
          { onLog: (line) => pushLog(agentLogLevel(line), line) },
          true // post-reinstall reindex: always do a full rebuild
        );
        indexed += 1;
        pushLog(
          "SUCCESS",
          `${label}: ${res.n_documents} docs, ${res.n_chunks} chunks indexed.`
        );
      } catch (e) {
        pushLog("ERROR", `KB reindex failed for ${label}: ${(e as Error).message}`);
      }
    }
    setKbState(indexed > 0 ? "ready" : "none");
    setKbMessage(
      indexed > 0
        ? `Reindex complete (${indexed} project KB${indexed === 1 ? "" : "s"})`
        : "KB: no files to index"
    );
    pushLog("SUCCESS", `Reindex complete — ${indexed} knowledge base(s) rebuilt.`);
  }, [displayName, pushLog, setLogVisible]);

  const selectProject = useCallback(
    (full: string) => {
      if (!full) return;
      setCurrentProject(full);
      setCurrentBoard(null);
      setBoardView(null);
      setBoards([]);
      setSelected(new Set());
      pushLog("INFO", `Selected project: ${displayName(full)}`);
      reloadBoards(full);
      kickKbIndex(full);
    },
    [displayName, pushLog, reloadBoards, kickKbIndex]
  );

  const toggleSelected = useCallback((id: number, on: boolean) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  }, []);

  const openDialog = useCallback((d: DialogId) => setDialog(d), []);
  const closeDialog = useCallback(() => setDialog(null), []);

  const value = useMemo<AppStateValue>(
    () => ({
      settings,
      setSettings,
      prefix,
      projects,
      projectsLoading,
      currentProject,
      reloadProjects,
      selectProject,
      displayName,
      boards,
      boardsLoading,
      currentBoard,
      reloadBoards,
      selectBoard,
      boardView,
      boardLoading,
      selected,
      setSelected,
      toggleSelected,
      log,
      pushLog,
      clearLog,
      logVisible,
      setLogVisible,
      kbState,
      kbMessage,
      kbProgress,
      kbDirty,
      markKbDirty,
      clearKbDirty,
      indexKb,
      reindexAllKbs,
      navVisible,
      setNavVisible,
      dialog,
      openDialog,
      closeDialog,
      generateCtx,
      setGenerateCtx,
    }),
    [
      settings,
      prefix,
      projects,
      projectsLoading,
      currentProject,
      reloadProjects,
      selectProject,
      displayName,
      boards,
      boardsLoading,
      currentBoard,
      reloadBoards,
      selectBoard,
      boardView,
      boardLoading,
      selected,
      toggleSelected,
      log,
      pushLog,
      clearLog,
      logVisible,
      kbState,
      kbMessage,
      kbProgress,
      kbDirty,
      markKbDirty,
      clearKbDirty,
      indexKb,
      reindexAllKbs,
      navVisible,
      dialog,
      openDialog,
      closeDialog,
      generateCtx,
    ]
  );

  return (
    <AppStateContext.Provider value={value}>
      {children}
    </AppStateContext.Provider>
  );
}

export function useAppState(): AppStateValue {
  const ctx = useContext(AppStateContext);
  if (!ctx) throw new Error("useAppState must be used within AppStateProvider");
  return ctx;
}
