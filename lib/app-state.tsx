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
  useEffect,
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
  setLastProjectPref,
  setLastBoardPref,
} from "./preferences";

export type KbState = "none" | "indexing" | "ready" | "error";

export type KbUploadStatus =
  | "queued"
  | "uploading"
  | "processing"
  | "done"
  | "error";

export interface KbUploadItem {
  id: string;
  name: string;
  size: number;
  /** 0..1 transfer fraction. */
  progress: number;
  status: KbUploadStatus;
  error?: string;
}
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
  /** When set, the Generate dialog loads this artifact's payload on open so
   *  it can be regenerated with feedback ("Load and Regenerate with feedback"). */
  loadArtifactPath?: string;
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

  // kb uploads (app-level so the batch survives the KB dialog closing and is
  // reflected in the status bar)
  /** The current/last upload batch (per project, see kbUploadProject). */
  kbUploads: KbUploadItem[];
  /** True while a batch is actively transferring. */
  kbUploading: boolean;
  /** Which project the current upload batch belongs to. */
  kbUploadProject: string;
  /** Upload files into a project's KB at app level; auto-indexes when done. */
  uploadKbFiles: (project: string, files: File[]) => Promise<void>;
  /** Clear the visible upload batch (e.g. after the user dismisses it). */
  clearKbUploads: () => void;

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

  // KB upload batch state lives here (not in the dialog) so it persists when
  // the KB window is closed/reopened mid-upload and so the status bar can show
  // an "Uploading X/Y" indicator.
  const [kbUploads, setKbUploads] = useState<KbUploadItem[]>([]);
  const [kbUploading, setKbUploading] = useState(false);
  const [kbUploadProject, setKbUploadProject] = useState("");
  const clearKbUploads = useCallback(() => setKbUploads([]), []);

  // Guard against overlapping index passes. If a new index is requested while
  // one is running (e.g. more files uploaded mid-index), we don't start a
  // second concurrent pass — we flag a rerun so exactly one more pass runs
  // afterwards to pick up the newly added documents.
  const indexingRef = useRef(false);
  const rerunIndexRef = useRef(false);

  // Ensures the saved last project/board is restored at most once per session.
  const restoredRef = useRef(false);

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
    async (projectOverride?: string, preferredBoardLabel?: string) => {
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
          // Prefer the previously selected board (restored across launches) when
          // it still exists; otherwise fall back to the Stories board / first.
          const preferred = preferredBoardLabel
            ? deduped.find((b) => b.label === preferredBoardLabel)
            : undefined;
          const storiesBoard =
            preferred ??
            deduped.find((b) =>
              (b.name || "").toLowerCase().includes("stories")
            ) ??
            deduped[0];
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
      // Remember this board so it's restored on the next launch.
      setLastBoardPref(b.label);
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

  // Live progress/log handlers + terminal-state handling shared by a fresh
  // index run and a reattach to an in-flight one.
  const kbJobHandlers = useCallback(
    () => ({
      onLog: (line: string) => pushLog(agentLogLevel(line), line),
      onProgress: (p: { current: number; total: number }) => {
        const { current: done, total } = p;
        setKbMessage("Indexing");
        if (!total || total <= 0) {
          setKbProgress(null);
          return;
        }
        setKbProgress(done >= total ? 1 : done / total);
      },
    }),
    [pushLog]
  );

  const finalizeKbIndex = useCallback(
    (res: { n_chunks: number; n_documents: number }) => {
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
    },
    []
  );

  const runKbIndexOnce = useCallback(
    async (project: string) => {
      setKbState("indexing");
      setKbMessage("Indexing");
      setKbProgress(null);
      try {
        const status = await agent.kbStatus(project);
        if (!status.documents || status.documents.length === 0) {
          setKbState("none");
          setKbMessage("KB: no files uploaded");
          setKbProgress(null);
          return;
        }
        // The agent dedupes: if an index is already running for this project
        // (e.g. started before this tab opened), this reattaches to it instead
        // of starting a second pass.
        const res = await agent.kbIndex(project, kbJobHandlers());
        finalizeKbIndex(res);
      } catch {
        setKbState("error");
        setKbMessage("KB index error (see log)");
        setKbProgress(null);
      }
    },
    [kbJobHandlers, finalizeKbIndex]
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
  // App-level so an in-flight index keeps running after the KB window is closed.
  // The agent runs indexing as a DETACHED task, so it also keeps running if the
  // whole web app is closed. On reopen, restoring the last project re-kicks the
  // index, and the agent dedupes — returning the still-running job so the web
  // reattaches to its live progress instead of starting a second pass.
  const indexKb = useCallback(
    async (project: string) => {
      if (!project) return;
      await kickKbIndex(project);
    },
    [kickKbIndex]
  );

  // App-level KB upload. Lives in the provider (not the KB dialog) so the batch
  // keeps running and stays visible after the window is closed/reopened, drives
  // the status-bar "Uploading X/Y" indicator, and auto-starts indexing once the
  // whole batch finishes.
  const uploadKbFiles = useCallback(
    async (project: string, files: File[]) => {
      if (!project || files.length === 0) return;

      const items: KbUploadItem[] = files.map((f, i) => ({
        id: `${Date.now()}-${i}-${f.name}`,
        name: f.name,
        size: f.size,
        progress: 0,
        status: "queued",
      }));
      setKbUploadProject(project);
      setKbUploads(items);
      setKbUploading(true);

      const patch = (id: string, p: Partial<KbUploadItem>) =>
        setKbUploads((prev) => prev.map((u) => (u.id === id ? { ...u, ...p } : u)));

      let okCount = 0;

      // Small worker pool: each file is an independent localhost copy, so a few
      // parallel transfers remove the sequential round-trip stall on big drops.
      const CONCURRENCY = Math.min(5, files.length);
      let next = 0;
      const uploadOne = async (i: number) => {
        const f = files[i];
        const id = items[i].id;
        patch(id, { status: "uploading", progress: 0 });
        try {
          await agent.kbUploadProgress(project, f, (frac) => {
            if (frac === null) return; // indeterminate — leave bar animating
            patch(id, {
              progress: Math.min(0.99, frac),
              status: frac >= 1 ? "processing" : "uploading",
            });
          });
          patch(id, { status: "done", progress: 1 });
          okCount += 1;
          pushLog("SUCCESS", `Uploaded ${f.name} to KB.`);
        } catch (e) {
          patch(id, { status: "error", error: (e as Error).message });
          pushLog("ERROR", `Upload failed for ${f.name}: ${(e as Error).message}`);
        }
      };
      const worker = async () => {
        while (next < files.length) {
          const i = next++;
          await uploadOne(i);
        }
      };
      await Promise.all(Array.from({ length: CONCURRENCY }, worker));

      setKbUploading(false);

      // Any successful upload means the index is now stale — kick off indexing
      // immediately (it runs here at app level, so it survives the dialog).
      if (okCount > 0) {
        markKbDirty();
        pushLog("INFO", "Upload complete — indexing knowledge base...");
        void kickKbIndex(project);
      }

      // Auto-dismiss the batch shortly after a fully successful upload; keep it
      // on screen if anything failed so the user can read the error.
      if (okCount === files.length) {
        setTimeout(() => setKbUploads([]), 2500);
      }
    },
    [pushLog, markKbDirty, kickKbIndex]
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
    (full: string, preferredBoardLabel?: string) => {
      if (!full) return;
      setCurrentProject(full);
      setCurrentBoard(null);
      setBoardView(null);
      setBoards([]);
      setSelected(new Set());
      // Remember this project so it's restored on the next launch.
      setLastProjectPref(full);
      pushLog("INFO", `Selected project: ${displayName(full)}`);
      reloadBoards(full, preferredBoardLabel);
      kickKbIndex(full);
    },
    [displayName, pushLog, reloadBoards, kickKbIndex]
  );

  // Restore the last selected project (and, via reloadBoards, its last board)
  // once the project list has loaded — so the app reopens exactly where the
  // user left off. Only runs when nothing is selected yet, so a manual Refresh
  // after the user has navigated never yanks them back.
  useEffect(() => {
    if (restoredRef.current) return;
    if (!projects.length) return;
    restoredRef.current = true;
    if (currentProject) return; // user already picked something
    const { lastProject, lastBoard } = getPreferences();
    if (lastProject && projects.includes(lastProject)) {
      selectProject(lastProject, lastBoard || undefined);
    }
  }, [projects, currentProject, selectProject]);

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
      kbUploads,
      kbUploading,
      kbUploadProject,
      uploadKbFiles,
      clearKbUploads,
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
      kbUploads,
      kbUploading,
      kbUploadProject,
      uploadKbFiles,
      clearKbUploads,
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
