"use client";

import { useState } from "react";
import {
  FileText,
  MessageSquare,
  History,
  LogOut,
  Brain,
  ChevronRight,
  User,
  Loader2,
  Trash2,
  Search,
  X,
  Layers,
  FolderOpen,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { cn } from "@/lib/utils";
import { supabase } from "@/lib/supabaseClient";

export interface BatchHistoryItem {
  id: string;
  title: string;
  uploadedAt: string;
  documents: Array<{
    id: string;
    filename: string;
    summary?: string | null;
    word_count?: number | null;
  }>;
}

interface SidebarProps {
  activeView: "upload" | "chat";
  onViewChange: (view: "upload" | "chat") => void;
  uploadHistory: BatchHistoryItem[];
  onSelectHistory: (batchId: string) => void;
  /** Called when the user confirms deletion of a batch. */
  onDeleteBatch: (batchId: string) => Promise<void>;
  selectedBatchId: string | null;
  userEmail: string;
  /** When true, show a loading spinner in the history section. */
  isLoadingHistory?: boolean;
}

export default function Sidebar({
  activeView,
  onViewChange,
  uploadHistory,
  onSelectHistory,
  onDeleteBatch,
  selectedBatchId,
  userEmail,
  isLoadingHistory = false,
}: SidebarProps) {
  const router = useRouter();
  // Track which batch ID is currently being deleted so we can show a spinner
  const [deletingId, setDeletingId] = useState<string | null>(null);
  // Search/filter query for the batch history
  const [searchQuery, setSearchQuery] = useState("");

  const handleSignOut = async () => {
    await supabase.auth.signOut();
    router.replace("/auth");
  };

  const handleDelete = async (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    setDeletingId(id);
    try {
      await onDeleteBatch(id);
    } finally {
      setDeletingId(null);
    }
  };

  const selectedBatch = uploadHistory.find((b) => b.id === selectedBatchId);
  const docCountInSelected = selectedBatch?.documents.length || 0;

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
          label={
            docCountInSelected > 1
              ? `Chat (${docCountInSelected} docs)`
              : "Chat with PDF"
          }
          active={activeView === "chat"}
          onClick={() => onViewChange("chat")}
          disabled={!selectedBatchId}
          tooltip={!selectedBatchId ? "Upload or select a document first" : undefined}
        />
      </nav>

      {/* History */}
      <div className="flex-1 overflow-y-auto px-3 py-2">
        <div className="flex items-center gap-2 px-2 pb-2">
          <History className="h-3.5 w-3.5 text-gray-600" />
          <p className="text-xs font-medium uppercase tracking-widest text-gray-600">
            Recent Uploads
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
              placeholder="Search uploads…"
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
            <p className="text-xs">Loading uploads…</p>
          </div>
        ) : uploadHistory.length === 0 ? (
          <div className="px-2 py-4 text-center">
            <p className="text-xs text-gray-600">No uploads yet</p>
          </div>
        ) : (() => {
          const q = searchQuery.toLowerCase();
          const filtered = uploadHistory.filter((item) =>
            item.title.toLowerCase().includes(q) ||
            item.documents.some((d) => d.filename.toLowerCase().includes(q))
          );
          if (filtered.length === 0) {
            return (
              <div className="px-2 py-4 text-center">
                <p className="text-xs text-gray-500">No results for &ldquo;{searchQuery}&rdquo;</p>
              </div>
            );
          }
          return (
            <ul className="space-y-1">
              {filtered.map((item) => {
                const isSelected = selectedBatchId === item.id;
                const isMulti = item.documents.length > 1;

                return (
                  <li key={item.id} className="group relative">
                    <button
                      onClick={() => onSelectHistory(item.id)}
                      className={cn(
                        "w-full flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-left transition-all duration-150 pr-8",
                        isSelected
                          ? "bg-violet-600/15 text-violet-300 border border-violet-700/30"
                          : "text-gray-400 hover:bg-gray-800/50 hover:text-gray-200 border border-transparent"
                      )}
                    >
                      {isMulti ? (
                        <div className="flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-md bg-violet-950/80 text-violet-400 border border-violet-800/40">
                          <Layers className="h-3.5 w-3.5" />
                        </div>
                      ) : (
                        <FileText className="h-3.5 w-3.5 flex-shrink-0 text-gray-500" />
                      )}

                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-1.5">
                          <p className="truncate text-xs font-medium">{item.title}</p>
                        </div>
                        <div className="flex items-center gap-2 text-[11px] text-gray-600">
                          <span>{item.uploadedAt}</span>
                          {isMulti && (
                            <span className="rounded bg-violet-900/40 px-1 py-0.2 text-[10px] font-medium text-violet-300">
                              {item.documents.length} files
                            </span>
                          )}
                        </div>
                      </div>

                      {isSelected && deletingId !== item.id && (
                        <ChevronRight className="h-3 w-3 flex-shrink-0 text-violet-400" />
                      )}
                    </button>

                    {/* Delete button */}
                    <button
                      onClick={(e) => handleDelete(e, item.id)}
                      disabled={deletingId === item.id}
                      title="Delete upload batch"
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
                );
              })}
            </ul>
          );
        })()}
      </div>

      {/* Footer — user info + sign out */}
      <div className="border-t border-gray-800/60 p-3 space-y-1">
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
