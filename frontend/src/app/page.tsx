"use client";

import { useState, useCallback, useEffect } from "react";
import { useRouter } from "next/navigation";
import {
  FileText,
  Sparkles,
  MessageSquare,
  UploadCloud,
  ArrowRight,
  Loader2,
  Layers,
  ChevronDown,
  ChevronUp,
} from "lucide-react";
import Sidebar, { BatchHistoryItem } from "@/components/Sidebar";
import PdfUploader, { BatchSuccessData } from "@/components/PdfUploader";
import SummaryView from "@/components/SummaryView";
import ChatWindow from "@/components/ChatWindow";
import CommentSection from "@/components/CommentSection";
import ShareControls from "@/components/ShareControls";
import { cn } from "@/lib/utils";
import { useAuth } from "@/lib/AuthContext";
import { supabase } from "@/lib/supabaseClient";

const FASTAPI_URL = process.env.NEXT_PUBLIC_FASTAPI_URL || "http://127.0.0.1:8000";

export interface BatchDocument {
  id: string;
  filename: string;
  summary: string;
  word_count?: number | null;
  uploadedAt: string;
}

export interface UploadBatch {
  id: string;
  title: string;
  uploadedAt: string;
  documents: BatchDocument[];
}

type ActiveView = "upload" | "chat";

export default function DashboardPage() {
  const router = useRouter();
  const { user, loading } = useAuth();
  const [activeView, setActiveView] = useState<ActiveView>("upload");
  const [batches, setBatches] = useState<UploadBatch[]>([]);
  const [selectedBatchId, setSelectedBatchId] = useState<string | null>(null);
  const [showSummary, setShowSummary] = useState(false);
  const [authToken, setAuthToken] = useState<string | null>(null);
  const [isFetchingBatches, setIsFetchingBatches] = useState(false);

  /**
   * Fetch user's upload batch history from the backend.
   */
  const fetchBatches = useCallback(async (token: string) => {
    setIsFetchingBatches(true);
    try {
      const res = await fetch(`${FASTAPI_URL}/api/batches`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) return;

      const data: Array<{
        id: string;
        title: string;
        created_at: string;
        documents: Array<{
          id: string;
          file_name: string;
          created_at: string;
          summary?: string | null;
          word_count?: number | null;
        }>;
      }> = await res.json();

      setBatches((prev) => {
        // Retain any in-memory summaries if freshly uploaded
        const summaryLookup: Record<string, string> = {};
        prev.forEach((b) => {
          b.documents.forEach((d) => {
            if (d.summary) summaryLookup[d.id] = d.summary;
          });
        });

        return data.map((b) => ({
          id: b.id,
          title: b.title,
          uploadedAt: new Date(b.created_at).toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
          }),
          documents: (b.documents || []).map((d) => ({
            id: d.id,
            filename: d.file_name,
            uploadedAt: new Date(d.created_at).toLocaleTimeString([], {
              hour: "2-digit",
              minute: "2-digit",
            }),
            summary: d.summary ?? summaryLookup[d.id] ?? "",
            word_count: d.word_count,
          })),
        }));
      });
    } catch {
      // Non-fatal fallback
    } finally {
      setIsFetchingBatches(false);
    }
  }, []);

  // Keep access token updated
  useEffect(() => {
    supabase.auth.getSession().then(({ data: { session } }) => {
      const token = session?.access_token ?? null;
      setAuthToken(token);
      if (token) fetchBatches(token);
    });
    const { data: { subscription } } = supabase.auth.onAuthStateChange(
      (_event, session) => setAuthToken(session?.access_token ?? null)
    );
    return () => subscription.unsubscribe();
  }, [fetchBatches]);

  // Auth redirect
  useEffect(() => {
    if (!loading && !user) {
      router.replace("/auth");
    }
  }, [loading, user, router]);

  // Restore active view & selected batch from localStorage on mount
  useEffect(() => {
    try {
      const savedBatchId = localStorage.getItem("pdf_selected_batch_id");
      const savedView = localStorage.getItem("pdf_active_view") as ActiveView | null;
      if (savedBatchId) setSelectedBatchId(savedBatchId);
      if (savedView === "upload" || savedView === "chat") setActiveView(savedView);
    } catch {}
  }, []);

  // Persist selectedBatchId to localStorage
  useEffect(() => {
    try {
      if (selectedBatchId) {
        localStorage.setItem("pdf_selected_batch_id", selectedBatchId);
      } else {
        localStorage.removeItem("pdf_selected_batch_id");
      }
    } catch {}
  }, [selectedBatchId]);

  // Persist activeView to localStorage
  useEffect(() => {
    try {
      if (activeView) {
        localStorage.setItem("pdf_active_view", activeView);
      }
    } catch {}
  }, [activeView]);

  const handleUploadSuccess = useCallback(
    (data: BatchSuccessData) => {
      const formatted = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

      const newBatch: UploadBatch = {
        id: data.batch_id,
        title: data.title,
        uploadedAt: formatted,
        documents: data.documents.map((d) => ({
          id: d.document_id,
          filename: d.file_name,
          summary: d.summary,
          word_count: d.word_count,
          uploadedAt: formatted,
        })),
      };

      setBatches((prev) => [newBatch, ...prev.filter((b) => b.id !== newBatch.id)]);
      setSelectedBatchId(newBatch.id);
      setShowSummary(true);

      if (authToken) fetchBatches(authToken);
    },
    [authToken, fetchBatches]
  );

  const handleDeleteBatch = useCallback(
    async (batchId: string) => {
      if (!authToken) return;
      try {
        const res = await fetch(`${FASTAPI_URL}/api/batches/${batchId}`, {
          method: "DELETE",
          headers: { Authorization: `Bearer ${authToken}` },
        });
        if (!res.ok) return;
      } catch {
        return;
      }
      setBatches((prev) => prev.filter((b) => b.id !== batchId));
      setSelectedBatchId((prev) => (prev === batchId ? null : prev));
      setShowSummary((prev) => (selectedBatchId === batchId ? false : prev));
      try {
        localStorage.removeItem(`pdf_chat_batch_${batchId}`);
        if (selectedBatchId === batchId) {
          localStorage.removeItem("pdf_selected_batch_id");
        }
      } catch {}
    },
    [authToken, selectedBatchId]
  );

  if (loading || !user) {
    return (
      <div className="flex h-screen items-center justify-center bg-gray-950">
        <Loader2 className="h-8 w-8 animate-spin text-violet-500" />
      </div>
    );
  }

  const selectedBatch = batches.find((b) => b.id === selectedBatchId) ?? null;
  const primaryDocId = selectedBatch?.documents[0]?.id ?? null;

  const handleSelectHistory = (batchId: string) => {
    setSelectedBatchId(batchId);
    setShowSummary(true);
  };

  const uploadHistoryItems: BatchHistoryItem[] = batches.map((b) => ({
    id: b.id,
    title: b.title,
    uploadedAt: b.uploadedAt,
    documents: b.documents.map((d) => ({
      id: d.id,
      filename: d.filename,
      summary: d.summary,
      word_count: d.word_count,
    })),
  }));

  return (
    <div className="flex h-screen overflow-hidden bg-gray-950">
      {/* Sidebar */}
      <Sidebar
        activeView={activeView}
        onViewChange={setActiveView}
        uploadHistory={uploadHistoryItems}
        onSelectHistory={handleSelectHistory}
        onDeleteBatch={handleDeleteBatch}
        selectedBatchId={selectedBatchId}
        userEmail={user.email ?? ""}
        isLoadingHistory={isFetchingBatches}
      />

      {/* Main Content */}
      <main className="flex flex-1 flex-col min-w-0 overflow-hidden">
        {/* Top bar */}
        <header className="flex items-center justify-between px-6 py-4 border-b border-gray-800/60 flex-shrink-0 gap-4">
          <div className="min-w-0">
            <h1 className="text-lg font-semibold text-white">
              {activeView === "upload" ? "Document Analysis" : "Document Chat"}
            </h1>
            <p className="text-xs text-gray-500 truncate">
              {activeView === "upload"
                ? "Upload PDFs to extract insights and AI summaries"
                : selectedBatch && selectedBatch.documents.length > 1
                ? `Chatting across batch (${selectedBatch.documents.length} PDFs): ${selectedBatch.title}`
                : selectedBatch
                ? `Chatting with: ${selectedBatch.title}`
                : "Select an upload to begin chatting"}
            </p>
          </div>

          {/* Share controls for primary document */}
          {primaryDocId && authToken && (
            <div className="flex-shrink-0">
              <ShareControls
                key={primaryDocId}
                documentId={primaryDocId}
                authToken={authToken}
              />
            </div>
          )}

          {/* View toggle tabs */}
          <div className="flex items-center gap-1 rounded-xl bg-gray-900 border border-gray-800 p-1 flex-shrink-0">
            <TabButton
              icon={<UploadCloud className="h-3.5 w-3.5" />}
              label="Analyze"
              active={activeView === "upload"}
              onClick={() => setActiveView("upload")}
              id="tab-analyze"
            />
            <TabButton
              icon={<MessageSquare className="h-3.5 w-3.5" />}
              label="Chat"
              active={activeView === "chat"}
              onClick={() => setActiveView("chat")}
              disabled={!selectedBatchId}
              id="tab-chat"
            />
          </div>
        </header>

        {/* Content Area */}
        <div className="flex-1 overflow-y-auto p-6">
          {activeView === "upload" && (
            <UploadView
              onSuccess={handleUploadSuccess}
              selectedBatch={selectedBatch}
              showSummary={showSummary}
              onGoToChat={() => setActiveView("chat")}
              userId={user.id}
              authToken={authToken}
              ownerName={user.email ?? "You"}
            />
          )}

          {activeView === "chat" && selectedBatch ? (
            <div className="h-full" style={{ height: "calc(100vh - 130px)" }}>
              <ChatWindow
                batchId={selectedBatch.id}
                documentCount={selectedBatch.documents.length}
                filename={selectedBatch.title}
              />
            </div>
          ) : activeView === "chat" && !selectedBatch ? (
            <EmptyState
              icon={<MessageSquare className="h-12 w-12 text-gray-700" />}
              title="No Upload Selected"
              description="Upload PDFs first or choose one from the sidebar to begin chatting."
              action={
                <button
                  onClick={() => setActiveView("upload")}
                  className="flex items-center gap-2 rounded-xl bg-violet-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-violet-500 transition-colors"
                >
                  <UploadCloud className="h-4 w-4" />
                  Upload PDFs
                </button>
              }
            />
          ) : null}
        </div>
      </main>
    </div>
  );
}

/* ─── Sub-components ─────────────────────────────────────────────── */

interface UploadViewProps {
  onSuccess: (data: BatchSuccessData) => void;
  selectedBatch: UploadBatch | null;
  showSummary: boolean;
  onGoToChat: () => void;
  userId: string;
  authToken: string | null;
  ownerName: string;
}

function UploadView({
  onSuccess,
  selectedBatch,
  showSummary,
  onGoToChat,
  userId,
  authToken,
  ownerName,
}: UploadViewProps) {
  const [activeTabDocId, setActiveTabDocId] = useState<string | null>(null);

  // Sync active tab when batch changes
  useEffect(() => {
    if (selectedBatch && selectedBatch.documents.length > 0) {
      setActiveTabDocId(selectedBatch.documents[0].id);
    } else {
      setActiveTabDocId(null);
    }
  }, [selectedBatch]);

  const isMultiFile = (selectedBatch?.documents.length ?? 0) > 1;

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      {/* Welcome banner */}
      {!selectedBatch && (
        <div className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-violet-950/60 via-gray-900 to-indigo-950/60 border border-violet-800/30 p-8">
          <div className="absolute -top-10 -right-10 h-40 w-40 rounded-full bg-violet-600/10 blur-3xl" />
          <div className="absolute -bottom-10 -left-5 h-32 w-32 rounded-full bg-indigo-600/10 blur-3xl" />

          <div className="relative flex items-start gap-5">
            <div className="flex h-14 w-14 flex-shrink-0 items-center justify-center rounded-2xl bg-gradient-to-br from-violet-600 to-indigo-600 shadow-xl shadow-violet-900/40">
              <FileText className="h-7 w-7 text-white" />
            </div>
            <div>
              <h2 className="text-xl font-semibold text-white">
                Chat with any PDF — or multi-PDF batch
              </h2>
              <p className="mt-1.5 text-sm leading-relaxed text-gray-400">
                Upload one or multiple documents in a single action. Each PDF gets its own distinct AI summary,
                and the chatbot searches across the entire batch seamlessly.
              </p>
              <div className="mt-4 flex flex-wrap gap-3">
                {[
                  { icon: "📦", label: "Unified batch sessions" },
                  { icon: "📄", label: "Per-file summaries" },
                  { icon: "💬", label: "Cross-document RAG" },
                  { icon: "⚡", label: "Groq + Gemini powered" },
                ].map(({ icon, label }) => (
                  <span
                    key={label}
                    className="flex items-center gap-1.5 rounded-full bg-gray-800/60 border border-gray-700/50 px-3 py-1 text-xs font-medium text-gray-400"
                  >
                    <span>{icon}</span>
                    {label}
                  </span>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Uploader */}
      <PdfUploader onSuccess={onSuccess} userId={userId} />

      {/* Multi-file batch summaries view */}
      {showSummary && selectedBatch && isMultiFile && (
        <div className="space-y-4 animate-in fade-in slide-in-from-bottom-4 duration-500">
          <div className="flex items-center justify-between px-1">
            <div className="flex items-center gap-2">
              <Layers className="h-4 w-4 text-violet-400" />
              <p className="text-sm font-semibold text-white">
                Batch: {selectedBatch.title}
              </p>
              <span className="rounded-full bg-violet-900/50 px-2 py-0.5 text-xs text-violet-300">
                {selectedBatch.documents.length} files
              </span>
            </div>
          </div>

          {/* File selector tabs within the batch */}
          <div className="flex flex-wrap gap-1.5 p-1 rounded-xl bg-gray-900/90 border border-gray-800">
            {selectedBatch.documents.map((doc, idx) => {
              const isTabActive = (activeTabDocId || selectedBatch.documents[0].id) === doc.id;
              return (
                <button
                  key={doc.id}
                  onClick={() => setActiveTabDocId(doc.id)}
                  className={cn(
                    "flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium transition-all duration-150 max-w-[220px]",
                    isTabActive
                      ? "bg-violet-600 text-white shadow-md shadow-violet-900/30"
                      : "text-gray-400 hover:text-gray-200 hover:bg-gray-800/60"
                  )}
                >
                  <FileText className="h-3.5 w-3.5 flex-shrink-0" />
                  <span className="truncate">{doc.filename}</span>
                </button>
              );
            })}
          </div>

          {/* Render active document summary */}
          {(() => {
            const currentDoc =
              selectedBatch.documents.find((d) => d.id === activeTabDocId) ||
              selectedBatch.documents[0];
            if (!currentDoc) return null;
            return (
              <div key={currentDoc.id} className="space-y-4">
                <SummaryView
                  summary={currentDoc.summary}
                  filename={currentDoc.filename}
                />
                <CommentSection
                  mode="owner"
                  documentId={currentDoc.id}
                  authToken={authToken ?? undefined}
                  currentUserName={ownerName}
                  defaultCollapsed
                />
              </div>
            );
          })()}

          {/* CTA to chat across the batch */}
          <div className="flex justify-center pt-2">
            <button
              onClick={onGoToChat}
              id="go-to-chat-btn"
              className="group flex items-center gap-2.5 rounded-xl bg-gradient-to-r from-violet-600 to-indigo-600 px-6 py-3 text-sm font-semibold text-white shadow-lg shadow-violet-900/30 hover:shadow-violet-900/50 hover:scale-[1.02] transition-all duration-200"
            >
              <Sparkles className="h-4 w-4" />
              Chat with this Batch ({selectedBatch.documents.length} PDFs)
              <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" />
            </button>
          </div>
        </div>
      )}

      {/* Single-file batch summary */}
      {showSummary && selectedBatch && !isMultiFile && selectedBatch.documents[0] && (
        <div className="space-y-4 animate-in fade-in slide-in-from-bottom-4 duration-500">
          <SummaryView
            summary={selectedBatch.documents[0].summary}
            filename={selectedBatch.documents[0].filename}
          />

          <CommentSection
            mode="owner"
            documentId={selectedBatch.documents[0].id}
            authToken={authToken ?? undefined}
            currentUserName={ownerName}
            defaultCollapsed
          />

          <div className="flex justify-center">
            <button
              onClick={onGoToChat}
              id="go-to-chat-btn"
              className="group flex items-center gap-2.5 rounded-xl bg-gradient-to-r from-violet-600 to-indigo-600 px-6 py-3 text-sm font-semibold text-white shadow-lg shadow-violet-900/30 hover:shadow-violet-900/50 hover:scale-[1.02] transition-all duration-200"
            >
              <Sparkles className="h-4 w-4" />
              Chat with this Document
              <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function TabButton({
  icon,
  label,
  active,
  onClick,
  disabled,
  id,
}: {
  icon: React.ReactNode;
  label: string;
  active: boolean;
  onClick: () => void;
  disabled?: boolean;
  id: string;
}) {
  return (
    <button
      id={id}
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "flex items-center gap-1.5 rounded-lg px-3.5 py-1.5 text-xs font-medium transition-all duration-150",
        active
          ? "bg-violet-600 text-white shadow-sm"
          : "text-gray-400 hover:text-gray-200",
        disabled && "cursor-not-allowed opacity-40"
      )}
    >
      {icon}
      {label}
    </button>
  );
}

function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex h-full items-center justify-center">
      <div className="flex flex-col items-center gap-4 text-center max-w-sm">
        <div className="flex h-20 w-20 items-center justify-center rounded-2xl bg-gray-900 border border-gray-800">
          {icon}
        </div>
        <div>
          <h3 className="text-base font-semibold text-gray-300">{title}</h3>
          <p className="mt-1.5 text-sm text-gray-500">{description}</p>
        </div>
        {action}
      </div>
    </div>
  );
}