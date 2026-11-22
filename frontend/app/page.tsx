"use client";

import { useEffect, useState } from "react";
import ChatPanel, { Message } from "@/components/ChatPanel";
import { DocumentInfo, SessionSummary, listSessions, getSession, deleteSession } from "@/lib/api";

export default function Home() {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [activeDocuments, setActiveDocuments] = useState<DocumentInfo[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [isDark, setIsDark] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<SessionSummary | null>(null);

  useEffect(() => {
    let cancelled = false;
    listSessions().then((items) => {
      if (!cancelled) setSessions(items);
    }).catch(() => { /* Keep the empty list when the server is unavailable. */ });
    return () => { cancelled = true; };
  }, []);

  async function refreshSessions() {
    try { setSessions(await listSessions()); } catch { /* leave list as-is on failure */ }
  }

  function startNewConversation() {
    setActiveSessionId(crypto.randomUUID());
    setActiveDocuments([]);
    setMessages([]);
  }

  async function openSession(session: SessionSummary) {
    const detail = await getSession(session.session_id);
    setActiveDocuments(detail.documents);
    setActiveSessionId(detail.session_id);
    setMessages(detail.messages.map((m) => ({ role: m.role, content: m.content, sources: m.sources })));
  }

  function handleMessagesChange(next: Message[]) {
    setMessages(next);
    refreshSessions();
  }

  function requestDeleteSession(event: React.MouseEvent, session: SessionSummary) {
    event.stopPropagation();
    setPendingDelete(session);
  }

  async function confirmDeleteSession() {
    const session = pendingDelete;
    if (!session) return;
    setPendingDelete(null);
    await deleteSession(session.session_id);
    setSessions((current) => current.filter((item) => item.session_id !== session.session_id));
    if (activeSessionId === session.session_id) {
      setActiveDocuments([]);
      setActiveSessionId(null);
      setMessages([]);
    }
  }

  return (
    <main className={`app-shell flex min-h-dvh bg-[#f5f2ec] font-body text-[#293138] ${isDark ? "app-dark" : ""}`}>
      <aside className="sticky top-0 hidden h-dvh w-72 shrink-0 self-start flex-col bg-[#29383d] p-4 text-[#f7f3eb] md:flex">
        <div className="mb-8 flex items-center gap-3 px-2 pt-1">
          <div className="grid h-9 w-9 place-items-center rounded-lg bg-[#e5b88a] font-display text-lg text-[#29383d]">D</div>
          <div><p className="font-display text-lg leading-none">Document Chatter</p><p className="mt-1 text-[10px] uppercase tracking-[0.14em] text-[#b5c6c2]">Your knowledge space</p></div>
        </div>
        <button onClick={startNewConversation} className="mb-3 flex w-full items-center gap-3 rounded-lg border border-white/15 bg-white/8 px-3.5 py-3 text-sm font-medium transition hover:bg-white/15"><span className="text-lg">+</span> New conversation</button>
        <p className="mb-2 px-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-[#aebfbc]">Conversations</p>
        <nav aria-label="Conversations" tabIndex={0} className="sidebar-scroll min-h-0 min-w-0 flex-1 space-y-1 overflow-y-auto overscroll-contain pr-2">
          {sessions.length === 0 && <p className="px-2 py-3 text-sm leading-6 text-[#aebfbc]">Ask a question to start a conversation.</p>}
          {sessions.map((session) => {
            const active = session.session_id === activeSessionId;
            return (
              <div key={session.session_id} className="group relative">
                <button onClick={() => openSession(session)} className={`w-full rounded-lg px-3 py-3 pr-9 text-left transition ${active ? "bg-[#e3ece8] text-[#29383d]" : "text-[#dce6e2] hover:bg-white/10"}`}>
                  <span className="block truncate text-sm font-medium">{session.title}</span>
                </button>
                <button
                  type="button"
                  onClick={(event) => requestDeleteSession(event, session)}
                  aria-label={`Delete conversation: ${session.title}`}
                  className={`absolute right-1.5 top-1/2 -translate-y-1/2 rounded-md p-1.5 text-sm opacity-0 transition group-hover:opacity-100 focus:opacity-100 ${active ? "text-[#29383d] hover:bg-black/10" : "text-[#dce6e2] hover:bg-white/15"}`}
                >
                  ✕
                </button>
              </div>
            );
          })}
        </nav>
        <p className="border-t border-white/10 pt-4 text-xs leading-5 text-[#aebfbc]">Sessions are stored on the server.</p>
      </aside>

      <section className="theme-surface relative min-w-0 flex flex-1 flex-col">
        <button onClick={() => setIsDark((value) => !value)} aria-label={`Switch to ${isDark ? "light" : "dark"} mode`} className="theme-card fixed right-5 top-4 z-40 grid h-9 w-9 place-items-center rounded-full border border-[#d6d0c5] bg-[#faf8f3] text-sm text-[#52635e] shadow-sm transition hover:scale-105">{isDark ? "☀" : "◐"}</button>
        <header className="theme-card flex h-16 shrink-0 items-center border-b border-[#dfd9cf] bg-[#faf8f3] px-5 md:hidden"><div className="grid h-9 w-9 place-items-center rounded-lg bg-[#e5b88a] font-display text-lg">D</div><span className="ml-3 font-display text-lg">Document Chatter</span></header>
        {activeSessionId ? (
          <div className="min-h-0 flex-1 px-5 py-4 sm:px-10 sm:py-7">
            <ChatPanel
              sessionId={activeSessionId}
              documents={activeDocuments}
              messages={messages}
              onMessagesChange={handleMessagesChange}
              onDocumentsChange={setActiveDocuments}
            />
          </div>
        ) : (
          <div className="flex flex-1 items-center justify-center p-5 sm:p-10">
            <div className="theme-card w-full max-w-lg rounded-2xl border border-[var(--border-strong)] p-10 text-center">
              <div className="mx-auto mb-6 grid h-14 w-14 place-items-center rounded-full bg-[#e3ece8] text-xl text-[#395c56]">✦</div>
              <p className="font-display text-3xl text-[var(--text-heading)]">Start a new conversation</p>
              <p className="mt-3 text-sm text-[var(--text-muted)]">Ask anything — attach documents once you&apos;re chatting to search their contents.</p>
              <button onClick={startNewConversation} className="mt-7 rounded-lg bg-[#b96f53] px-5 py-2.5 text-sm font-medium text-white transition hover:bg-[#a45f46]">New conversation</button>
            </div>
          </div>
        )}
      </section>

      {pendingDelete && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          onClick={() => setPendingDelete(null)}
        >
          <div
            className="theme-card w-full max-w-lg rounded-2xl border border-[var(--border-strong)] p-7 shadow-2xl"
            onClick={(event) => event.stopPropagation()}
          >
            <p className="font-display text-lg text-[var(--text-heading)]">Delete this conversation?</p>
            <p className="mt-2 text-sm text-[var(--text-muted)]">
              This removes &quot;{pendingDelete.title}&quot; and its chat history. This can&apos;t be undone.
            </p>
            <div className="mt-6 flex justify-end gap-3">
              <button
                onClick={() => setPendingDelete(null)}
                className="rounded-lg px-4 py-2 text-sm font-medium text-[var(--text-body)] transition hover:bg-[var(--bg-surface-2)]"
              >
                Cancel
              </button>
              <button
                onClick={confirmDeleteSession}
                className="rounded-lg bg-[#b96f53] px-4 py-2 text-sm font-medium text-white transition hover:bg-[#a45f46]"
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}
