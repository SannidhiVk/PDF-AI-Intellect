"use client";

/**
 * CommentSection.tsx
 * ------------------
 * Reusable threaded comments component used in:
 *   - Owner dashboard  (mode="owner", pass documentId + authToken)
 *   - Public share page (mode="shared", pass shareToken)
 *
 * Supports:
 *   - Flat top-level comments with one-level replies
 *   - Guest commenting (display name input, no account needed)
 *   - Optimistic UI (comment appears immediately, rolls back on error)
 *   - Relative timestamps
 *   - Delete button for own comments / all comments if owner
 */

import { useState, useEffect, useCallback, useRef } from "react";
import axios from "axios";
import {
  MessageCircle,
  Send,
  Trash2,
  CornerDownRight,
  X,
  Loader2,
  ChevronDown,
  ChevronUp,
  User,
} from "lucide-react";
import { cn } from "@/lib/utils";

const FASTAPI_URL = process.env.NEXT_PUBLIC_FASTAPI_URL || "http://127.0.0.1:8000";

// ── Types ─────────────────────────────────────────────────────────────────────

export interface Comment {
  id: string;
  parent_id: string | null;
  author_name: string;
  content: string;
  created_at: string;
  is_own: boolean;
  replies: Comment[];
}

interface CommentSectionProps {
  /** "owner"  — uses /api/documents/{documentId}/comments (needs authToken) */
  /** "shared" — uses /api/share/{shareToken}/comments (no auth needed) */
  mode: "owner" | "shared";
  documentId?: string;   // required when mode="owner"
  shareToken?: string;   // required when mode="shared"
  authToken?: string;    // JWT; required when mode="owner"
  /** Signed-in user's display name/email — used as the author for owner
   *  comments instead of a free-text field, since the owner's identity is
   *  already known from their account. Ignored in "shared" (guest) mode. */
  currentUserName?: string;
  /** If true the section starts collapsed */
  defaultCollapsed?: boolean;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function relativeTime(iso: string): string {
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function listUrl(mode: "owner" | "shared", documentId?: string, shareToken?: string) {
  return mode === "owner"
    ? `${FASTAPI_URL}/api/documents/${documentId}/comments`
    : `${FASTAPI_URL}/api/share/${shareToken}/comments`;
}

function postUrl(mode: "owner" | "shared", documentId?: string, shareToken?: string) {
  return mode === "owner"
    ? `${FASTAPI_URL}/api/documents/${documentId}/comments` // owner posts directly, no share link required
    : `${FASTAPI_URL}/api/share/${shareToken}/comments`;
}

// ── Main Component ────────────────────────────────────────────────────────────

export default function CommentSection({
  mode,
  documentId,
  shareToken,
  authToken,
  currentUserName,
  defaultCollapsed = false,
}: CommentSectionProps) {
  const [comments, setComments] = useState<Comment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState(defaultCollapsed);

  // New comment form state
  const [authorName, setAuthorName] = useState("");
  const [content, setContent] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  // Reply state — which comment are we replying to?
  const [replyTo, setReplyTo] = useState<{ id: string; author: string } | null>(null);

  const fetchComments = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const url = listUrl(mode, documentId, shareToken);
      const headers: Record<string, string> = {};
      if (mode === "owner" && authToken) {
        headers["Authorization"] = `Bearer ${authToken}`;
      }
      const res = await axios.get<Comment[]>(url, { headers });
      setComments(res.data);
    } catch {
      setError("Failed to load comments. Please refresh.");
    } finally {
      setLoading(false);
    }
  }, [mode, documentId, shareToken, authToken]);

  useEffect(() => {
    if (!collapsed) fetchComments();
  }, [collapsed, fetchComments]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!content.trim()) return;

    setSubmitting(true);
    setSubmitError(null);

    // In owner mode, identity comes from the signed-in account, not a
    // free-text field — the field is guest-only (see JSX below).
    const resolvedAuthorName =
      mode === "owner" ? currentUserName?.trim() || "You" : authorName.trim() || "Anonymous";

    // Optimistic comment
    const optimistic: Comment = {
      id: `opt-${Date.now()}`,
      parent_id: replyTo?.id ?? null,
      author_name: resolvedAuthorName,
      content: content.trim(),
      created_at: new Date().toISOString(),
      is_own: true,
      replies: [],
    };

    setComments((prev) => {
      if (replyTo) {
        return prev.map((c) =>
          c.id === replyTo.id
            ? { ...c, replies: [...c.replies, optimistic] }
            : c
        );
      }
      return [...prev, optimistic];
    });

    const savedContent = content;
    const savedReplyTo = replyTo;
    setContent("");
    setReplyTo(null);

    try {
      const url = postUrl(mode, documentId, shareToken);
      const payload = {
        content: optimistic.content,
        author_name: optimistic.author_name,
        parent_id: savedReplyTo?.id ?? null,
      };
      const headers: Record<string, string> = {};
      if (mode === "owner" && authToken) {
        headers["Authorization"] = `Bearer ${authToken}`;
      }

      await axios.post(url, payload, { headers });
      // Refresh to get server-generated id
      await fetchComments();
    } catch {
      setSubmitError("Failed to post comment. Please try again.");
      // Roll back optimistic insert
      setComments((prev) => {
        if (savedReplyTo) {
          return prev.map((c) =>
            c.id === savedReplyTo.id
              ? { ...c, replies: c.replies.filter((r) => r.id !== optimistic.id) }
              : c
          );
        }
        return prev.filter((c) => c.id !== optimistic.id);
      });
      setContent(savedContent);
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (commentId: string) => {
    if (!authToken) return;
    try {
      await axios.delete(`${FASTAPI_URL}/api/comments/${commentId}`, {
        headers: { Authorization: `Bearer ${authToken}` },
      });
      await fetchComments();
    } catch {
      // Silently ignore — comment may already be gone
    }
  };

  const totalCount = comments.reduce((n, c) => n + 1 + c.replies.length, 0);

  return (
    <div className="rounded-2xl border border-gray-800/60 bg-gray-900/60 backdrop-blur-sm overflow-hidden">
      {/* Header */}
      <button
        onClick={() => setCollapsed((v) => !v)}
        className="w-full flex items-center justify-between px-6 py-4 border-b border-gray-800/60 hover:bg-gray-800/20 transition-colors"
      >
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-sky-600 to-blue-600 shadow-lg shadow-sky-900/20">
            <MessageCircle className="h-4 w-4 text-white" />
          </div>
          <div className="text-left">
            <h3 className="text-sm font-semibold text-white">Comments</h3>
            <p className="text-xs text-gray-500">
              {loading ? "Loading…" : `${totalCount} comment${totalCount !== 1 ? "s" : ""}`}
            </p>
          </div>
        </div>
        {collapsed ? (
          <ChevronDown className="h-4 w-4 text-gray-500" />
        ) : (
          <ChevronUp className="h-4 w-4 text-gray-500" />
        )}
      </button>

      {/* Body */}
      {!collapsed && (
        <div className="px-6 py-5 space-y-6">
          {/* Error */}
          {error && (
            <p className="text-sm text-red-400 bg-red-900/20 border border-red-800/40 rounded-xl px-4 py-3">
              {error}
            </p>
          )}

          {/* Comment list */}
          {loading ? (
            <div className="flex items-center gap-2 text-sm text-gray-500">
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading comments…
            </div>
          ) : comments.length === 0 ? (
            <div className="text-center py-6">
              <MessageCircle className="h-8 w-8 text-gray-700 mx-auto mb-2" />
              <p className="text-sm text-gray-500">No comments yet. Be the first!</p>
            </div>
          ) : (
            <div className="space-y-4">
              {comments.map((comment) => (
                <CommentThread
                  key={comment.id}
                  comment={comment}
                  onReply={(id, author) => setReplyTo({ id, author })}
                  onDelete={handleDelete}
                  canDelete={!!authToken}
                />
              ))}
            </div>
          )}

          {/* Reply context banner */}
          {replyTo && (
            <div className="flex items-center gap-2 rounded-xl bg-sky-900/20 border border-sky-800/40 px-4 py-2 text-sm text-sky-400">
              <CornerDownRight className="h-4 w-4 shrink-0" />
              <span>
                Replying to <span className="font-semibold">{replyTo.author}</span>
              </span>
              <button
                onClick={() => setReplyTo(null)}
                className="ml-auto text-sky-500 hover:text-sky-300"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          )}

          {/* Submit error */}
          {submitError && (
            <p className="text-sm text-red-400">{submitError}</p>
          )}

          {/* New comment form */}
          <form onSubmit={handleSubmit} className="space-y-3">
            {/* Display name input — guests only. Owner identity comes
                from their signed-in account (currentUserName prop). */}
            {mode === "shared" && (
              <div className="flex items-center gap-2 rounded-xl border border-gray-700/60 bg-gray-800/40 px-4 py-2">
                <User className="h-3.5 w-3.5 text-gray-500 shrink-0" />
                <input
                  type="text"
                  value={authorName}
                  onChange={(e) => setAuthorName(e.target.value)}
                  placeholder="Your name (optional)"
                  className="flex-1 bg-transparent text-sm text-gray-200 placeholder-gray-600 focus:outline-none"
                  maxLength={60}
                />
              </div>
            )}

            {/* Comment textarea */}
            <div className="flex items-end gap-3 rounded-xl border border-gray-700 bg-gray-800/60 px-4 py-3 focus-within:border-sky-600/60 transition-colors">
              <textarea
                value={content}
                onChange={(e) => setContent(e.target.value)}
                placeholder={
                  replyTo
                    ? `Reply to ${replyTo.author}…`
                    : "Write a comment…"
                }
                rows={2}
                className="flex-1 resize-none bg-transparent text-sm text-gray-200 placeholder-gray-600 focus:outline-none leading-relaxed"
                style={{ minHeight: "48px", maxHeight: "140px" }}
              />
              <button
                type="submit"
                disabled={!content.trim() || submitting}
                className={cn(
                  "flex h-8 w-8 shrink-0 items-center justify-center rounded-lg transition-all duration-200",
                  content.trim() && !submitting
                    ? "bg-sky-600 text-white hover:bg-sky-500 shadow-lg shadow-sky-900/30"
                    : "bg-gray-700 text-gray-600 cursor-not-allowed"
                )}
              >
                {submitting ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Send className="h-4 w-4" />
                )}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}

// ── Comment Thread (top-level + replies) ─────────────────────────────────────

function CommentThread({
  comment,
  onReply,
  onDelete,
  canDelete,
}: {
  comment: Comment;
  onReply: (id: string, author: string) => void;
  onDelete: (id: string) => void;
  canDelete: boolean;
}) {
  return (
    <div className="space-y-3">
      <CommentBubble
        comment={comment}
        onReply={onReply}
        onDelete={onDelete}
        canDelete={canDelete}
      />
      {/* Replies — indented */}
      {comment.replies.length > 0 && (
        <div className="ml-6 pl-4 border-l border-gray-800/60 space-y-3">
          {comment.replies.map((reply) => (
            <CommentBubble
              key={reply.id}
              comment={reply}
              onReply={onReply}
              onDelete={onDelete}
              canDelete={canDelete}
              isReply
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ── Single Comment Bubble ─────────────────────────────────────────────────────

function CommentBubble({
  comment,
  onReply,
  onDelete,
  canDelete,
  isReply = false,
}: {
  comment: Comment;
  onReply: (id: string, author: string) => void;
  onDelete: (id: string) => void;
  canDelete: boolean;
  isReply?: boolean;
}) {
  const initials = comment.author_name
    .split(" ")
    .map((w) => w[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();

  return (
    <div className="flex gap-3 animate-in fade-in slide-in-from-bottom-1 duration-200">
      {/* Avatar */}
      <div
        className={cn(
          "flex shrink-0 items-center justify-center rounded-full font-semibold text-white text-xs",
          isReply ? "h-7 w-7" : "h-8 w-8",
          "bg-gradient-to-br from-sky-600 to-indigo-600"
        )}
      >
        {initials || <User className="h-3.5 w-3.5" />}
      </div>

      {/* Body */}
      <div className="flex-1 min-w-0">
        <div className="flex items-baseline gap-2 flex-wrap">
          <span className="text-sm font-semibold text-gray-200">
            {comment.author_name}
          </span>
          <span className="text-xs text-gray-600">
            {relativeTime(comment.created_at)}
          </span>
        </div>
        <p className="mt-0.5 text-sm text-gray-300 leading-relaxed break-words">
          {comment.content}
        </p>

        {/* Actions */}
        <div className="mt-1.5 flex items-center gap-3">
          {!isReply && (
            <button
              onClick={() => onReply(comment.id, comment.author_name)}
              className="flex items-center gap-1 text-xs text-gray-600 hover:text-sky-400 transition-colors"
            >
              <CornerDownRight className="h-3 w-3" />
              Reply
            </button>
          )}
          {(canDelete || comment.is_own) && (
            <button
              onClick={() => onDelete(comment.id)}
              className="flex items-center gap-1 text-xs text-gray-600 hover:text-red-400 transition-colors"
            >
              <Trash2 className="h-3 w-3" />
              Delete
            </button>
          )}
        </div>
      </div>
    </div>
  );
}