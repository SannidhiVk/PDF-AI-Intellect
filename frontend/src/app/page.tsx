"use client";

import { useState, useCallback } from "react";
import { FileText, Sparkles, MessageSquare, UploadCloud, ArrowRight } from "lucide-react";
import Sidebar from "@/components/Sidebar";
import PdfUploader from "@/components/PdfUploader";
import SummaryView from "@/components/SummaryView";
import ChatWindow from "@/components/ChatWindow";
import { cn } from "@/lib/utils";

interface UploadedDocument {
  id: string;
  filename: string;
  summary: string;
  uploadedAt: string;
}

type ActiveView = "upload" | "chat";

export default function DashboardPage() {
  const [activeView, setActiveView] = useState<ActiveView>("upload");
  const [documents, setDocuments] = useState<UploadedDocument[]>([]);
  const [selectedDocId, setSelectedDocId] = useState<string | null>(null);
  const [showSummary, setShowSummary] = useState(false);

  // Get the currently selected document
  const selectedDocument = documents.find((d) => d.id === selectedDocId) ?? null;

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

      setDocuments((prev) => [newDoc, ...prev]);
      setSelectedDocId(newDoc.id);
      setShowSummary(true);
    },
    []
  );

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
        selectedDocumentId={selectedDocId}
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
}

function UploadView({ onSuccess, selectedDocument, showSummary, onGoToChat }: UploadViewProps) {
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
                Analyze Any PDF with AI
              </h2>
              <p className="mt-1.5 text-sm leading-relaxed text-gray-400">
                Upload your document below. Our AI will extract the text, chunk it intelligently,
                generate vector embeddings, and produce a comprehensive summary — all in seconds.
              </p>
              <div className="mt-4 flex flex-wrap gap-3">
                {["Auto Summary", "Semantic Search", "RAG Chat", "Multi-doc History"].map((feat) => (
                  <FeaturePill key={feat} label={feat} />
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Uploader */}
      <PdfUploader onSuccess={onSuccess} />

      {/* Summary — shown after upload */}
      {showSummary && selectedDocument && (
        <div className="space-y-4 animate-in fade-in slide-in-from-bottom-4 duration-500">
          <SummaryView
            summary={selectedDocument.summary}
            filename={selectedDocument.filename}
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

function FeaturePill({ label }: { label: string }) {
  return (
    <span className="flex items-center gap-1.5 rounded-full bg-gray-800/60 border border-gray-700/50 px-3 py-1 text-xs font-medium text-gray-400">
      <span className="h-1.5 w-1.5 rounded-full bg-violet-500" />
      {label}
    </span>
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
