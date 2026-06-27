"use client";

import {
  createContext,
  useContext,
  useEffect,
  useState,
  useCallback,
  type ReactNode,
} from "react";
import { agent, type AgentStatus, type HealthResponse } from "./agent-client";

interface AgentContextValue {
  status: AgentStatus;
  health: HealthResponse | null;
  retry: () => void;
}

const AgentContext = createContext<AgentContextValue>({
  status: "connecting",
  health: null,
  retry: () => {},
});

export function AgentProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AgentStatus>("connecting");
  const [health, setHealth] = useState<HealthResponse | null>(null);

  const check = useCallback(async () => {
    try {
      const h = await agent.health();
      setHealth(h);
      setStatus("connected");
    } catch {
      setHealth(null);
      setStatus("offline");
    }
  }, []);

  useEffect(() => {
    check();
    // Poll every 3 seconds while offline, every 30 seconds when connected
    const interval = setInterval(() => {
      check();
    }, status === "connected" ? 30000 : 3000);
    return () => clearInterval(interval);
  }, [check, status]);

  return (
    <AgentContext.Provider value={{ status, health, retry: check }}>
      {children}
    </AgentContext.Provider>
  );
}

export function useAgent() {
  return useContext(AgentContext);
}
