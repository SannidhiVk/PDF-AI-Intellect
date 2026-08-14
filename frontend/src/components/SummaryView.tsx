"use client";

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { FileText, ChevronDown, ChevronUp, BookOpen, Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";

interface SummaryViewProps {
  summary: string;
  filename: string;
}

export default function SummaryView({ summary, filename }: SummaryViewProps) {
  const [isExpanded, setIsExpanded] = useState(true);

  // Word count
  const wordCount = summary.split(/\s+/).filter(Boolean).length;
  const readingTime = Math.ceil(wordCount / 200); // avg 200 wpm

  // Plain-text preview for the collapsed state (strip common markdown symbols)
  const plainPreview = summary
    .replace(/#{1,6}\s*/g, "")
    .replace(/\*{1,2}([^*]+)\*{1,2}/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\n+/g, " ")
    .trim();

  return (
    <div className="rounded-2xl border border-gray-800/60 bg-gray-900/60 backdrop-blur-sm overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-gray-800/60">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-amber-600 to-orange-600 shadow-lg shadow-amber-900/20">
            <Sparkles className="h-4 w-4 text-white" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-white">AI Summary</h2>
            <p className="text-xs text-gray-500 max-w-xs truncate">{filename}</p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          {/* Metadata badges */}
          <div className="hidden sm:flex items-center gap-2">
            <Badge icon={<BookOpen className="h-3 w-3" />} label={`${wordCount} words`} />
            <Badge icon={<FileText className="h-3 w-3" />} label={`~${readingTime} min read`} />
          </div>

          <button
            onClick={() => setIsExpanded(!isExpanded)}
            className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium text-gray-400 hover:bg-gray-800 hover:text-gray-200 transition-all duration-150"
            aria-label={isExpanded ? "Collapse summary" : "Expand summary"}
          >
            {isExpanded ? (
              <>
                <ChevronUp className="h-3.5 w-3.5" />
                Collapse
              </>
            ) : (
              <>
                <ChevronDown className="h-3.5 w-3.5" />
                Expand
              </>
            )}
          </button>
        </div>
      </div>

      {/* Content */}
      <div
        className={cn(
          "transition-all duration-300 ease-in-out overflow-hidden",
          isExpanded ? "max-h-[600px]" : "max-h-0"
        )}
      >
        <div className="overflow-y-auto max-h-[600px] px-6 py-5 scrollbar-thin">
          {summary.trim().length > 0 ? (
            <div
              className={cn(
                "prose prose-invert prose-sm max-w-none",
                "prose-headings:text-gray-100 prose-headings:font-semibold",
                "prose-p:text-gray-300 prose-p:leading-relaxed",
                "prose-strong:text-gray-200 prose-strong:font-semibold",
                "prose-li:text-gray-300",
                "prose-ul:my-2 prose-ol:my-2",
                "prose-h2:text-base prose-h2:mt-4 prose-h2:mb-1",
                "prose-h3:text-sm prose-h3:mt-3 prose-h3:mb-1"
              )}
            >
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {summary}
              </ReactMarkdown>
            </div>
          ) : (
            <p className="text-sm text-gray-500">No summary available.</p>
          )}
        </div>

        {/* Gradient fade at the bottom if content is long */}
        {summary.split("\n").length > 10 && (
          <div className="h-8 bg-gradient-to-t from-gray-900/60 to-transparent -mt-8 relative z-10 pointer-events-none" />
        )}
      </div>

      {/* Collapsed preview — plain text, no markdown symbols */}
      {!isExpanded && plainPreview.length > 0 && (
        <div className="px-6 py-3 border-t border-gray-800/40">
          <p className="text-xs text-gray-500 truncate italic">{plainPreview}</p>
        </div>
      )}
    </div>
  );
}

function Badge({ icon, label }: { icon: React.ReactNode; label: string }) {
  return (
    <span className="flex items-center gap-1.5 rounded-full bg-gray-800 px-2.5 py-1 text-xs font-medium text-gray-400">
      {icon}
      {label}
    </span>
  );
}
