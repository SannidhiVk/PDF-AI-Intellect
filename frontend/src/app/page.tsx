"use client";

import { useState, useCallback, useEffect } from "react";
import { useRouter } from "next/navigation";
import { FileText, Sparkles, MessageSquare, UploadCloud, ArrowRight, Loader2 } from "lucide-react";
import Sidebar from "@/components/Sidebar";
import PdfUploader from "@/components/PdfUploader";
import SummaryView from "@/components/SummaryView";
import ChatWindow from "@/components/ChatWindow";
import CommentSection from "@/components/CommentSection";
import { cn } from "@/lib/utils";
import { useAuth } from "@/lib/AuthContext";
import { supabase } from "@/lib/supabaseClient";

const FASTAPI_URL = process.env.NEXT_PUBLIC_FASTAPI_URL || "http://127.0.0.1:8000";

interface UploadedDocument {
  id: string;
  filename: string;
  summary: string;
  uploadedAt: string;
}

type ActiveView = "upload" | "chat";

export default function DashboardPage() {
  const router = useRouter();
  const { user, loading } = useAuth();
  const [activeView, setActiveView] = useState<ActiveView>("upload");
  const [documents, setDocuments] = useState<UploadedDocument[]>([]);
  const [selectedDocId, setSelectedDocId] = useState<string | null>(null);
  const [showSummary, setShowSummary] = useState(false);
  const [authToken, setAuthToken] = useState<string | null>(null);
  const [isFetchingDocs, setIsFetchingDocs] = useState(false);

  /**
   * Fetch the user's document history from the backend.
   * Called on mount (to restore sidebar after refresh) and after each upload.
   * Uses the JWT so the backend can scope results to this user only.
   */
  const fetchDocuments = useCallback(async (token: string) => {
    setIsFetchingDocs(true);
    try {
      const res = await fetch(`${FASTAPI_URL}/api/documents`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) return;
      const data: Array<{ id: string; file_name: string; created_at: string; summary?: string | null }> = await res.json();
      setDocuments((prev) => {
        // Build a lookup of existing summaries so a just-uploaded doc's
        // freshly-generated summary is not lost if the DB hasn't persisted
        // yet when this refetch fires.
        const existingSummaries: Record<string, string> = {};
        prev.forEach((d) => { if (d.summary) existingSummaries[d.id] = d.summary; });

        return data.map((d) => ({
          id: d.id,
          filename: d.file_name,
          // ISO timestamp → readable time (e.g. "14:32")
          uploadedAt: new Date(d.created_at).toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
          }),
          // Prefer: DB summary → existing in-memory summary → empty string
          summary: d.summary ?? existingSummaries[d.id] ?? "",
        }));
      });
    } catch {
      // Non-fatal: sidebar will just be empty until next upload
    } finally {
      setIsFetchingDocs(false);
    }
  }, []);

  // Keep the access token fresh for Share/Comment calls
  useEffect(() => {
    supabase.auth.getSession().then(({ data: { session } }) => {
      const token = session?.access_token ?? null;
      setAuthToken(token);
      // Restore sidebar history from the API now that we have a token
      if (token) fetchDocuments(token);
    });
    const { data: { subscription } } = supabase.auth.onAuthStateChange(
      (_event, session) => setAuthToken(session?.access_token ?? null)
    );
    return () => subscription.unsubscribe();
  }, [fetchDocuments]);

  // Redirect to /auth if not authenticated
  useEffect(() => {
    if (!loading && !user) {
      router.replace("/auth");
    }
  }, [loading, user, router]);

  // ── All hooks MUST be declared unconditionally, before any early return ──
  // (moved up from below the loading/user guard to fix "change in order of Hooks")
  const handleUploadSuccess = useCallback(
    (data: { document_id: string; summary: string; filename: string }) => {
      const now = new Date();
      const formatted = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

      const newDoc: UploadedDocument = {
        id: data.document_id,
        filename: data.filename,
        summary: data.summary,
        uploadedAt: formatted,
      };

      // Prepend the new upload and deduplicate (id is the key)
      setDocuments((prev) => {
        const without = prev.filter((d) => d.id !== newDoc.id);
        return [newDoc, ...without];
      });
      setSelectedDocId(newDoc.id);
      setShowSummary(true);

      // Refresh the full list so the sidebar stays consistent with the DB
      if (authToken) fetchDocuments(authToken);
    },
    [authToken, fetchDocuments]
  );

  const handleDeleteDocument = useCallback(
    async (id: string) => {
      if (!authToken) return;
      try {
        const res = await fetch(`${FASTAPI_URL}/api/documents/${id}`, {
          method: "DELETE",
          headers: { Authorization: `Bearer ${authToken}` },
        });
        if (!res.ok) return; // silently ignore — doc stays in sidebar
      } catch {
        return; // network error — leave the list unchanged
      }
      // Remove from local state immediately (no need to refetch)
      setDocuments((prev) => prev.filter((d) => d.id !== id));
      // If the deleted doc was selected, clear the selection
      setSelectedDocId((prev) => (prev === id ? null : prev));
      setShowSummary((prev) => (selectedDocId === id ? false : prev));
    },
    [authToken, selectedDocId]
  );

  // Show full-screen spinner while session is being resolved.
  // This early return now happens AFTER all hooks are called, so hook
  // order/count stays identical across the loading -> authenticated transition.
  if (loading || !user) {
    return (
      <div className="flex h-screen items-center justify-center bg-gray-950">
        <Loader2 className="h-8 w-8 animate-spin text-violet-500" />
      </div>
    );
  }

  // Get the currently selected document (plain derived value, not a hook —
  // safe to compute after the guard)
  const selectedDocument = documents.find((d) => d.id === selectedDocId) ?? null;

  const handleSelectHistory = (id: string) => {
    setSelectedDocId(id);
    setShowSummary(true);
  };

  return (
    <div className="flex h-screen overflow-hidden bg-gray-950">
      {/* Sidebar */}
      <Sidebar
        activeView={activeView}
        onViewChange={setActiveView}
        uploadHistory={documents.map((d) => ({
          id: d.id,
          filename: d.filename,
          uploadedAt: d.uploadedAt,
        }))}
        onSelectHistory={handleSelectHistory}
        onDeleteDocument={handleDeleteDocument}
        selectedDocumentId={selectedDocId}
        userEmail={user.email ?? ""}
        isLoadingHistory={isFetchingDocs}
      />

      {/* Main Content */}
      <main className="flex flex-1 flex-col min-w-0 overflow-hidden">
        {/* Top bar */}
        <header className="flex items-center justify-between px-6 py-4 border-b border-gray-800/60 flex-shrink-0">
          <div>
            <h1 className="text-lg font-semibold text-white">
              {activeView === "upload" ? "Document Analysis" : "Document Chat"}
            </h1>
            <p className="text-xs text-gray-500">
              {activeView === "upload"
                ? "Upload a PDF to extract insights and AI summaries"
                : selectedDocument
                ? `Chatting with: ${selectedDocument.filename}`
                : "Select a document to begin chatting"}
            </p>
          </div>

          {/* View toggle tabs */}
          <div className="flex items-center gap-1 rounded-xl bg-gray-900 border border-gray-800 p-1">
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
              disabled={!selectedDocId}
              id="tab-chat"
            />
          </div>
        </header>

        {/* Content Area */}
        <div className="flex-1 overflow-y-auto p-6">
          {activeView === "upload" && (
            <UploadView
              onSuccess={handleUploadSuccess}
              selectedDocument={selectedDocument}
              showSummary={showSummary}
              onGoToChat={() => setActiveView("chat")}
              userId={user.id}
              authToken={authToken}
              ownerName={user.email ?? "You"}
            />
          )}

          {activeView === "chat" && selectedDocument ? (
            <div className="h-full" style={{ height: "calc(100vh - 130px)" }}>
              <ChatWindow
                documentId={selectedDocument.id}
                filename={selectedDocument.filename}
              />
            </div>
          ) : activeView === "chat" && !selectedDocument ? (
            <EmptyState
              icon={<MessageSquare className="h-12 w-12 text-gray-700" />}
              title="No Document Selected"
              description="Upload a PDF first, then come back here to start chatting."
              action={
                <button
                  onClick={() => setActiveView("upload")}
                  className="flex items-center gap-2 rounded-xl bg-violet-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-violet-500 transition-colors"
                >
                  <UploadCloud className="h-4 w-4" />
                  Upload a PDF
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
  onSuccess: (data: { document_id: string; summary: string; filename: string }) => void;
  selectedDocument: UploadedDocument | null;
  showSummary: boolean;
  onGoToChat: () => void;
  userId: string;
  authToken: string | null;
  ownerName: string;
}

function UploadView({ onSuccess, selectedDocument, showSummary, onGoToChat, userId, authToken, ownerName }: UploadViewProps) {
  return (
    <div className="mx-auto max-w-3xl space-y-6">
      {/* Welcome banner — only shown when no document uploaded */}
      {!selectedDocument && (
        <div className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-violet-950/60 via-gray-900 to-indigo-950/60 border border-violet-800/30 p-8">
          {/* Background glow */}
          <div className="absolute -top-10 -right-10 h-40 w-40 rounded-full bg-violet-600/10 blur-3xl" />
          <div className="absolute -bottom-10 -left-5 h-32 w-32 rounded-full bg-indigo-600/10 blur-3xl" />

          <div className="relative flex items-start gap-5">
            <div className="flex h-14 w-14 flex-shrink-0 items-center justify-center rounded-2xl bg-gradient-to-br from-violet-600 to-indigo-600 shadow-xl shadow-violet-900/40">
              <FileText className="h-7 w-7 text-white" />
            </div>
            <div>
              <h2 className="text-xl font-semibold text-white">
                Chat with any PDF — instantly
              </h2>
              <p className="mt-1.5 text-sm leading-relaxed text-gray-400">
                Upload a document and ask questions in plain English. Get accurate answers,
                key highlights, and a full summary — no technical knowledge needed.
              </p>
              <div className="mt-4 flex flex-wrap gap-3">
                {[
                  { icon: "📄", label: "Instant summary" },
                  { icon: "💬", label: "Ask anything" },
                  { icon: "🔍", label: "Find key info fast" },
                  { icon: "📚", label: "Multiple documents" },
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

      {/* Summary — shown after upload */}
      {showSummary && selectedDocument && (
        <div className="space-y-4 animate-in fade-in slide-in-from-bottom-4 duration-500">
          <SummaryView
            summary={selectedDocument.summary}
            filename={selectedDocument.filename}
            documentId={selectedDocument.id}
            authToken={authToken ?? undefined}
          />

          {/* Comments — owner view */}
          <CommentSection
            mode="owner"
            documentId={selectedDocument.id}
            authToken={authToken ?? undefined}
            currentUserName={ownerName}
            defaultCollapsed
          />

          {/* CTA to chat */}
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