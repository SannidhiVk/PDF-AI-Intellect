"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import axios from "axios";
import { Send, Bot, User, Loader2, AlertCircle, Trash2, MessageSquare } from "lucide-react";
import { cn } from "@/lib/utils";

const FASTAPI_URL = process.env.NEXT_PUBLIC_FASTAPI_URL || "http://127.0.0.1:8000";

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: Date;
  isError?: boolean;
}

interface ChatWindowProps {
  documentId: string;
  filename: string;
}

export default function ChatWindow({ documentId, filename }: ChatWindowProps) {
  const [messages, setMessages] = useState<Message[]>([
    {
      id: "welcome",
      role: "assistant",
      content: `Hello! I've analyzed **${filename}**. Ask me anything about this document — I'll provide answers based on its content.`,
      timestamp: new Date(),
    },
  ]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Auto-scroll to bottom when messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isLoading]);

  // Auto-resize textarea
  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 160)}px`;
  }, [input]);

  const sendMessage = useCallback(async () => {
    const trimmed = input.trim();
    if (!trimmed || isLoading) return;

    const userMessage: Message = {
      id: `user-${Date.now()}`,
      role: "user",
      content: trimmed,
      timestamp: new Date(),
    };

    setMessages((prev) => [...prev, userMessage]);
    setInput("");
    setIsLoading(true);

    try {
      // Build chat history for backend (exclude welcome message)
      const history = messages
        .filter((m) => m.id !== "welcome" && !m.isError)
        .map((m) => ({ role: m.role, content: m.content }));

      const response = await axios.post(
        `${FASTAPI_URL}/api/chat`,
        {
          document_id: documentId,
          question: trimmed,
          chat_history: history,
        },
        { timeout: 60000 }
      );

      const assistantMessage: Message = {
        id: `assistant-${Date.now()}`,
        role: "assistant",
        content: response.data.answer || response.data.response || "I couldn't find an answer to that.",
        timestamp: new Date(),
      };

      setMessages((prev) => [...prev, assistantMessage]);
    } catch (err: unknown) {
      let errText = "Sorry, I encountered an error. Please try again.";
      if (axios.isAxiosError(err)) {
        errText = err.response?.data?.detail || errText;
      }

      setMessages((prev) => [
        ...prev,
        {
          id: `error-${Date.now()}`,
          role: "assistant",
          content: errText,
          timestamp: new Date(),
          isError: true,
        },
      ]);
    } finally {
      setIsLoading(false);
      textareaRef.current?.focus();
    }
  }, [input, isLoading, messages, documentId]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  const clearChat = () => {
    setMessages([
      {
        id: "welcome",
        role: "assistant",
        content: `Chat cleared. Ask me anything about **${filename}**.`,
        timestamp: new Date(),
      },
    ]);
  };

  return (
    <div className="flex h-full flex-col rounded-2xl border border-gray-800/60 bg-gray-900/60 backdrop-blur-sm overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-4 border-b border-gray-800/60 flex-shrink-0">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-violet-600 to-indigo-600 shadow-lg shadow-violet-900/20">
            <MessageSquare className="h-4 w-4 text-white" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-white">Chat with Document</h2>
            <p className="text-xs text-gray-500 max-w-xs truncate">{filename}</p>
          </div>
        </div>
        <button
          onClick={clearChat}
          title="Clear chat history"
          className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium text-gray-500 hover:bg-gray-800 hover:text-red-400 transition-all duration-150"
        >
          <Trash2 className="h-3.5 w-3.5" />
          Clear
        </button>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4 min-h-0">
        {messages.map((message) => (
          <MessageBubble key={message.id} message={message} />
        ))}

        {/* Thinking indicator */}
        {isLoading && (
          <div className="flex items-start gap-3">
            <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-violet-600 to-indigo-600">
              <Bot className="h-4 w-4 text-white" />
            </div>
            <div className="rounded-2xl rounded-tl-sm bg-gray-800/80 px-4 py-3">
              <div className="flex items-center gap-2">
                <Loader2 className="h-3.5 w-3.5 text-violet-400 animate-spin" />
                <span className="text-sm text-gray-400 italic">AI is thinking...</span>
                <span className="flex gap-0.5">
                  {[0, 1, 2].map((i) => (
                    <span
                      key={i}
                      className="h-1.5 w-1.5 rounded-full bg-violet-500 animate-bounce"
                      style={{ animationDelay: `${i * 0.15}s` }}
                    />
                  ))}
                </span>
              </div>
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Input Area */}
      <div className="border-t border-gray-800/60 px-4 py-4 flex-shrink-0">
        <div className="flex items-end gap-3 rounded-xl border border-gray-700 bg-gray-800/60 px-4 py-3 focus-within:border-violet-600/60 transition-colors duration-200">
          <textarea
            ref={textareaRef}
            id="chat-input"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask a question about the document..."
            rows={1}
            disabled={isLoading}
            className="flex-1 resize-none bg-transparent text-sm text-gray-200 placeholder-gray-600 focus:outline-none disabled:opacity-50 leading-relaxed"
            style={{ minHeight: "24px", maxHeight: "160px" }}
          />
          <button
            onClick={sendMessage}
            disabled={!input.trim() || isLoading}
            id="send-message-btn"
            className={cn(
              "flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg transition-all duration-200",
              input.trim() && !isLoading
                ? "bg-violet-600 text-white hover:bg-violet-500 shadow-lg shadow-violet-900/30"
                : "bg-gray-700 text-gray-600 cursor-not-allowed"
            )}
          >
            {isLoading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Send className="h-4 w-4" />
            )}
          </button>
        </div>
        <p className="mt-2 text-center text-xs text-gray-700">
          Press <kbd className="rounded bg-gray-800 px-1 py-0.5 font-mono text-gray-500">Enter</kbd> to send,{" "}
          <kbd className="rounded bg-gray-800 px-1 py-0.5 font-mono text-gray-500">Shift+Enter</kbd> for new line
        </p>
      </div>
    </div>
  );
}

function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === "user";

  // Simple bold markdown rendering
  const formatContent = (text: string) => {
    const parts = text.split(/(\*\*[^*]+\*\*)/g);
    return parts.map((part, i) => {
      if (part.startsWith("**") && part.endsWith("**")) {
        return (
          <strong key={i} className="font-semibold text-white">
            {part.slice(2, -2)}
          </strong>
        );
      }
      return <span key={i}>{part}</span>;
    });
  };

  return (
    <div
      className={cn(
        "flex items-start gap-3 animate-in fade-in slide-in-from-bottom-2 duration-300",
        isUser && "flex-row-reverse"
      )}
    >
      {/* Avatar */}
      <div
        className={cn(
          "flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full",
          isUser
            ? "bg-gradient-to-br from-sky-600 to-blue-600"
            : "bg-gradient-to-br from-violet-600 to-indigo-600"
        )}
      >
        {isUser ? (
          <User className="h-4 w-4 text-white" />
        ) : message.isError ? (
          <AlertCircle className="h-4 w-4 text-red-300" />
        ) : (
          <Bot className="h-4 w-4 text-white" />
        )}
      </div>

      {/* Bubble */}
      <div
        className={cn(
          "max-w-[80%] rounded-2xl px-4 py-3 text-sm leading-relaxed",
          isUser
            ? "rounded-tr-sm bg-violet-700/40 text-gray-100"
            : message.isError
            ? "rounded-tl-sm bg-red-900/30 text-red-300 border border-red-800/40"
            : "rounded-tl-sm bg-gray-800/80 text-gray-200"
        )}
      >
        {formatContent(message.content)}
        <p
          className={cn(
            "mt-1.5 text-xs",
            isUser ? "text-right text-violet-400/60" : "text-gray-600"
          )}
        >
          {message.timestamp.toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
          })}
        </p>
      </div>
    </div>
  );
}
