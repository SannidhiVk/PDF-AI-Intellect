"use client";

import { useState, useCallback, useRef } from "react";
import axios from "axios";
import {
  UploadCloud,
  FileText,
  CheckCircle2,
  XCircle,
  Loader2,
  Sparkles,
  Plus,
  AlertTriangle,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { supabase } from "@/lib/supabaseClient";

const FASTAPI_URL = process.env.NEXT_PUBLIC_FASTAPI_URL || "http://127.0.0.1:8000";

type FileStatus = "idle" | "processing" | "success" | "error";

interface FileEntry {
  file: File;
  status: FileStatus;
  error?: string;
}

export interface BatchSuccessData {
  batch_id: string;
  title: string;
  created_at: string;
  documents: Array<{
    document_id: string;
    file_name: string;
    summary: string;
    chunk_count: number;
    word_count?: number | null;
  }>;
  failed?: Array<{ filename: string; error: string }>;
}

interface PdfUploaderProps {
  onSuccess: (data: BatchSuccessData) => void;
  userId: string;
}

export default function PdfUploader({ onSuccess, userId }: PdfUploaderProps) {
  const [queue, setQueue] = useState<FileEntry[]>([]);
  const [isDragOver, setIsDragOver] = useState(false);
  const [isRunning, setIsRunning] = useState(false);
  const [partialWarnings, setPartialWarnings] = useState<Array<{ filename: string; error: string }>>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // ── Process all queued files in ONE batch request ──────────────────────────
  const processBatchQueue = useCallback(
    async (entries: FileEntry[]) => {
      setIsRunning(true);
      setPartialWarnings([]);

      const { data: { session } } = await supabase.auth.getSession();
      const token = session?.access_token;

      if (!token) {
        setQueue((prev) =>
          prev.map((e) => ({ ...e, status: "error", error: "Not signed in. Please refresh." }))
        );
        setIsRunning(false);
        return;
      }

      // Mark all idle files as processing
      setQueue((prev) =>
        prev.map((e) => (e.status === "idle" ? { ...e, status: "processing" } : e))
      );

      const formData = new FormData();
      entries.forEach((entry) => {
        formData.append("files", entry.file);
      });

      try {
        const response = await axios.post<BatchSuccessData>(
          `${FASTAPI_URL}/api/process-batch`,
          formData,
          {
            headers: {
              "Content-Type": "multipart/form-data",
              Authorization: `Bearer ${token}`,
            },
            timeout: 300000, // 5 minutes for multi-file batches
          }
        );

        const resData = response.data;
        const succeededNames = new Set(resData.documents.map((d) => d.file_name));
        const failedMap = new Map((resData.failed || []).map((f) => [f.filename, f.error]));

        setQueue((prev) =>
          prev.map((e) => {
            if (succeededNames.has(e.file.name)) {
              return { ...e, status: "success" };
            }
            if (failedMap.has(e.file.name)) {
              return { ...e, status: "error", error: failedMap.get(e.file.name) };
            }
            return { ...e, status: "success" };
          })
        );

        if (resData.failed && resData.failed.length > 0) {
          setPartialWarnings(resData.failed);
        }

        onSuccess(resData);
      } catch (err: unknown) {
        let msg = "Failed to process PDF batch. Please try again.";
        if (axios.isAxiosError(err)) {
          const detail = err.response?.data?.detail;
          if (typeof detail === "string") msg = detail;
          else if (err.response?.status === 504) msg = "Server timed out processing PDFs. Try smaller files.";
          else if (!err.response) msg = "Cannot reach the backend server.";
        }
        setQueue((prev) =>
          prev.map((e) => ({ ...e, status: "error", error: msg }))
        );
      } finally {
        setIsRunning(false);
      }
    },
    [onSuccess]
  );

  // ── Add files from drop or input ───────────────────────────────────────────
  const addFiles = useCallback(
    (files: FileList | File[]) => {
      const valid = Array.from(files).filter(
        (f) => f.type === "application/pdf" || f.name.toLowerCase().endsWith(".pdf")
      ).filter((f) => f.size <= 50 * 1024 * 1024);

      if (valid.length === 0) return;

      const newEntries: FileEntry[] = valid.map((f) => ({ file: f, status: "idle" }));

      setQueue(newEntries);
      setPartialWarnings([]);
      setTimeout(() => processBatchQueue(newEntries), 0);
    },
    [processBatchQueue]
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragOver(false);
      if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
    },
    [addFiles]
  );

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files?.length) {
      addFiles(e.target.files);
      e.target.value = "";
    }
  };

  const handleReset = () => {
    if (isRunning) return;
    setQueue([]);
    setPartialWarnings([]);
  };

  const allDone =
    queue.length > 0 && queue.every((e) => e.status === "success" || e.status === "error");
  const successCount = queue.filter((e) => e.status === "success").length;
  const anyError = queue.some((e) => e.status === "error");

  return (
    <div className="w-full space-y-4">
      {/* Drop Zone */}
      <div
        onDrop={handleDrop}
        onDragOver={(e) => { e.preventDefault(); setIsDragOver(true); }}
        onDragLeave={() => setIsDragOver(false)}
        onClick={() => !isRunning && fileInputRef.current?.click()}
        className={cn(
          "relative flex flex-col items-center justify-center rounded-2xl border-2 border-dashed p-10 transition-all duration-300 cursor-pointer select-none",
          isDragOver
            ? "border-violet-500 bg-violet-500/10 scale-[1.02]"
            : isRunning
            ? "border-gray-700 bg-gray-900/40 cursor-wait pointer-events-none"
            : "border-gray-700 bg-gray-900/40 hover:border-violet-600/60 hover:bg-violet-900/10"
        )}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept="application/pdf"
          multiple
          onChange={handleInputChange}
          className="hidden"
          id="pdf-upload-input"
        />

        {isRunning ? (
          <div className="flex flex-col items-center gap-4 text-center">
            <div className="relative flex h-16 w-16 items-center justify-center">
              <div className="absolute inset-0 rounded-full border-4 border-gray-800" />
              <div
                className="absolute inset-0 rounded-full border-4 border-transparent border-t-violet-500 border-r-violet-400/40 animate-spin"
                style={{ animationDuration: "0.9s" }}
              />
              <Sparkles className="h-6 w-6 text-violet-400" />
            </div>
            <div>
              <p className="text-sm font-semibold text-gray-200">
                Processing {queue.length} file{queue.length !== 1 ? "s" : ""} in batch…
              </p>
              <p className="mt-1 text-xs text-gray-500">Extracting, embedding & generating summaries</p>
            </div>
          </div>
        ) : (
          <div className="flex flex-col items-center gap-4 text-center">
            <div className={cn(
              "flex h-16 w-16 items-center justify-center rounded-2xl transition-all duration-300",
              isDragOver ? "bg-violet-600/30 scale-110" : "bg-gray-800"
            )}>
              <UploadCloud className={cn(
                "h-8 w-8 transition-colors duration-300",
                isDragOver ? "text-violet-400" : "text-gray-500"
              )} />
            </div>
            <div>
              <p className="text-sm font-semibold text-gray-200">
                {isDragOver ? "Drop your PDFs here" : "Drag & drop one or more PDFs"}
              </p>
              <p className="mt-1 text-xs text-gray-500">or click to browse — up to 50 MB each</p>
            </div>
            <span className="flex items-center gap-1.5 rounded-full border border-gray-700 bg-gray-800/60 px-3 py-1 text-xs font-medium text-gray-400">
              <Plus className="h-3 w-3" />
              Uploads group into a unified batch
            </span>
          </div>
        )}
      </div>

      {/* Partial warning banner */}
      {partialWarnings.length > 0 && (
        <div className="rounded-xl border border-amber-800/50 bg-amber-950/20 p-3.5 text-xs text-amber-300 flex items-start gap-2.5">
          <AlertTriangle className="h-4 w-4 text-amber-400 flex-shrink-0 mt-0.5" />
          <div className="space-y-1">
            <p className="font-semibold">Some files in this batch could not be processed:</p>
            <ul className="list-disc pl-4 space-y-0.5 text-amber-400/90">
              {partialWarnings.map((w, idx) => (
                <li key={idx}>
                  <span className="font-medium">{w.filename}:</span> {w.error}
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}

      {/* Per-file progress list */}
      {queue.length > 0 && (
        <div className="space-y-2 animate-in fade-in slide-in-from-bottom-2 duration-300">
          <div className="flex items-center justify-between px-1">
            <p className="text-xs font-medium text-gray-500 uppercase tracking-wider">
              Upload Batch ({queue.length} file{queue.length !== 1 ? "s" : ""})
            </p>
            {allDone && (
              <button
                onClick={handleReset}
                className="text-xs text-gray-600 hover:text-gray-400 transition-colors"
              >
                Clear all
              </button>
            )}
          </div>

          {queue.map((entry, i) => (
            <div
              key={`${entry.file.name}-${i}`}
              className={cn(
                "flex items-center gap-3 rounded-xl border px-4 py-3 transition-all duration-300",
                entry.status === "success"
                  ? "border-emerald-800/40 bg-emerald-900/10"
                  : entry.status === "error"
                  ? "border-red-800/40 bg-red-900/10"
                  : entry.status === "processing"
                  ? "border-violet-700/40 bg-violet-900/10"
                  : "border-gray-800/60 bg-gray-900/40"
              )}
            >
              <div className="flex-shrink-0">
                {entry.status === "success" ? (
                  <CheckCircle2 className="h-4 w-4 text-emerald-400" />
                ) : entry.status === "error" ? (
                  <XCircle className="h-4 w-4 text-red-400" />
                ) : entry.status === "processing" ? (
                  <Loader2 className="h-4 w-4 text-violet-400 animate-spin" />
                ) : (
                  <FileText className="h-4 w-4 text-gray-600" />
                )}
              </div>

              <div className="min-w-0 flex-1">
                <p className="truncate text-xs font-medium text-gray-300">{entry.file.name}</p>
                {entry.status === "error" && entry.error && (
                  <p className="mt-0.5 text-xs text-red-400 truncate">{entry.error}</p>
                )}
                {entry.status === "success" && (
                  <p className="mt-0.5 text-xs text-emerald-500">Ready — summary & embeddings generated</p>
                )}
                {entry.status === "processing" && (
                  <p className="mt-0.5 text-xs text-violet-400">Processing in batch…</p>
                )}
                {entry.status === "idle" && (
                  <p className="mt-0.5 text-xs text-gray-600">Queued</p>
                )}
              </div>

              <span className="flex-shrink-0 text-xs text-gray-600">
                {(entry.file.size / (1024 * 1024)).toFixed(1)} MB
              </span>
            </div>
          ))}

          {allDone && (
            <p className={cn(
              "text-center text-xs pt-1 font-medium",
              anyError ? "text-amber-400" : "text-emerald-400"
            )}>
              {anyError
                ? `${successCount} of ${queue.length} processed in batch — some files had issues`
                : `Batch created with all ${queue.length} document${queue.length !== 1 ? "s" : ""}!`}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
