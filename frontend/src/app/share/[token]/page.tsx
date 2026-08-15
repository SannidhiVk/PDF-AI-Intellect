"use client";

/**
 * /share/[token]/page.tsx
 * ------------------------
 * Public shareable document page. No authentication required.
 * The share token in the URL is the access control.
 *
 * Three tabs:
 *   1. Summary — Document filename + AI-generated summary (read-only)
 *   2. Chat    — Full RAG AI chat via /api/share/{token}/chat
 *   3. Comments — Threaded comments + post form (guests welcome)
 */

import { useState, useEffect } from "react";
import { useParams } from "next/navigation";
import axios from "axios";
import {
  FileText,
  MessageSquare,
  MessageCircle,
  Sparkles,
  Loader2,
  AlertTriangle,
  Link2Off,
  BookOpen,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import ChatWindow from "@/components/ChatWindow";
import CommentSection from "@/components/CommentSection";
import { cn } from "@/lib/utils";

const FASTAPI_URL = process.env.NEXT_PUBLIC_FASTAPI_URL || "http://127.0.0.1:8000";

type Tab = "summary" | "chat" | "comments";

interface ShareInfo {
  document_id: string;
  file_name: string;
  summary: string | null;
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function SharePage() {
  const params = useParams();
  const token = Array.isArray(params.token) ? params.token[0] : (params.token as string);

  const [shareInfo, setShareInfo] = useState<ShareInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<Tab>("summary");

  useEffect(() => {
    if (!token) return;
    axios
      .get<ShareInfo>(`${FASTAPI_URL}/api/share/${token}`)
      .then((res) => setShareInfo(res.data))
      .catch((err) => {
        if (axios.isAxiosError(err) && err.response?.status === 404) {
          setError("This share link is no longer active or does not exist.");
        } else {
          setError("Failed to load the shared document. Please try again.");
        }
      })
      .finally(() => setLoading(false));
  }, [token]);

  // ── Loading ───────────────────────────────────────────────────────────────
  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center bg-gray-950">
        <div className="flex flex-col items-center gap-4">
          <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-violet-600 to-indigo-600 shadow-xl shadow-violet-900/40">
            <Loader2 className="h-7 w-7 text-white animate-spin" />
          </div>
          <p className="text-sm text-gray-400">Loading shared document…</p>
        </div>
      </div>
    );
  }

  // ── Revoked / Invalid ─────────────────────────────────────────────────────
  if (error) {
    return (
      <div className="flex h-screen items-center justify-center bg-gray-950 px-4">
        <div className="flex flex-col items-center gap-5 text-center max-w-sm">
          <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-red-900/30 border border-red-800/40">
            <Link2Off className="h-8 w-8 text-red-400" />
          </div>
          <div>
            <h1 className="text-lg font-semibold text-white">Link Not Available</h1>
            <p className="mt-2 text-sm text-gray-400">{error}</p>
          </div>
          <div className="flex items-center gap-2 rounded-xl bg-gray-900 border border-gray-800 px-4 py-3">
            <AlertTriangle className="h-4 w-4 text-amber-500 shrink-0" />
            <p className="text-xs text-gray-400 text-left">
              The owner may have revoked this link. Contact them for a new one.
            </p>
          </div>
        </div>
      </div>
    );
  }

  if (!shareInfo) return null;

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      {/* Top banner */}
      <header className="sticky top-0 z-20 flex items-center gap-4 px-6 py-4 border-b border-gray-800/60 bg-gray-950/90 backdrop-blur-sm">
        <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-violet-600 to-indigo-600 shadow-lg shadow-violet-900/30 shrink-0">
          <FileText className="h-4 w-4 text-white" />
        </div>
        <div className="min-w-0">
          <h1 className="text-sm font-semibold text-white truncate">{shareInfo.file_name}</h1>
          <p className="text-xs text-gray-500">Shared document — view only</p>
        </div>

        {/* Tab switcher */}
        <div className="ml-auto flex items-center gap-1 rounded-xl bg-gray-900 border border-gray-800 p-1 shrink-0">
          <TabBtn
            icon={<Sparkles className="h-3.5 w-3.5" />}
            label="Summary"
            active={activeTab === "summary"}
            onClick={() => setActiveTab("summary")}
          />
          <TabBtn
            icon={<MessageSquare className="h-3.5 w-3.5" />}
            label="Chat"
            active={activeTab === "chat"}
            onClick={() => setActiveTab("chat")}
          />
          <TabBtn
            icon={<MessageCircle className="h-3.5 w-3.5" />}
            label="Comments"
            active={activeTab === "comments"}
            onClick={() => setActiveTab("comments")}
          />
        </div>
      </header>

      {/* Content */}
      <main className="mx-auto max-w-3xl px-4 py-8">
        {activeTab === "summary" && (
          <PublicSummaryView
            token={token}
            filename={shareInfo.file_name}
            summary={shareInfo.summary}
          />
        )}

        {activeTab === "chat" && (
          <div style={{ height: "calc(100vh - 160px)" }}>
            <ChatWindow
              filename={shareInfo.file_name}
              shareToken={token}
            />
          </div>
        )}

        {activeTab === "comments" && (
          <CommentSection
            mode="shared"
            shareToken={token}
          />
        )}
      </main>
    </div>
  );
}

// ── Public Summary View ───────────────────────────────────────────────────────
// Displays the AI-generated summary fetched from /api/share/{token}.

function PublicSummaryView({
  token,
  filename,
  summary,
}: {
  token: string;
  filename: string;
  summary: string | null;
}) {
  return (
    <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
      {/* Document card */}
      <div className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-violet-950/60 via-gray-900 to-indigo-950/60 border border-violet-800/30 p-8">
        <div className="absolute -top-10 -right-10 h-40 w-40 rounded-full bg-violet-600/10 blur-3xl" />
        <div className="absolute -bottom-10 -left-5 h-32 w-32 rounded-full bg-indigo-600/10 blur-3xl" />

        <div className="relative flex items-start gap-5">
          <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl bg-gradient-to-br from-violet-600 to-indigo-600 shadow-xl shadow-violet-900/40">
            <FileText className="h-7 w-7 text-white" />
          </div>
          <div>
            <h2 className="text-xl font-semibold text-white">{filename}</h2>
            <p className="mt-1.5 text-sm text-gray-400 leading-relaxed">
              This document has been shared with you. Use the <strong className="text-gray-300">Chat</strong> tab to
              ask questions about its content, or the <strong className="text-gray-300">Comments</strong> tab to leave
              a note for the document owner.
            </p>
            <div className="mt-4 flex flex-wrap gap-3">
              {["AI Chat Enabled", "Guest Comments", "Read-Only"].map((feat) => (
                <span
                  key={feat}
                  className="flex items-center gap-1.5 rounded-full bg-gray-800/60 border border-gray-700/50 px-3 py-1 text-xs font-medium text-gray-400"
                >
                  <span className="h-1.5 w-1.5 rounded-full bg-violet-500" />
                  {feat}
                </span>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* Summary panel */}
      <div className="rounded-2xl border border-gray-800/60 bg-gray-900/60 backdrop-blur-sm overflow-hidden">
        <div className="flex items-center gap-3 px-6 py-4 border-b border-gray-800/60">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-amber-600 to-orange-600 shadow-lg shadow-amber-900/20">
            <Sparkles className="h-4 w-4 text-white" />
          </div>
          <div>
            <h3 className="text-sm font-semibold text-white">Document Summary</h3>
            <p className="text-xs text-gray-500 truncate max-w-xs">{filename}</p>
          </div>
        </div>
        <div className="px-6 py-6">
          {summary ? (
            <div className="prose prose-invert prose-sm max-w-none prose-headings:text-white prose-p:text-gray-300 prose-strong:text-white prose-li:text-gray-300 prose-hr:border-gray-800">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{summary}</ReactMarkdown>
            </div>
          ) : (
            <div className="text-center py-4">
              <BookOpen className="h-8 w-8 text-gray-700 mx-auto mb-3" />
              <p className="text-sm text-gray-400">
                No summary available. Use the <strong className="text-gray-300">Chat</strong> tab to ask the AI to
                summarise this document for you.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Tab Button ────────────────────────────────────────────────────────────────

function TabBtn({
  icon,
  label,
  active,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "flex items-center gap-1.5 rounded-lg px-3.5 py-1.5 text-xs font-medium transition-all duration-150",
        active
          ? "bg-violet-600 text-white shadow-sm"
          : "text-gray-400 hover:text-gray-200"
      )}
    >
      {icon}
      {label}
    </button>
  );
}
