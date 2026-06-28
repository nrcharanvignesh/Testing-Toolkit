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
  useState,
  type ReactNode,
} from "react";
import {
  agent,
  displayProjectName,
  type Board,
  type BoardView,
  type SettingsResponse,
} from "./agent-client";

export type KbState = "none" | "indexing" | "ready" | "error";
export type DialogId =
  | "settings"
  | "generate"
  | "kb"
  | "defects"
  | "upload"
  | "package"
  | "retrieval"
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
  const [logVisible, setLogVisible] = useState(false);

  const [kbState, setKbState] = useState<KbState>("none");
  const [kbMessage, setKbMessage] = useState("KB: no project selected");

  const [navVisible, setNavVisible] = useState(true);

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
    setLogVisible(true);
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

  const kickKbIndex = useCallback(
    async (project: string) => {
      setKbState("indexing");
      setKbMessage("KB indexing... starting");
      try {
        const status = await agent.kbStatus(project);
        if (!status.documents || status.documents.length === 0) {
          setKbState("none");
          setKbMessage("KB: no files uploaded");
          return;
        }
        const res = await agent.kbIndex(project);
        if (res.n_chunks > 0) {
          setKbState("ready");
          setKbMessage(
            `KB ready (${res.n_documents} docs, ${res.n_chunks} chunks)`
          );
        } else {
          setKbState("none");
          setKbMessage("KB: no files uploaded");
        }
      } catch {
        setKbState("error");
        setKbMessage("KB index error (see log)");
      }
    },
    []
  );

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
