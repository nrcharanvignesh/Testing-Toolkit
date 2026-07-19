"use client";

import {
  createContext,
  useContext,
  useEffect,
  useRef,
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
  const inFlightRef = useRef(false);
  const statusRef = useRef(status);
  const failCountRef = useRef(0);
  useEffect(() => { statusRef.current = status; }, [status]);

  // Require 3 consecutive failures before declaring offline. Prevents
  // the onboarding screen from flashing when the agent is simply slow
  // under heavy load (e.g. bulk uploads, indexing).
  const OFFLINE_THRESHOLD = 3;

  const check = useCallback(async () => {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    try {
      const h = await agent.health();
      setHealth(h);
      setStatus("connected");
      failCountRef.current = 0;
    } catch {
      failCountRef.current++;
      if (
        statusRef.current !== "connected" ||
        failCountRef.current >= OFFLINE_THRESHOLD
      ) {
        setHealth(null);
        setStatus("offline");
      }
    } finally {
      inFlightRef.current = false;
    }
  }, []);

  useEffect(() => {
    check();
    // Single stable interval; timing adapts via statusRef without
    // recreating the interval on every status change.
    let elapsed = 0;
    const tick = 3000;
    const id = setInterval(() => {
      elapsed += tick;
      const needed = statusRef.current === "connected" ? 30000 : 3000;
      if (elapsed >= needed) {
        elapsed = 0;
        check();
      }
    }, tick);
    return () => clearInterval(id);
  }, [check]);

  return (
    <AgentContext.Provider value={{ status, health, retry: check }}>
      {children}
    </AgentContext.Provider>
  );
}

export function useAgent() {
  return useContext(AgentContext);
}
