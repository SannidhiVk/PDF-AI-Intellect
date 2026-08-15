"use client";

/**
 * /auth/update-password/page.tsx
 * --------------------------------
 * Landing page for Supabase password-reset redirect links.
 * Supabase navigates the user here after they click the reset email link.
 * We listen for the PASSWORD_RECOVERY auth event, then let them set a new password.
 */

import { useState, useEffect, FormEvent } from "react";
import { useRouter } from "next/navigation";
import { Brain, Lock, Loader2, CheckCircle2, AlertCircle } from "lucide-react";
import { supabase } from "@/lib/supabaseClient";

export default function UpdatePasswordPage() {
  const router = useRouter();
  const [ready, setReady] = useState(false);   // true once PASSWORD_RECOVERY event fires
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [loading, setLoading] = useState(false);
  const [success, setSuccess] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Supabase emits PASSWORD_RECOVERY when the page loads from a reset link.
  // We wait for that event before showing the form (the session is active at
  // that point so updateUser() will work).
  useEffect(() => {
    const { data: { subscription } } = supabase.auth.onAuthStateChange(
      (event) => {
        if (event === "PASSWORD_RECOVERY") setReady(true);
      }
    );
    // Also mark ready if there's already an active session from the URL hash
    supabase.auth.getSession().then(({ data }) => {
      if (data.session) setReady(true);
    });
    return () => subscription.unsubscribe();
  }, []);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (password !== confirm) {
      setError("Passwords do not match.");
      return;
    }
    if (password.length < 6) {
      setError("Password must be at least 6 characters.");
      return;
    }
    setError(null);
    setLoading(true);
    try {
      const { error } = await supabase.auth.updateUser({ password });
      if (error) throw error;
      setSuccess(true);
      setTimeout(() => router.replace("/"), 2500);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to update password.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="relative flex min-h-screen items-center justify-center bg-gray-950 px-4 overflow-hidden">
      {/* Background blobs */}
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="absolute -top-32 -left-32 h-96 w-96 rounded-full bg-violet-700/15 blur-3xl animate-pulse" />
        <div className="absolute -bottom-32 -right-32 h-96 w-96 rounded-full bg-indigo-700/15 blur-3xl animate-pulse" style={{ animationDelay: "1.5s" }} />
      </div>

      <div className="relative w-full max-w-md">
        <div className="rounded-2xl border border-gray-800/80 bg-gray-900/70 p-8 shadow-2xl backdrop-blur-xl">
          {/* Logo */}
          <div className="mb-8 flex flex-col items-center gap-3 text-center">
            <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-gradient-to-br from-violet-600 to-indigo-600 shadow-lg shadow-violet-900/40">
              <Brain className="h-6 w-6 text-white" />
            </div>
            <div>
              <h1 className="text-xl font-bold text-white">Set New Password</h1>
              <p className="text-xs text-gray-500 mt-0.5">PDF Intellect · AI Document Assistant</p>
            </div>
          </div>

          {success ? (
            <div className="flex flex-col items-center gap-4 py-4 text-center">
              <div className="flex h-14 w-14 items-center justify-center rounded-full bg-emerald-900/40">
                <CheckCircle2 className="h-7 w-7 text-emerald-400" />
              </div>
              <p className="text-sm text-gray-300">
                Password updated! Redirecting you to the dashboard…
              </p>
            </div>
          ) : !ready ? (
            <div className="flex flex-col items-center gap-4 py-8 text-center">
              <Loader2 className="h-7 w-7 animate-spin text-violet-400" />
              <p className="text-sm text-gray-400">Verifying reset link…</p>
            </div>
          ) : (
            <form onSubmit={handleSubmit} className="space-y-4">
              {/* New password */}
              <div>
                <label htmlFor="new-password" className="block mb-1.5 text-xs font-medium text-gray-400">
                  New password
                </label>
                <div className="relative">
                  <Lock className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-600" />
                  <input
                    id="new-password"
                    type="password"
                    required
                    minLength={6}
                    autoComplete="new-password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="••••••••"
                    className="w-full rounded-lg border border-gray-700 bg-gray-800/60 pl-10 pr-4 py-2.5 text-sm text-gray-100 placeholder-gray-600 focus:border-violet-600 focus:outline-none focus:ring-1 focus:ring-violet-600/50 transition-colors"
                  />
                </div>
              </div>

              {/* Confirm password */}
              <div>
                <label htmlFor="confirm-password" className="block mb-1.5 text-xs font-medium text-gray-400">
                  Confirm new password
                </label>
                <div className="relative">
                  <Lock className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-600" />
                  <input
                    id="confirm-password"
                    type="password"
                    required
                    minLength={6}
                    autoComplete="new-password"
                    value={confirm}
                    onChange={(e) => setConfirm(e.target.value)}
                    placeholder="••••••••"
                    className="w-full rounded-lg border border-gray-700 bg-gray-800/60 pl-10 pr-4 py-2.5 text-sm text-gray-100 placeholder-gray-600 focus:border-violet-600 focus:outline-none focus:ring-1 focus:ring-violet-600/50 transition-colors"
                  />
                </div>
                <p className="mt-1.5 text-xs text-gray-600">Minimum 6 characters</p>
              </div>

              {/* Error */}
              {error && (
                <div className="flex items-start gap-2.5 rounded-lg border border-red-800/60 bg-red-900/20 px-3 py-2.5">
                  <AlertCircle className="h-4 w-4 flex-shrink-0 text-red-400 mt-0.5" />
                  <p className="text-sm text-red-300">{error}</p>
                </div>
              )}

              {/* Submit */}
              <button
                id="update-password-btn"
                type="submit"
                disabled={loading}
                className="mt-2 w-full flex items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-violet-600 to-indigo-600 py-2.5 text-sm font-semibold text-white shadow-lg shadow-violet-900/30 hover:opacity-90 hover:scale-[1.01] active:scale-[0.99] disabled:opacity-60 disabled:cursor-not-allowed disabled:scale-100 transition-all duration-200"
              >
                {loading ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" />
                    Updating password…
                  </>
                ) : (
                  "Update Password"
                )}
              </button>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
