"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import axios from "axios";
import { Send, Bot, User, AlertCircle, Trash2, MessageSquare, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { supabase } from "@/lib/supabaseClient";

const FASTAPI_URL = process.env.NEXT_PUBLIC_FASTAPI_URL || "http://127.0.0.1:8000";

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: Date;
  isError?: boolean;
}

interface ChatWindowProps {
  batchId?: string;
  documentId?: string;
  /** For multi-doc mode: pass 2+ IDs and the chat routes to /api/chat/multi */
  documentIds?: string[];
  filename: string;
  documentCount?: number;
  /** When provided, chats via the public /api/share/{token}/chat endpoint (no auth). */
  shareToken?: string;
}

export default function ChatWindow({ batchId, documentId, documentIds, filename, documentCount, shareToken }: ChatWindowProps) {
  // Effective doc IDs: prefer explicit documentIds array, fall back to single documentId
  const effectiveDocIds: string[] =
    documentIds && documentIds.length > 0
      ? documentIds
      : documentId
      ? [documentId]
      : [];
  const isMultiDoc = !!(batchId && documentCount && documentCount > 1) || effectiveDocIds.length > 1;
  const count = documentCount || effectiveDocIds.length || 1;

  const storageKey = shareToken
    ? null
    : batchId
    ? `pdf_chat_batch_${batchId}`
    : documentId
    ? `pdf_chat_doc_${documentId}`
    : effectiveDocIds.length > 0
    ? `pdf_chat_docs_${effectiveDocIds.slice().sort().join("_")}`
    : null;

  const welcomeMessage: Message = {
    id: "welcome",
    role: "assistant",
    content: isMultiDoc
      ? `Hello! I've analyzed **${count} documents** in **${filename}**. Ask me anything — I'll search across all documents in this batch to find the best answer.`
      : `Hello! I've analyzed **${filename}**. Ask me anything about this document — I'll provide answers based on its content.`,
    timestamp: new Date(),
  };

  const [messages, setMessages] = useState<Message[]>([welcomeMessage]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  /** true while we're fetching persisted history on first load (share links only) */
  const [isLoadingHistory, setIsLoadingHistory] = useState(!!shareToken);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // ── Load persisted share-link chat history on mount ──────────────────────
  useEffect(() => {
    if (!shareToken) return;

    let cancelled = false;
    setIsLoadingHistory(true);

    axios
      .get<{ id: string; role: "user" | "assistant"; content: string; created_at: string }[]>(
        `${FASTAPI_URL}/api/share/${shareToken}/chat-history`
      )
      .then((res) => {
        if (cancelled) return;
        const history = res.data;

        if (history.length === 0) {
          // No prior conversation — keep the welcome message
          setMessages([welcomeMessage]);
        } else {
          // Restore the full conversation; prepend a static welcome banner
          const restored: Message[] = [
            welcomeMessage,
            ...history.map((row) => ({
              id: row.id,
              role: row.role,
              content: row.content,
              timestamp: new Date(row.created_at),
            })),
          ];
          setMessages(restored);
        }
      })
      .catch((err) => {
        if (cancelled) return;
        // Silently fall back to the welcome message — don't block the chat UI
        console.warn("[ChatWindow] Could not load share chat history:", err);
        setMessages([welcomeMessage]);
      })
      .finally(() => {
        if (!cancelled) setIsLoadingHistory(false);
      });

    return () => {
      cancelled = true;
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shareToken]);

  // ── Load persisted chat from localStorage on mount/switch (authenticated chats) ──
  useEffect(() => {
    if (shareToken || !storageKey || typeof window === "undefined") return;

    try {
      const saved = localStorage.getItem(storageKey);
      if (saved) {
        const parsed = JSON.parse(saved);
        if (Array.isArray(parsed) && parsed.length > 0) {
          setMessages(
            parsed.map((m: any) => ({
              ...m,
              timestamp: new Date(m.timestamp),
            }))
          );
          return;
        }
      }
    } catch (err) {
      console.warn("[ChatWindow] Could not restore local chat history:", err);
    }
    setMessages([welcomeMessage]);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storageKey, shareToken]);

  // ── Save messages to localStorage on change (authenticated chats) ──────────
  useEffect(() => {
    if (shareToken || !storageKey || typeof window === "undefined") return;

    try {
      const validMessages = messages.filter((m) => !m.isError);
      if (validMessages.length > 1) {
        localStorage.setItem(storageKey, JSON.stringify(validMessages));
      } else if (validMessages.length === 1 && validMessages[0].id === "welcome") {
        localStorage.removeItem(storageKey);
      }
    } catch (err) {
      console.warn("[ChatWindow] Could not persist chat history:", err);
    }
  }, [messages, storageKey, shareToken]);

  // Auto-scroll to bottom when messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isLoading]);

  // Auto-resize textarea
  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 160)}px`;
  }, [input]);

  const sendMessage = useCallback(async () => {
    const trimmed = input.trim();
    if (!trimmed || isLoading) return;

    const userMessage: Message = {
      id: `user-${Date.now()}`,
      role: "user",
      content: trimmed,
      timestamp: new Date(),
    };

    setMessages((prev) => [...prev, userMessage]);
    setInput("");
    setIsLoading(true);

    try {
      // Build chat history for backend (exclude welcome message and errors)
      // Send the last 6 turns (up to 12 messages: 6 user + 6 assistant)
      const history = messages
        .filter((m) => m.id !== "welcome" && !m.isError)
        .slice(-12)
        .map((m) => ({ role: m.role, content: m.content }));

      let endpoint: string;
      let headers: Record<string, string> = {};
      let payload: object;

      if (shareToken) {
        // Public share-link chat — no auth needed
        endpoint = `${FASTAPI_URL}/api/share/${shareToken}/chat`;
        payload = { question: trimmed, chat_history: history };
      } else if (batchId) {
        // Batch-scoped RAG chat
        const { data: { session } } = await supabase.auth.getSession();
        const token = session?.access_token ?? "";
        endpoint = `${FASTAPI_URL}/api/chat`;
        headers["Authorization"] = `Bearer ${token}`;
        payload = { batch_id: batchId, question: trimmed, chat_history: history };
      } else if (effectiveDocIds.length > 1) {
        // Multi-document explicit RAG chat
        const { data: { session } } = await supabase.auth.getSession();
        const token = session?.access_token ?? "";
        endpoint = `${FASTAPI_URL}/api/chat/multi`;
        headers["Authorization"] = `Bearer ${token}`;
        payload = { document_ids: effectiveDocIds, question: trimmed, chat_history: history };
      } else {
        // Authenticated owner/dashboard single document chat
        const { data: { session } } = await supabase.auth.getSession();
        const token = session?.access_token ?? "";
        endpoint = `${FASTAPI_URL}/api/chat`;
        headers["Authorization"] = `Bearer ${token}`;
        payload = { document_id: documentId, question: trimmed, chat_history: history };
      }

      const response = await axios.post(endpoint, payload, {
        timeout: 180000,
        headers,
      });

      const assistantMessage: Message = {
        id: `assistant-${Date.now()}`,
        role: "assistant",
        content: response.data.answer || response.data.response || "I couldn't find an answer to that.",
        timestamp: new Date(),
      };

      setMessages((prev) => [...prev, assistantMessage]);
    } catch (err: unknown) {
      // Always log the full error to DevTools for debugging
      console.error("[ChatWindow] sendMessage error:", err);
      let errText = "Sorry, I encountered an error. Please try again.";
      if (axios.isAxiosError(err)) {
        if (err.code === "ECONNABORTED" || err.message?.includes("timeout")) {
          errText =
            "The request timed out — the AI model is taking too long to respond. " +
            "This usually means Ollama is loading a model into memory. Please wait a moment and try again.";
        } else if (err.response?.data?.detail) {
          errText = err.response.data.detail;
        } else if (err.response?.status === 401) {
          errText = "Your session has expired. Please refresh the page and sign in again.";
        } else if (err.response?.status === 404) {
          errText = "Document not found. It may have been deleted.";
        } else if (!err.response) {
          errText = `Could not reach the backend server (${FASTAPI_URL}). If using Render free tier, the backend might be waking up from sleep (takes ~50 seconds). Please verify NEXT_PUBLIC_FASTAPI_URL in your Vercel settings and try again in a moment.`;
        }
      }

      setMessages((prev) => [
        ...prev,
        {
          id: `error-${Date.now()}`,
          role: "assistant",
          content: errText,
          timestamp: new Date(),
          isError: true,
        },
      ]);
    } finally {
      setIsLoading(false);
      textareaRef.current?.focus();
    }
  }, [input, isLoading, messages, batchId, documentId, effectiveDocIds, shareToken]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  const clearChat = () => {
    if (storageKey && typeof window !== "undefined") {
      try {
        localStorage.removeItem(storageKey);
      } catch {}
    }
    setMessages([
      {
        id: "welcome",
        role: "assistant",
        content: `Chat cleared. Ask me anything about **${filename}**.`,
        timestamp: new Date(),
      },
    ]);
  };

  return (
    <div className="flex h-full flex-col rounded-2xl border border-gray-800/60 bg-gray-900/60 backdrop-blur-sm overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-4 border-b border-gray-800/60 flex-shrink-0">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-violet-600 to-indigo-600 shadow-lg shadow-violet-900/20">
            <MessageSquare className="h-4 w-4 text-white" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-white">Chat with Document</h2>
            <p className="text-xs text-gray-500 max-w-xs truncate">{filename}</p>
          </div>
        </div>
        <button
          onClick={clearChat}
          title="Clear chat history"
          className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium text-gray-500 hover:bg-gray-800 hover:text-red-400 transition-all duration-150"
        >
          <Trash2 className="h-3.5 w-3.5" />
          Clear
        </button>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4 min-h-0 relative">
        {/* History loading overlay */}
        {isLoadingHistory && (
          <div className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-3 bg-gray-900/80 backdrop-blur-sm animate-in fade-in duration-200">
            <div className="flex h-10 w-10 items-center justify-center rounded-full bg-gradient-to-br from-violet-600 to-indigo-600 shadow-lg shadow-violet-900/30">
              <Loader2 className="h-5 w-5 text-white animate-spin" />
            </div>
            <p className="text-xs text-gray-400">Loading conversation history…</p>
          </div>
        )}

        {messages.map((message) => (
          <MessageBubble key={message.id} message={message} />
        ))}

        {/* Thinking indicator — dots only, no state info */}
        {isLoading && (
          <div className="flex items-start gap-3 animate-in fade-in duration-300">
            <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-violet-600 to-indigo-600">
              <Bot className="h-4 w-4 text-white" />
            </div>
            <div className="rounded-2xl rounded-tl-sm bg-gray-800/80 px-4 py-3">
              <span className="flex gap-1">
                {[0, 1, 2].map((i) => (
                  <span
                    key={i}
                    className="h-2 w-2 rounded-full bg-violet-500 animate-bounce"
                    style={{ animationDelay: `${i * 0.15}s`, animationDuration: "0.8s" }}
                  />
                ))}
              </span>
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Input Area */}
      <div className="border-t border-gray-800/60 px-4 py-4 flex-shrink-0">
        <div className="flex items-end gap-3 rounded-xl border border-gray-700 bg-gray-800/60 px-4 py-3 focus-within:border-violet-600/60 transition-colors duration-200">
          <textarea
            ref={textareaRef}
            id="chat-input"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask a question about the document..."
            rows={1}
            disabled={isLoading || isLoadingHistory}
            className="flex-1 resize-none bg-transparent text-sm text-gray-200 placeholder-gray-600 focus:outline-none disabled:opacity-50 leading-relaxed"
            style={{ minHeight: "24px", maxHeight: "160px" }}
          />
          <button
            onClick={sendMessage}
            disabled={!input.trim() || isLoading || isLoadingHistory}
            id="send-message-btn"
            className={cn(
              "flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg transition-all duration-200",
              input.trim() && !isLoading && !isLoadingHistory
                ? "bg-violet-600 text-white hover:bg-violet-500 shadow-lg shadow-violet-900/30"
                : "bg-gray-700 text-gray-600 cursor-not-allowed"
            )}
          >
            <Send className="h-4 w-4" />
          </button>
        </div>
        <p className="mt-2 text-center text-xs text-gray-700">
          Press <kbd className="rounded bg-gray-800 px-1 py-0.5 font-mono text-gray-500">Enter</kbd> to send,{" "}
          <kbd className="rounded bg-gray-800 px-1 py-0.5 font-mono text-gray-500">Shift+Enter</kbd> for new line
        </p>
      </div>
    </div>
  );
}

function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === "user";

  // Simple bold markdown rendering
  const formatContent = (text: string) => {
    const parts = text.split(/(\*\*[^*]+\*\*)/g);
    return parts.map((part, i) => {
      if (part.startsWith("**") && part.endsWith("**")) {
        return (
          <strong key={i} className="font-semibold text-white">
            {part.slice(2, -2)}
          </strong>
        );
      }
      return <span key={i}>{part}</span>;
    });
  };

  return (
    <div
      className={cn(
        "flex items-start gap-3 animate-in fade-in slide-in-from-bottom-2 duration-300",
        isUser && "flex-row-reverse"
      )}
    >
      {/* Avatar */}
      <div
        className={cn(
          "flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full",
          isUser
            ? "bg-gradient-to-br from-sky-600 to-blue-600"
            : "bg-gradient-to-br from-violet-600 to-indigo-600"
        )}
      >
        {isUser ? (
          <User className="h-4 w-4 text-white" />
        ) : message.isError ? (
          <AlertCircle className="h-4 w-4 text-red-300" />
        ) : (
          <Bot className="h-4 w-4 text-white" />
        )}
      </div>

      {/* Bubble */}
      <div
        className={cn(
          "max-w-[80%] rounded-2xl px-4 py-3 text-sm leading-relaxed",
          isUser
            ? "rounded-tr-sm bg-violet-700/40 text-gray-100"
            : message.isError
            ? "rounded-tl-sm bg-red-900/30 text-red-300 border border-red-800/40"
            : "rounded-tl-sm bg-gray-800/80 text-gray-200"
        )}
      >
        {formatContent(message.content)}
        <p
          className={cn(
            "mt-1.5 text-xs",
            isUser ? "text-right text-violet-400/60" : "text-gray-600"
          )}
        >
          {message.timestamp.toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
          })}
        </p>
      </div>
    </div>
  );
}
