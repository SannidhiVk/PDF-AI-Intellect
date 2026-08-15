"use client";

import { useState, useCallback } from "react";
import {
  Link2,
  Link2Off,
  Check,
  Loader2,
  Mail,
  Send,
} from "lucide-react";
import axios from "axios";
import { cn } from "@/lib/utils";

const FASTAPI_URL =
  process.env.NEXT_PUBLIC_FASTAPI_URL || "http://127.0.0.1:8000";

interface ShareControlsProps {
  /** The document to share/revoke */
  documentId: string;
  /** Owner's JWT */
  authToken: string;
}

/**
 * ShareControls — Copy Link · Invite · Revoke
 *
 * Fully self-contained share widget. Designed to live in the top header so it
 * is accessible at any time while a document is selected, not only after the
 * summary is shown.
 */
export default function ShareControls({
  documentId,
  authToken,
}: ShareControlsProps) {
  // ── Share state ───────────────────────────────────────────────────────────
  const [shareUrl, setShareUrl] = useState<string | null>(null);
  const [shareActive, setShareActive] = useState(false);
  const [sharing, setSharing] = useState(false);
  const [revoking, setRevoking] = useState(false);
  const [copied, setCopied] = useState(false);

  // ── Invite panel state ────────────────────────────────────────────────────
  const [showInvite, setShowInvite] = useState(false);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviting, setInviting] = useState(false);
  const [inviteSent, setInviteSent] = useState(false);
  const [inviteError, setInviteError] = useState<string | null>(null);

  // ── Handlers ──────────────────────────────────────────────────────────────
  const handleShare = useCallback(async () => {
    setSharing(true);
    try {
      const res = await axios.post<{ share_url: string; is_active: boolean }>(
        `${FASTAPI_URL}/api/documents/${documentId}/share`,
        {},
        { headers: { Authorization: `Bearer ${authToken}` } }
      );
      const url = res.data.share_url;
      setShareUrl(url);
      setShareActive(true);
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
    } catch {
      // silently ignore — user can retry
    } finally {
      setSharing(false);
    }
  }, [documentId, authToken]);

  const handleCopy = useCallback(async () => {
    if (!shareUrl) return;
    await navigator.clipboard.writeText(shareUrl);
    setCopied(true);
    setTimeout(() => setCopied(false), 2500);
  }, [shareUrl]);

  const handleRevoke = useCallback(async () => {
    setRevoking(true);
    try {
      await axios.delete(`${FASTAPI_URL}/api/documents/${documentId}/share`, {
        headers: { Authorization: `Bearer ${authToken}` },
      });
      setShareUrl(null);
      setShareActive(false);
      setShowInvite(false);
      setInviteEmail("");
      setInviteSent(false);
    } catch {
      // silently ignore
    } finally {
      setRevoking(false);
    }
  }, [documentId, authToken]);

  const handleInvite = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (!inviteEmail.trim()) return;
      setInviting(true);
      setInviteError(null);
      try {
        await axios.post(
          `${FASTAPI_URL}/api/documents/${documentId}/share/invite`,
          {
            recipient_email: inviteEmail.trim(),
            sender_name: "A PDF Intellect user",
          },
          { headers: { Authorization: `Bearer ${authToken}` } }
        );
        setInviteSent(true);
        setInviteEmail("");
        setTimeout(() => {
          setInviteSent(false);
          setShowInvite(false);
        }, 3000);
      } catch (err: unknown) {
        if (axios.isAxiosError(err)) {
          setInviteError(
            err.response?.data?.detail ?? "Failed to send invitation."
          );
        } else {
          setInviteError("Failed to send invitation.");
        }
      } finally {
        setInviting(false);
      }
    },
    [documentId, authToken, inviteEmail]
  );

  return (
    <div className="flex flex-col items-end gap-1.5">
      {/* ── Button row ─────────────────────────────────────────────────── */}
      <div className="flex items-center gap-1.5">
        {!shareActive ? (
          /* Generate + copy link */
          <button
            onClick={handleShare}
            disabled={sharing}
            id="share-doc-btn"
            className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium bg-violet-700/30 border border-violet-700/40 text-violet-300 hover:bg-violet-700/50 hover:border-violet-600/60 transition-all duration-150 disabled:opacity-50"
          >
            {sharing ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Link2 className="h-3.5 w-3.5" />
            )}
            Share Link
          </button>
        ) : (
          <>
            {/* Copy URL */}
            <button
              onClick={handleCopy}
              id="copy-share-link-btn"
              className={cn(
                "flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-all duration-200",
                copied
                  ? "bg-green-700/30 border border-green-700/40 text-green-300"
                  : "bg-violet-700/30 border border-violet-700/40 text-violet-300 hover:bg-violet-700/50"
              )}
            >
              {copied ? (
                <>
                  <Check className="h-3.5 w-3.5" /> Copied!
                </>
              ) : (
                <>
                  <Link2 className="h-3.5 w-3.5" /> Copy Link
                </>
              )}
            </button>

            {/* Toggle invite panel */}
            <button
              onClick={() => {
                setShowInvite((v) => !v);
                setInviteError(null);
              }}
              id="invite-email-btn"
              className={cn(
                "flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-all duration-150",
                showInvite
                  ? "bg-indigo-700/40 border border-indigo-600/50 text-indigo-300"
                  : "bg-gray-800 border border-gray-700 text-gray-400 hover:text-gray-200 hover:border-gray-600"
              )}
            >
              <Mail className="h-3.5 w-3.5" />
              Invite
            </button>

            {/* Revoke */}
            <button
              onClick={handleRevoke}
              disabled={revoking}
              id="revoke-share-btn"
              className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium bg-red-900/20 border border-red-800/30 text-red-400 hover:bg-red-900/40 hover:text-red-300 transition-all duration-150 disabled:opacity-50"
            >
              {revoking ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Link2Off className="h-3.5 w-3.5" />
              )}
              Revoke
            </button>
          </>
        )}
      </div>

      {/* ── Active share URL bar ────────────────────────────────────────── */}
      {shareActive && shareUrl && (
        <div className="flex items-center gap-2 rounded-lg px-3 py-1.5 bg-violet-950/40 border border-violet-800/30 max-w-xs">
          <Link2 className="h-3 w-3 text-violet-400 shrink-0" />
          <p className="text-[11px] text-violet-300 truncate font-mono">
            {shareUrl}
          </p>
        </div>
      )}

      {/* ── Invite panel ────────────────────────────────────────────────── */}
      {shareActive && showInvite && (
        <div className="w-80 rounded-xl border border-indigo-800/30 bg-indigo-950/30 backdrop-blur-sm p-3 animate-in fade-in slide-in-from-top-2 duration-200">
          <p className="text-xs font-medium text-indigo-300 mb-2 flex items-center gap-1.5">
            <Mail className="h-3.5 w-3.5" />
            Send invite email
          </p>
          {inviteSent ? (
            <div className="flex items-center gap-2 text-emerald-400 text-xs">
              <Check className="h-4 w-4" />
              Invitation sent! The link has been emailed.
            </div>
          ) : (
            <form onSubmit={handleInvite} className="flex items-center gap-2">
              <input
                id="invite-email-input"
                type="email"
                required
                value={inviteEmail}
                onChange={(e) => setInviteEmail(e.target.value)}
                placeholder="recipient@example.com"
                className="flex-1 rounded-lg border border-indigo-800/60 bg-indigo-900/20 px-3 py-2 text-xs text-gray-100 placeholder-gray-600 focus:border-indigo-600 focus:outline-none focus:ring-1 focus:ring-indigo-600/40 transition-colors"
              />
              <button
                type="submit"
                disabled={inviting || !inviteEmail.trim()}
                id="send-invite-btn"
                className="flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs font-medium bg-indigo-600 text-white hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {inviting ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Send className="h-3.5 w-3.5" />
                )}
                Send
              </button>
            </form>
          )}
          {inviteError && (
            <p className="mt-2 text-xs text-red-400">{inviteError}</p>
          )}
        </div>
      )}
    </div>
  );
}
