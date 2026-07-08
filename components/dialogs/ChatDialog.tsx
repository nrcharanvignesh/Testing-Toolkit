"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  FileText,
  Plus,
  Send,
  Square,
  Trash2,
  Wrench,
  X,
} from "lucide-react";
import { Modal } from "@/components/ui/modal";
import { agent } from "@/lib/agent-client";
import { useAppState } from "@/lib/app-state";

/**
 * Custom Generate / AI Chat — web port of the desktop chat_dialog.py.
 * Streaming SSE assistant with an agentic ADO tool-use loop (search / read /
 * update / create work items), optional KB grounding, file attachments, a
 * multi-conversation sidebar (persisted in localStorage, scoped per project),
 * and a Stop control. Guardrails keep the assistant on testing/QA topics.
 */

const STORAGE_KEY = "tt-chat-conversations-v1";
const MAX_TITLE = 40;

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  // Tool activity captured while this assistant turn was streaming.
  tools?: string[];
}

interface Conversation {
  id: string;
  title: string;
  messages: ChatMessage[];
}

type Attachment = { name: string; chars: number; text: string };
type ImageAttachment = {
  name: string;
  media_type: string;
  data_b64: string;
  data_url: string;
};

const IMAGE_TYPES = new Set([
  "image/png",
  "image/jpeg",
  "image/gif",
  "image/webp",
]);

function newConversation(): Conversation {
  return {
    id:
      typeof crypto !== "undefined" && crypto.randomUUID
        ? crypto.randomUUID().slice(0, 12)
        : Math.random().toString(36).slice(2, 14),
    title: "New Chat",
    messages: [],
  };
}

function loadStore(): Record<string, Conversation[]> {
  if (typeof window === "undefined") return {};
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
  } catch {
    return {};
  }
}

function saveStore(store: Record<string, Conversation[]>): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(store));
  } catch {
    /* quota / disabled storage — chat still works for the session */
  }
}

export function ChatDialog({ onClose }: { onClose: () => void }) {
  const { currentProject, displayName, pushLog } = useAppState();

  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string>("");
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [useKb, setUseKb] = useState(true);
  const [useTools, setUseTools] = useState(true);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [images, setImages] = useState<ImageAttachment[]>([]);
  const [reading, setReading] = useState(false);
  const [note, setNote] = useState("");

  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const projectLabel = currentProject ? displayName(currentProject) : "";

  // Load this project's conversations on open / project change.
  useEffect(() => {
    if (!currentProject) return;
    const store = loadStore();
    const list = store[currentProject] ?? [];
    if (list.length === 0) {
      const conv = newConversation();
      setConversations([conv]);
      setActiveId(conv.id);
    } else {
      setConversations(list);
      setActiveId(list[0].id);
    }
  }, [currentProject]);

  // Persist whenever conversations change.
  useEffect(() => {
    if (!currentProject) return;
    const store = loadStore();
    store[currentProject] = conversations;
    saveStore(store);
  }, [conversations, currentProject]);

  const active = useMemo(
    () => conversations.find((c) => c.id === activeId) ?? null,
    [conversations, activeId]
  );

  // Auto-scroll to the newest content while streaming.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [active?.messages, busy]);

  const patchActive = (fn: (c: Conversation) => Conversation) => {
    setConversations((prev) =>
      prev.map((c) => (c.id === activeId ? fn(c) : c))
    );
  };

  const startNewChat = () => {
    if (busy) return;
    const conv = newConversation();
    setConversations((prev) => [conv, ...prev]);
    setActiveId(conv.id);
    setAttachments([]);
    setImages([]);
    setInput("");
  };

  const deleteChat = (id: string) => {
    if (busy) return;
    setConversations((prev) => {
      const next = prev.filter((c) => c.id !== id);
      if (next.length === 0) {
        const conv = newConversation();
        setActiveId(conv.id);
        return [conv];
      }
      if (id === activeId) setActiveId(next[0].id);
      return next;
    });
  };

  const addImageFiles = async (imgFiles: File[]) => {
    const read = (f: File) =>
      new Promise<ImageAttachment | null>((resolve) => {
        const reader = new FileReader();
        reader.onerror = () => resolve(null);
        reader.onload = () => {
          const dataUrl = String(reader.result || "");
          const comma = dataUrl.indexOf(",");
          if (comma < 0) return resolve(null);
          resolve({
            name: f.name || "pasted-image",
            media_type: f.type || "image/png",
            data_b64: dataUrl.slice(comma + 1),
            data_url: dataUrl,
          });
        };
        reader.readAsDataURL(f);
      });
    const mapped = (await Promise.all(imgFiles.map(read))).filter(
      (x): x is ImageAttachment => x !== null
    );
    if (mapped.length) {
      setImages((prev) => {
        const byName = new Map(prev.map((a) => [a.name, a]));
        for (const m of mapped) byName.set(m.name, m);
        return Array.from(byName.values());
      });
    }
    return mapped.length;
  };

  const pickFiles = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    const all = Array.from(files);
    const imgFiles = all.filter((f) => IMAGE_TYPES.has(f.type));
    const docFiles = all.filter((f) => !IMAGE_TYPES.has(f.type));
    setReading(true);
    setNote("Reading files...");
    try {
      const nImg = imgFiles.length ? await addImageFiles(imgFiles) : 0;
      let nDoc = 0;
      let failedDocs = 0;
      if (docFiles.length) {
        const results = await agent.extractAttachments(docFiles);
        const mapped: Attachment[] = results
          .filter((r) => !r.error)
          .map((r) => ({ name: r.name, chars: r.chars, text: r.text }));
        nDoc = mapped.length;
        failedDocs = results.filter((r) => r.error).length;
        setAttachments((prev) => {
          const byName = new Map(prev.map((a) => [a.name, a]));
          for (const m of mapped) byName.set(m.name, m);
          return Array.from(byName.values());
        });
      }
      const parts: string[] = [];
      if (nDoc) parts.push(`${nDoc} file(s)`);
      if (nImg) parts.push(`${nImg} image(s)`);
      if (failedDocs) parts.push(`skipped ${failedDocs} (unreadable)`);
      setNote(parts.length ? `Attached ${parts.join(", ")}.` : "Nothing attached.");
    } catch (e) {
      setNote(`Attach failed: ${(e as Error).message}`);
    } finally {
      setReading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  // Paste screenshots directly into the composer (desktop chat parity).
  const onPasteImages = async (e: React.ClipboardEvent) => {
    const imgs = Array.from(e.clipboardData?.items ?? [])
      .filter((it) => it.kind === "file" && it.type.startsWith("image/"))
      .map((it) => it.getAsFile())
      .filter((f): f is File => f !== null);
    if (imgs.length === 0) return;
    e.preventDefault();
    const n = await addImageFiles(imgs);
    if (n) setNote(`Attached ${n} pasted image(s).`);
  };

  const send = async () => {
    const text = input.trim();
    if (
      (!text && attachments.length === 0 && images.length === 0) ||
      busy ||
      !currentProject
    )
      return;

    const attachmentText = attachments
      .map((a) => `--- FILE: ${a.name} ---\n${a.text}`)
      .join("\n\n");

    const imgPayload = images.map((i) => ({
      media_type: i.media_type,
      data_b64: i.data_b64,
    }));
    const placeholder =
      images.length && !attachments.length
        ? "(see attached image(s))"
        : "(see attached files)";
    const userMsg: ChatMessage = {
      role: "user",
      content: text || placeholder,
    };
    const assistantMsg: ChatMessage = { role: "assistant", content: "", tools: [] };

    // Snapshot history (before this turn) for the API call.
    const history = (active?.messages ?? []).map((m) => ({
      role: m.role,
      content: m.content,
    }));

    patchActive((c) => {
      const isFirst = c.messages.length === 0;
      return {
        ...c,
        title:
          isFirst && text
            ? text.slice(0, MAX_TITLE) + (text.length > MAX_TITLE ? "..." : "")
            : c.title,
        messages: [...c.messages, userMsg, assistantMsg],
      };
    });
    setInput("");
    setAttachments([]);
    setImages([]);
    setNote("");
    setBusy(true);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await agent.chatStream(
        {
          project: currentProject,
          messages: [...history, { role: "user", content: userMsg.content }],
          use_kb: useKb,
          use_tools: useTools,
          attachment_text: attachmentText,
          images: imgPayload,
        },
        {
          onText: (delta) =>
            patchActive((c) => {
              const msgs = c.messages.slice();
              const last = msgs[msgs.length - 1];
              if (last && last.role === "assistant") {
                msgs[msgs.length - 1] = {
                  ...last,
                  content: last.content + delta,
                };
              }
              return { ...c, messages: msgs };
            }),
          onTool: (name, phase) => {
            if (phase !== "start") return;
            patchActive((c) => {
              const msgs = c.messages.slice();
              const last = msgs[msgs.length - 1];
              if (last && last.role === "assistant") {
                msgs[msgs.length - 1] = {
                  ...last,
                  tools: [...(last.tools ?? []), name],
                };
              }
              return { ...c, messages: msgs };
            });
          },
          onError: (message) => {
            pushLog("ERROR", `Chat: ${message}`);
            patchActive((c) => {
              const msgs = c.messages.slice();
              const last = msgs[msgs.length - 1];
              if (last && last.role === "assistant") {
                msgs[msgs.length - 1] = {
                  ...last,
                  content:
                    last.content + `\n\n[Error] ${message}`,
                };
              }
              return { ...c, messages: msgs };
            });
          },
          onDone: () => {},
        },
        controller.signal
      );
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        const msg = (e as Error).message;
        pushLog("ERROR", `Chat failed: ${msg}`);
        patchActive((c) => {
          const msgs = c.messages.slice();
          const last = msgs[msgs.length - 1];
          if (last && last.role === "assistant" && !last.content) {
            msgs[msgs.length - 1] = { ...last, content: `[Error] ${msg}` };
          }
          return { ...c, messages: msgs };
        });
      }
    } finally {
      setBusy(false);
      abortRef.current = null;
    }
  };

  const stop = () => {
    abortRef.current?.abort();
    setBusy(false);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // CJK IME safety: don't submit mid-composition.
    if (
      e.key === "Enter" &&
      !e.shiftKey &&
      !e.nativeEvent.isComposing &&
      (e.nativeEvent as unknown as { keyCode?: number }).keyCode !== 229
    ) {
      e.preventDefault();
      void send();
    }
  };

  return (
    <Modal
      open
      onClose={busy ? () => {} : onClose}
      title={`Custom Generate${projectLabel ? ` - ${projectLabel}` : ""}`}
      width={1040}
      footer={
        <>
          {note && (
            <span className="mr-auto text-xs text-muted-foreground">{note}</span>
          )}
          <button className="tt-btn-ghost" onClick={onClose} disabled={busy}>
            Close
          </button>
        </>
      }
    >
      <div className="flex h-[64vh] gap-3">
        {/* Conversation sidebar */}
        <aside className="flex w-56 shrink-0 flex-col gap-2 border-r border-[var(--tt-outline-soft)] pr-3">
          <button
            className="tt-btn-primary flex items-center justify-center gap-1.5"
            onClick={startNewChat}
            disabled={busy}
          >
            <Plus className="h-4 w-4" /> New chat
          </button>
          <div className="flex-1 overflow-auto">
            {conversations.map((c) => (
              <div
                key={c.id}
                className={`group mb-1 flex items-center gap-1 rounded-md px-2 py-1.5 text-xs ${
                  c.id === activeId
                    ? "bg-[var(--tt-outline-soft)] text-[var(--tt-text-primary)]"
                    : "text-[var(--tt-text-secondary)] hover:bg-[var(--tt-outline-soft)]"
                }`}
              >
                <button
                  className="flex-1 truncate text-left"
                  onClick={() => !busy && setActiveId(c.id)}
                  disabled={busy}
                  title={c.title}
                >
                  {c.title}
                </button>
                <button
                  className="opacity-0 transition-opacity group-hover:opacity-100"
                  onClick={() => deleteChat(c.id)}
                  disabled={busy}
                  aria-label="Delete conversation"
                >
                  <Trash2 className="h-3.5 w-3.5 text-[var(--tt-text-muted)] hover:text-[var(--tt-danger)]" />
                </button>
              </div>
            ))}
          </div>
        </aside>

        {/* Chat pane */}
        <div className="flex min-w-0 flex-1 flex-col gap-2">
          <div
            ref={scrollRef}
            className="flex-1 overflow-auto rounded-lg border border-[var(--tt-outline)] bg-[var(--tt-surface-base)] p-3"
          >
            {!active || active.messages.length === 0 ? (
              <div className="flex h-full items-center justify-center text-center text-sm text-[var(--tt-text-muted)]">
                <div>
                  Ask about your work items, test coverage, or QA process.
                  <br />
                  The assistant can search, read, update, and create ADO work
                  items.
                </div>
              </div>
            ) : (
              active.messages.map((m, i) => (
                <div key={i} className="mb-4 last:mb-0">
                  <div className="mb-1 text-[10px] font-bold uppercase tracking-wide text-[var(--tt-text-muted)]">
                    {m.role === "user" ? "You" : "Assistant"}
                  </div>
                  {m.tools && m.tools.length > 0 && (
                    <div className="mb-1.5 flex flex-wrap gap-1">
                      {m.tools.map((t, j) => (
                        <span
                          key={j}
                          className="inline-flex items-center gap-1 rounded border border-[var(--tt-outline)] px-1.5 py-0.5 text-[10px] text-[var(--tt-primary-soft)]"
                        >
                          <Wrench className="h-3 w-3" /> {t}
                        </span>
                      ))}
                    </div>
                  )}
                  <div className="whitespace-pre-wrap text-sm leading-relaxed text-[var(--tt-text-secondary)]">
                    {m.content ||
                      (busy && i === active.messages.length - 1 ? "..." : "")}
                  </div>
                </div>
              ))
            )}
          </div>

          {/* Attachment chips */}
          {attachments.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {attachments.map((a) => (
                <span
                  key={a.name}
                  className="inline-flex items-center gap-1 rounded border border-[var(--tt-outline)] bg-[var(--tt-surface-high)] px-2 py-0.5 text-xs text-[var(--tt-text-secondary)]"
                >
                  <FileText className="h-3 w-3" /> {a.name}
                  <button
                    onClick={() =>
                      setAttachments((prev) =>
                        prev.filter((x) => x.name !== a.name)
                      )
                    }
                    aria-label={`Remove ${a.name}`}
                  >
                    <X className="h-3 w-3 hover:text-[var(--tt-danger)]" />
                  </button>
                </span>
              ))}
            </div>
          )}

          {/* Image thumbnails (desktop chat parity: pasted/attached images) */}
          {images.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {images.map((img) => (
                <span
                  key={img.name}
                  className="group relative inline-block overflow-hidden rounded-md border border-[var(--tt-outline)]"
                >
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={img.data_url || "/placeholder.svg"}
                    alt={img.name}
                    className="h-16 w-16 object-cover"
                  />
                  <button
                    onClick={() =>
                      setImages((prev) =>
                        prev.filter((x) => x.name !== img.name)
                      )
                    }
                    aria-label={`Remove ${img.name}`}
                    className="absolute right-0.5 top-0.5 rounded bg-[var(--tt-surface-base)]/80 p-0.5"
                  >
                    <X className="h-3 w-3 text-[var(--tt-text-secondary)] hover:text-[var(--tt-danger)]" />
                  </button>
                </span>
              ))}
            </div>
          )}

          {/* Composer */}
          <div className="flex items-end gap-2">
            <div className="flex flex-col gap-1">
              <label className="flex items-center gap-1 text-[11px] text-[var(--tt-text-secondary)]">
                <input
                  type="checkbox"
                  checked={useKb}
                  onChange={(e) => setUseKb(e.target.checked)}
                  disabled={busy}
                />
                KB
              </label>
              <label className="flex items-center gap-1 text-[11px] text-[var(--tt-text-secondary)]">
                <input
                  type="checkbox"
                  checked={useTools}
                  onChange={(e) => setUseTools(e.target.checked)}
                  disabled={busy}
                />
                Tools
              </label>
            </div>
            <input
              ref={fileRef}
              type="file"
              multiple
              accept="image/*,.pdf,.txt,.md,.csv,.docx,.xlsx,.json"
              className="hidden"
              onChange={(e) => void pickFiles(e.target.files)}
            />
            <button
              className="tt-btn-ghost h-9 shrink-0"
              onClick={() => fileRef.current?.click()}
              disabled={busy || reading}
              title="Attach files or images (you can also paste a screenshot)"
            >
              {reading ? "Reading..." : <FileText className="h-4 w-4" />}
            </button>
            <textarea
              className="tt-input min-h-9 flex-1 resize-none text-sm"
              rows={2}
              placeholder="Message the assistant (Enter to send, Shift+Enter for newline, paste an image)..."
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
              onPaste={(e) => void onPasteImages(e)}
              disabled={busy || !currentProject}
            />
            {busy ? (
              <button
                className="tt-btn-danger flex h-9 shrink-0 items-center gap-1.5"
                onClick={stop}
              >
                <Square className="h-3.5 w-3.5" /> Stop
              </button>
            ) : (
              <button
                className="tt-btn-primary flex h-9 shrink-0 items-center gap-1.5"
                onClick={() => void send()}
                disabled={
                  !currentProject ||
                  (!input.trim() &&
                    attachments.length === 0 &&
                    images.length === 0)
                }
              >
                <Send className="h-4 w-4" /> Send
              </button>
            )}
          </div>
        </div>
      </div>
    </Modal>
  );
}
