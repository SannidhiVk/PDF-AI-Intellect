"use client";

import { useState } from "react";
import { FileText, MessageSquare, History, LogOut, Brain, ChevronRight, User, Loader2, Trash2, Search, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { cn } from "@/lib/utils";
import { supabase } from "@/lib/supabaseClient";

interface SidebarProps {
  activeView: "upload" | "chat";
  onViewChange: (view: "upload" | "chat") => void;
  uploadHistory: { id: string; filename: string; uploadedAt: string }[];
  onSelectHistory: (id: string) => void;
  /** Called when the user confirms deletion of a document. */
  onDeleteDocument: (id: string) => Promise<void>;
  selectedDocumentId: string | null;
  userEmail: string;
  /** When true, show a loading spinner in the history section. */
  isLoadingHistory?: boolean;
}

export default function Sidebar({
  activeView,
  onViewChange,
  uploadHistory,
  onSelectHistory,
  onDeleteDocument,
  selectedDocumentId,
  userEmail,
  isLoadingHistory = false,
}: SidebarProps) {
  const router = useRouter();
  // Track which doc ID is currently being deleted so we can show a spinner
  const [deletingId, setDeletingId] = useState<string | null>(null);
  // Search/filter query for the document history
  const [searchQuery, setSearchQuery] = useState("");

  const handleSignOut = async () => {
    await supabase.auth.signOut();
    router.replace("/auth");
  };

  const handleDelete = async (e: React.MouseEvent, id: string) => {
    // Prevent the click from bubbling up to the row's select handler
    e.stopPropagation();
    setDeletingId(id);
    try {
      await onDeleteDocument(id);
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <aside className="flex h-screen w-64 flex-shrink-0 flex-col bg-gray-950 border-r border-gray-800/60">
      {/* Logo */}
      <div className="flex items-center gap-3 px-5 py-5 border-b border-gray-800/60">
        <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-violet-600 to-indigo-600 shadow-lg shadow-violet-900/30">
          <Brain className="h-5 w-5 text-white" />
        </div>
        <div>
          <h1 className="text-sm font-semibold text-white tracking-wide">PDF Intellect</h1>
          <p className="text-xs text-gray-500">AI Document Assistant</p>
        </div>
      </div>

      {/* Main Navigation */}
      <nav className="px-3 py-4 space-y-1">
        <p className="px-2 pb-2 text-xs font-medium uppercase tracking-widest text-gray-600">
          Workspace
        </p>
        <NavButton
          icon={<FileText className="h-4 w-4" />}
          label="Upload & Analyze"
          active={activeView === "upload"}
          onClick={() => onViewChange("upload")}
        />
        <NavButton
          icon={<MessageSquare className="h-4 w-4" />}
          label="Chat with PDF"
          active={activeView === "chat"}
          onClick={() => onViewChange("chat")}
          disabled={!selectedDocumentId}
          tooltip={!selectedDocumentId ? "Upload a PDF first" : undefined}
        />
      </nav>

      {/* History */}
      <div className="flex-1 overflow-y-auto px-3 py-2">
        <div className="flex items-center gap-2 px-2 pb-2">
          <History className="h-3.5 w-3.5 text-gray-600" />
          <p className="text-xs font-medium uppercase tracking-widest text-gray-600">
            Recent Documents
          </p>
        </div>

        {/* Search input */}
        {uploadHistory.length > 0 && (
          <div className="relative mb-2">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-600" />
            <input
              id="doc-search-input"
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search documents…"
              className="w-full rounded-lg border border-gray-800 bg-gray-900/60 pl-8 pr-7 py-1.5 text-xs text-gray-300 placeholder-gray-600 focus:border-violet-700 focus:outline-none focus:ring-1 focus:ring-violet-700/40 transition-colors"
            />
            {searchQuery && (
              <button
                onClick={() => setSearchQuery("")}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-600 hover:text-gray-400 transition-colors"
                title="Clear search"
              >
                <X className="h-3 w-3" />
              </button>
            )}
          </div>
        )}

        {isLoadingHistory ? (
          <div className="px-2 py-4 flex items-center gap-2 text-gray-600">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            <p className="text-xs">Loading documents…</p>
          </div>
        ) : uploadHistory.length === 0 ? (
          <div className="px-2 py-4 text-center">
            <p className="text-xs text-gray-600">No documents yet</p>
          </div>
        ) : (() => {
          const filtered = uploadHistory.filter((item) =>
            item.filename.toLowerCase().includes(searchQuery.toLowerCase())
          );
          if (filtered.length === 0) {
            return (
              <div className="px-2 py-4 text-center">
                <p className="text-xs text-gray-500">No results for &ldquo;{searchQuery}&rdquo;</p>
              </div>
            );
          }
          return (
            <ul className="space-y-0.5">
              {filtered.map((item) => (
              <li key={item.id} className="group relative">
                <button
                  onClick={() => onSelectHistory(item.id)}
                  className={cn(
                    "w-full flex items-center gap-2.5 rounded-lg px-2.5 py-2 pr-8 text-left transition-all duration-150",
                    selectedDocumentId === item.id
                      ? "bg-violet-600/15 text-violet-300"
                      : "text-gray-400 hover:bg-gray-800/50 hover:text-gray-200"
                  )}
                >
                  <FileText className="h-3.5 w-3.5 flex-shrink-0" />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-xs font-medium">{item.filename}</p>
                    <p className="text-xs text-gray-600">{item.uploadedAt}</p>
                  </div>
                  {selectedDocumentId === item.id && deletingId !== item.id && (
                    <ChevronRight className="h-3 w-3 flex-shrink-0 text-violet-400" />
                  )}
                </button>

                {/* Delete button — visible on hover or while deleting */}
                <button
                  onClick={(e) => handleDelete(e, item.id)}
                  disabled={deletingId === item.id}
                  title="Delete document"
                  className={cn(
                    "absolute right-1.5 top-1/2 -translate-y-1/2",
                    "flex h-6 w-6 items-center justify-center rounded-md",
                    "text-gray-600 transition-all duration-150",
                    "opacity-0 group-hover:opacity-100",
                    deletingId === item.id && "opacity-100",
                    deletingId === item.id
                      ? "text-red-400"
                      : "hover:bg-red-900/30 hover:text-red-400",
                    "disabled:cursor-not-allowed"
                  )}
                >
                  {deletingId === item.id ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Trash2 className="h-3.5 w-3.5" />
                  )}
                </button>
              </li>
            ))}
          </ul>
          );
        })()}
      </div>

      {/* Footer — user info + sign out */}
      <div className="border-t border-gray-800/60 p-3 space-y-1">
        {/* User email strip */}
        {userEmail && (
          <div className="flex items-center gap-2.5 rounded-lg px-3 py-2 mb-1">
            <div className="flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-violet-700 to-indigo-700">
              <User className="h-3.5 w-3.5 text-white" />
            </div>
            <p className="truncate text-xs text-gray-500" title={userEmail}>
              {userEmail}
            </p>
          </div>
        )}
        <NavButton
          icon={<LogOut className="h-4 w-4" />}
          label="Sign Out"
          active={false}
          onClick={handleSignOut}
          danger
        />
      </div>
    </aside>
  );
}

interface NavButtonProps {
  icon: React.ReactNode;
  label: string;
  active: boolean;
  onClick: () => void;
  disabled?: boolean;
  danger?: boolean;
  tooltip?: string;
}

function NavButton({ icon, label, active, onClick, disabled, danger, tooltip }: NavButtonProps) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={tooltip}
      className={cn(
        "w-full flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-all duration-150",
        active
          ? "bg-violet-600/20 text-violet-300 shadow-sm"
          : danger
          ? "text-gray-500 hover:bg-red-900/20 hover:text-red-400"
          : "text-gray-400 hover:bg-gray-800/50 hover:text-gray-200",
        disabled && "cursor-not-allowed opacity-40"
      )}
    >
      {icon}
      {label}
    </button>
  );
}

