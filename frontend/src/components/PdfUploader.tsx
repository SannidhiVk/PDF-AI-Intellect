"use client";

import { useState, useCallback, useRef } from "react";
import axios from "axios";
import { UploadCloud, FileText, CheckCircle2, XCircle, Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";
import { supabase } from "@/lib/supabaseClient";

const FASTAPI_URL = process.env.NEXT_PUBLIC_FASTAPI_URL || "http://127.0.0.1:8000";

type UploadStatus = "idle" | "processing" | "success" | "error";

interface PdfUploaderProps {
  onSuccess: (data: { document_id: string; summary: string; filename: string }) => void;
  userId: string;
}

export default function PdfUploader({ onSuccess, userId }: PdfUploaderProps) {
  const [status, setStatus] = useState<UploadStatus>("idle");
  const [isDragOver, setIsDragOver] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFile = useCallback(
    async (file: File) => {
      if (file.type !== "application/pdf") {
        setErrorMessage("Only PDF files are accepted.");
        setStatus("error");
        return;
      }

      if (file.size > 50 * 1024 * 1024) {
        setErrorMessage("File size must be under 50 MB.");
        setStatus("error");
        return;
      }

      setSelectedFile(file);
      setStatus("processing");
      setErrorMessage(null);

      const formData = new FormData();
      formData.append("file", file);
      // user_id is NO LONGER sent from the client.
      // The backend extracts it from the verified JWT in the Authorization header.

      // Fetch the current session token
      const { data: { session } } = await supabase.auth.getSession();
      const token = session?.access_token;
      if (!token) {
        setErrorMessage("You are not signed in. Please refresh and sign in again.");
        setStatus("error");
        return;
      }

      try {
        const response = await axios.post(
          `${FASTAPI_URL}/api/process-pdf`,
          formData,
          {
            headers: {
              "Content-Type": "multipart/form-data",
              Authorization: `Bearer ${token}`,
            },
            timeout: 120000, // 2 minutes for large PDFs
          }
        );

        setStatus("success");
        onSuccess({
          document_id: response.data.document_id,
          summary: response.data.summary,
          filename: file.name,
        });
      } catch (err: unknown) {
        setStatus("error");
        if (axios.isAxiosError(err)) {
          setErrorMessage(
            err.response?.data?.detail || "Failed to process the PDF. Please try again."
          );
        } else {
          setErrorMessage("An unexpected error occurred.");
        }
      }
    },
    [onSuccess]
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragOver(false);
      const file = e.dataTransfer.files[0];
      if (file) handleFile(file);
    },
    [handleFile]
  );

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(true);
  };

  const handleDragLeave = () => setIsDragOver(false);

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) handleFile(file);
  };

  const handleReset = () => {
    setStatus("idle");
    setSelectedFile(null);
    setErrorMessage(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const isProcessing = status === "processing";

  return (
    <div className="w-full">
      {/* Drop Zone */}
      <div
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onClick={() => !isProcessing && fileInputRef.current?.click()}
        className={cn(
          "relative flex flex-col items-center justify-center rounded-2xl border-2 border-dashed p-12 transition-all duration-300 cursor-pointer select-none",
          isDragOver
            ? "border-violet-500 bg-violet-500/10 scale-[1.02]"
            : status === "success"
            ? "border-emerald-600/50 bg-emerald-900/10 cursor-default"
            : status === "error"
            ? "border-red-600/50 bg-red-900/10 cursor-default"
            : "border-gray-700 bg-gray-900/40 hover:border-violet-600/60 hover:bg-violet-900/10",
          isProcessing && "cursor-wait pointer-events-none"
        )}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept="application/pdf"
          onChange={handleInputChange}
          className="hidden"
          id="pdf-upload-input"
        />

        {/* Idle / Drag State */}
        {status === "idle" && (
          <div className="flex flex-col items-center gap-4 text-center">
            <div
              className={cn(
                "flex h-20 w-20 items-center justify-center rounded-2xl transition-all duration-300",
                isDragOver
                  ? "bg-violet-600/30 scale-110"
                  : "bg-gray-800"
              )}
            >
              <UploadCloud
                className={cn(
                  "h-9 w-9 transition-colors duration-300",
                  isDragOver ? "text-violet-400" : "text-gray-500"
                )}
              />
            </div>
            <div>
              <p className="text-base font-semibold text-gray-200">
                {isDragOver ? "Drop your PDF here" : "Drag & drop your PDF"}
              </p>
              <p className="mt-1 text-sm text-gray-500">or click to browse — up to 50 MB</p>
            </div>
            <span className="rounded-full border border-gray-700 bg-gray-800/60 px-4 py-1.5 text-xs font-medium text-gray-400">
              PDF files only
            </span>
          </div>
        )}

        {/* Processing State — no internal details exposed */}
        {isProcessing && (
          <div className="flex flex-col items-center gap-5 text-center">
            <div className="relative flex h-20 w-20 items-center justify-center">
              <div className="absolute inset-0 rounded-full border-4 border-gray-800" />
              <div
                className="absolute inset-0 rounded-full border-4 border-transparent border-t-violet-500 border-r-violet-400/40 animate-spin"
                style={{ animationDuration: "0.9s" }}
              />
              <Sparkles className="h-7 w-7 text-violet-400" />
            </div>
            <div>
              <p className="text-base font-semibold text-gray-200">Working on it…</p>
              <p className="mt-1 text-sm text-gray-500">This may take a moment</p>
            </div>
            {selectedFile && (
              <div className="flex items-center gap-2 rounded-lg bg-gray-800/50 px-4 py-2">
                <FileText className="h-4 w-4 text-violet-400" />
                <span className="max-w-xs truncate text-sm text-gray-300">{selectedFile.name}</span>
              </div>
            )}
          </div>
        )}

        {/* Success State */}
        {status === "success" && (
          <div className="flex flex-col items-center gap-4 text-center">
            <div className="flex h-20 w-20 items-center justify-center rounded-2xl bg-emerald-900/30">
              <CheckCircle2 className="h-10 w-10 text-emerald-400" />
            </div>
            <div>
              <p className="text-base font-semibold text-emerald-300">PDF Processed Successfully!</p>
              <p className="mt-1 text-sm text-gray-500">
                Your document is ready — view the summary or start chatting below.
              </p>
            </div>
            {selectedFile && (
              <div className="flex items-center gap-2 rounded-lg bg-gray-800/50 px-4 py-2">
                <FileText className="h-4 w-4 text-emerald-400" />
                <span className="max-w-xs truncate text-sm text-gray-300">{selectedFile.name}</span>
              </div>
            )}
            <button
              onClick={(e) => { e.stopPropagation(); handleReset(); }}
              className="mt-1 text-xs text-gray-500 hover:text-gray-300 underline underline-offset-2 transition-colors"
            >
              Upload another PDF
            </button>
          </div>
        )}

        {/* Error State */}
        {status === "error" && (
          <div className="flex flex-col items-center gap-4 text-center">
            <div className="flex h-20 w-20 items-center justify-center rounded-2xl bg-red-900/20">
              <XCircle className="h-10 w-10 text-red-400" />
            </div>
            <div>
              <p className="text-base font-semibold text-red-300">Upload Failed</p>
              <p className="mt-1 text-sm text-gray-500">{errorMessage}</p>
            </div>
            <button
              onClick={(e) => { e.stopPropagation(); handleReset(); }}
              className="rounded-lg bg-gray-800 px-5 py-2 text-sm font-medium text-gray-200 hover:bg-gray-700 transition-colors"
            >
              Try Again
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
