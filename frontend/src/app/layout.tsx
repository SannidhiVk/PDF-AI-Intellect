import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { AuthProvider } from "@/lib/AuthContext";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
  display: "swap",
});

const jetbrainsMono = JetBrains_Mono({
  variable: "--font-mono",
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "PDF Intellect — AI Document Assistant",
  description:
    "Upload PDF documents, get AI-powered summaries, and chat with your documents using natural language. Powered by Gemini and FastAPI.",
  keywords: ["PDF", "AI", "summarization", "RAG", "chatbot", "document analysis"],
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html
      lang="en"
      className={`${inter.variable} ${jetbrainsMono.variable} h-full antialiased`}
    >
      <body className="h-full bg-gray-950 text-gray-100 font-sans">
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
