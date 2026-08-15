# Supabase Auth — Sign Up / Login Implementation

## Overview

Add a full authentication flow using **Supabase Auth** on the Next.js frontend. Currently, the app hard-codes `user_id = "00000000-0000-0000-0000-000000000000"` in `PdfUploader.tsx`. After this implementation:

- Users must sign up or log in before accessing the dashboard.
- The real Supabase `user.id` is passed to the backend on every PDF upload.
- Sessions are persisted across page refreshes via Supabase's built-in session management.
- A protected route wrapper ensures the dashboard is unreachable without a valid session.
- Users can sign out from the sidebar.

---

## Design — Auth Page

The auth page will be a **dark glassmorphism** design matching the existing `gray-950` theme:
- Gradient background with subtle animated blobs (violet/indigo).
- Glassmorphic card with backdrop blur.
- Toggle between **Sign In** and **Sign Up** tabs.
- Email + Password fields with real-time validation feedback.
- Loading states on submit buttons.
- Clear error messages from Supabase (wrong password, email in use, etc.).
- "Check your email" screen after successful sign-up (Supabase sends a confirmation link).

---

## Proposed Changes

### 1. Auth Context & Session Provider

#### [NEW] `src/lib/AuthContext.tsx`
- A React context + provider wrapping the entire app.
- Uses `supabase.auth.getSession()` on mount and `supabase.auth.onAuthStateChange()` to keep session reactive.
- Exports `useAuth()` hook returning `{ user, session, loading }`.

---

### 2. Auth Page

#### [NEW] `src/app/auth/page.tsx`
- Sign In / Sign Up UI (tab toggle).
- Calls `supabase.auth.signInWithPassword()` / `supabase.auth.signUp()`.
- Redirects to `/` on successful sign-in.
- Wrapped in `"use client"`.

---

### 3. Root Layout — Provider Injection

#### [MODIFY] `src/app/layout.tsx`
- Wrap `{children}` with `<AuthProvider>` from `AuthContext.tsx`.

---

### 4. Protected Dashboard

#### [MODIFY] `src/app/page.tsx`
- At the top of `DashboardPage`, read `{ user, loading }` from `useAuth()`.
- If `loading` → show a centered spinner.
- If `!user` → `router.replace("/auth")` (redirect, not render).
- If `user` → render the dashboard as normal.
- Pass `user.id` down to `<PdfUploader>` as a prop.

---

### 5. PdfUploader — Real User ID

#### [MODIFY] `src/components/PdfUploader.tsx`
- Add `userId: string` prop to `PdfUploaderProps`.
- Replace `formData.append("user_id", "00000000-...")` with `formData.append("user_id", userId)`.

---

### 6. Sidebar — Sign Out Button

#### [MODIFY] `src/components/Sidebar.tsx`
- Add a **Sign Out** button at the bottom of the sidebar.
- Calls `supabase.auth.signOut()` and `router.replace("/auth")`.
- Shows the logged-in user's email in a subtle footer strip.

---

## Verification Plan

### Automated
- App builds without TypeScript errors: `npm run build`

### Manual Browser Testing
1. Navigate to `http://localhost:3000` → should redirect to `/auth`.
2. Sign up with a new email → "Check your email" screen appears.
3. Sign in with correct credentials → dashboard loads.
4. Upload a PDF → verify `documents` table in Supabase has correct `user_id` (not the zero UUID).
5. Refresh page → session persists, dashboard stays visible.
6. Click **Sign Out** → redirected to `/auth`, dashboard inaccessible.
7. Navigate to `/` manually when logged out → redirected to `/auth`.

---

## Open Questions

> [!IMPORTANT]
> **Email confirmation**: Supabase sends a confirmation email by default for sign-ups. Do you want to:
> - **A) Keep it** (recommended for production) — users get a "check your email" screen.
> - **B) Disable it** in Supabase dashboard (`Authentication → Settings → Enable email confirmations = OFF`) so users can log in immediately after sign-up.
>
> This doesn't change any code — it's a Supabase dashboard setting. The current plan handles both; the UI shows a "check your email" message and auto-redirects once confirmed.

> [!NOTE]
> **Google / OAuth**: Not included in this plan. Let me know if you want social login added.
