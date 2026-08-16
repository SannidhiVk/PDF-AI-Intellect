"use client";

/**
 * /auth/page.tsx
 * ---------------
 * Sign In / Sign Up page with dark glassmorphism design matching the app theme.
 * After a successful sign-in the user is redirected to the dashboard.
 * After sign-up they see a "check your email" confirmation screen.
 */

import { useState, FormEvent } from "react";
import { useRouter } from "next/navigation";
import { Brain, Mail, Lock, User, Loader2, CheckCircle2, AlertCircle } from "lucide-react";
import { supabase } from "@/lib/supabaseClient";

type AuthMode = "signin" | "signup" | "reset";

export default function AuthPage() {
  const router = useRouter();
  const [mode, setMode] = useState<AuthMode>("signin");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [emailSent, setEmailSent] = useState(false);
  const [resetSent, setResetSent] = useState(false);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);

    try {
      if (mode === "signup") {
        const { error } = await supabase.auth.signUp({
          email,
          password,
          options: {
            data: { full_name: name.trim() || email.split("@")[0] },
          },
        });
        if (error) throw error;
        setEmailSent(true);
      } else if (mode === "reset") {
        const { error } = await supabase.auth.resetPasswordForEmail(email, {
          redirectTo: `${window.location.origin}/auth/update-password`,
        });
        if (error) throw error;
        setResetSent(true);
      } else {
        const { error } = await supabase.auth.signInWithPassword({ email, password });
        if (error) throw error;
        router.replace("/");
      }
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "An unexpected error occurred.";
      setError(message);
    } finally {
      setLoading(false);
    }
  };

  if (emailSent) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-gray-950 px-4">
        <div className="relative max-w-md w-full rounded-2xl border border-gray-800 bg-gray-900/80 p-10 text-center backdrop-blur-xl shadow-2xl">
          <div className="mx-auto mb-5 flex h-16 w-16 items-center justify-center rounded-full bg-emerald-900/40">
            <CheckCircle2 className="h-8 w-8 text-emerald-400" />
          </div>
          <h2 className="text-xl font-semibold text-white">Check your email</h2>
          <p className="mt-3 text-sm leading-relaxed text-gray-400">
            We sent a confirmation link to{" "}
            <span className="font-medium text-violet-400">{email}</span>. Click
            the link to verify your account, then come back here to sign in.
          </p>
          <button
            onClick={() => { setEmailSent(false); setMode("signin"); }}
            className="mt-6 text-sm text-gray-500 hover:text-gray-300 underline underline-offset-2 transition-colors"
          >
            Back to Sign In
          </button>
        </div>
      </div>
    );
  }

  if (resetSent) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-gray-950 px-4">
        <div className="relative max-w-md w-full rounded-2xl border border-gray-800 bg-gray-900/80 p-10 text-center backdrop-blur-xl shadow-2xl">
          <div className="mx-auto mb-5 flex h-16 w-16 items-center justify-center rounded-full bg-sky-900/40">
            <CheckCircle2 className="h-8 w-8 text-sky-400" />
          </div>
          <h2 className="text-xl font-semibold text-white">Reset link sent!</h2>
          <p className="mt-3 text-sm leading-relaxed text-gray-400">
            We emailed a password reset link to{" "}
            <span className="font-medium text-violet-400">{email}</span>.{" "}
            Follow the link to choose a new password.
          </p>
          <button
            onClick={() => { setResetSent(false); setMode("signin"); setEmail(""); }}
            className="mt-6 text-sm text-gray-500 hover:text-gray-300 underline underline-offset-2 transition-colors"
          >
            Back to Sign In
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center bg-gray-950 px-4 overflow-hidden">
      {/* Animated background blobs */}
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="absolute -top-32 -left-32 h-96 w-96 rounded-full bg-violet-700/15 blur-3xl animate-pulse" />
        <div
          className="absolute -bottom-32 -right-32 h-96 w-96 rounded-full bg-indigo-700/15 blur-3xl animate-pulse"
          style={{ animationDelay: "1.5s" }}
        />
        <div
          className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 h-64 w-64 rounded-full bg-violet-900/10 blur-2xl animate-pulse"
          style={{ animationDelay: "0.75s" }}
        />
      </div>

      <div className="relative w-full max-w-md">
        {/* Card */}
        <div className="rounded-2xl border border-gray-800/80 bg-gray-900/70 p-8 shadow-2xl backdrop-blur-xl">
          {/* Logo */}
          <div className="mb-8 flex flex-col items-center gap-3 text-center">
            <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-gradient-to-br from-violet-600 to-indigo-600 shadow-lg shadow-violet-900/40">
              <Brain className="h-6 w-6 text-white" />
            </div>
            <div>
              <h1 className="text-xl font-bold text-white">PDF Intellect</h1>
              <p className="text-xs text-gray-500 mt-0.5">AI Document Assistant</p>
            </div>
          </div>

          {/* Tab toggle */}
          <div className="mb-6 flex rounded-xl bg-gray-950 border border-gray-800 p-1 gap-1">
            <TabBtn
              label="Sign In"
              active={mode === "signin"}
              onClick={() => { setMode("signin"); setError(null); }}
              id="auth-tab-signin"
            />
            <TabBtn
              label="Sign Up"
              active={mode === "signup"}
              onClick={() => { setMode("signup"); setError(null); }}
              id="auth-tab-signup"
            />
            <TabBtn
              label="Reset"
              active={mode === "reset"}
              onClick={() => { setMode("reset"); setError(null); }}
              id="auth-tab-reset"
            />
          </div>

          {/* Form */}
          <form onSubmit={handleSubmit} className="space-y-4" id="auth-form">
            {/* Name — only shown during signup */}
            {mode === "signup" && (
              <div>
                <label htmlFor="auth-name" className="block mb-1.5 text-xs font-medium text-gray-400">
                  Full name
                </label>
                <div className="relative">
                  <User className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-600" />
                  <input
                    id="auth-name"
                    type="text"
                    required
                    autoComplete="name"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="Jane Smith"
                    className="w-full rounded-lg border border-gray-700 bg-gray-800/60 pl-10 pr-4 py-2.5 text-sm text-gray-100 placeholder-gray-600 focus:border-violet-600 focus:outline-none focus:ring-1 focus:ring-violet-600/50 transition-colors"
                  />
                </div>
              </div>
            )}

            {/* Email */}
            <div>
              <label htmlFor="auth-email" className="block mb-1.5 text-xs font-medium text-gray-400">
                Email address
              </label>
              <div className="relative">
                <Mail className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-600" />
                <input
                  id="auth-email"
                  type="email"
                  required
                  autoComplete="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@example.com"
                  className="w-full rounded-lg border border-gray-700 bg-gray-800/60 pl-10 pr-4 py-2.5 text-sm text-gray-100 placeholder-gray-600 focus:border-violet-600 focus:outline-none focus:ring-1 focus:ring-violet-600/50 transition-colors"
                />
              </div>
            </div>

            {/* Password — hidden on reset mode */}
            {mode !== "reset" && (
              <div>
                <label htmlFor="auth-password" className="block mb-1.5 text-xs font-medium text-gray-400">
                  Password
                </label>
                <div className="relative">
                  <Lock className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-600" />
                  <input
                    id="auth-password"
                    type="password"
                    required
                    autoComplete={mode === "signup" ? "new-password" : "current-password"}
                    minLength={6}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="••••••••"
                    className="w-full rounded-lg border border-gray-700 bg-gray-800/60 pl-10 pr-4 py-2.5 text-sm text-gray-100 placeholder-gray-600 focus:border-violet-600 focus:outline-none focus:ring-1 focus:ring-violet-600/50 transition-colors"
                  />
                </div>
                {mode === "signup" && (
                  <p className="mt-1.5 text-xs text-gray-600">Minimum 6 characters</p>
                )}
              </div>
            )}

            {/* Reset mode helper text */}
            {mode === "reset" && (
              <p className="text-xs text-gray-500 leading-relaxed -mt-1">
                Enter your account email and we&apos;ll send you a link to reset your password.
              </p>
            )}

            {/* Error message */}
            {error && (
              <div className="flex items-start gap-2.5 rounded-lg border border-red-800/60 bg-red-900/20 px-3 py-2.5">
                <AlertCircle className="h-4 w-4 flex-shrink-0 text-red-400 mt-0.5" />
                <p className="text-sm text-red-300">{error}</p>
              </div>
            )}

            {/* Submit */}
            <button
              id="auth-submit-btn"
              type="submit"
              disabled={loading}
              className="mt-2 w-full flex items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-violet-600 to-indigo-600 py-2.5 text-sm font-semibold text-white shadow-lg shadow-violet-900/30 hover:opacity-90 hover:scale-[1.01] active:scale-[0.99] disabled:opacity-60 disabled:cursor-not-allowed disabled:scale-100 transition-all duration-200"
            >
              {loading ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  {mode === "signup" ? "Creating account…" : mode === "reset" ? "Sending reset link…" : "Signing in…"}
                </>
              ) : mode === "signup" ? (
                "Create Account"
              ) : mode === "reset" ? (
                "Send Reset Link"
              ) : (
                "Sign In"
              )}
            </button>
          </form>

          {/* Footer switch */}
          <p className="mt-5 text-center text-xs text-gray-600">
            {mode === "signin" ? (
              <>
                Don&apos;t have an account?{" "}
                <button
                  onClick={() => { setMode("signup"); setError(null); }}
                  className="text-violet-400 hover:text-violet-300 font-medium transition-colors"
                >
                  Sign up
                </button>
                <span className="mx-1.5">·</span>
                <button
                  onClick={() => { setMode("reset"); setError(null); }}
                  className="text-gray-500 hover:text-gray-300 transition-colors"
                >
                  Forgot password?
                </button>
              </>
            ) : mode === "reset" ? (
              <>
                Remember your password?{" "}
                <button
                  onClick={() => { setMode("signin"); setError(null); }}
                  className="text-violet-400 hover:text-violet-300 font-medium transition-colors"
                >
                  Sign in
                </button>
              </>
            ) : (
              <>
                Already have an account?{" "}
                <button
                  onClick={() => { setMode("signin"); setError(null); }}
                  className="text-violet-400 hover:text-violet-300 font-medium transition-colors"
                >
                  Sign in
                </button>
              </>
            )}
          </p>
        </div>
      </div>
    </div>
  );
}

function TabBtn({
  label,
  active,
  onClick,
  id,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
  id: string;
}) {
  return (
    <button
      type="button"
      id={id}
      onClick={onClick}
      className={`flex-1 rounded-lg py-2 text-sm font-medium transition-all duration-150 ${
        active
          ? "bg-violet-600 text-white shadow-sm"
          : "text-gray-500 hover:text-gray-300"
      }`}
    >
      {label}
    </button>
  );
}
