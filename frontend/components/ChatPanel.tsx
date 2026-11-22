import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { askQuestion, attachDocument, removeDocument, uploadFile, DocumentInfo, Source } from "@/lib/api";
import SourceCard from "./SourceCard";
export interface Message { role: "user" | "assistant" | "attachment" | "attachment_removed"; content: string; sources?: Source[]; attachedFilenames?: string[]; }
const UPLOAD_STEPS = [
  { icon: "👀", label: "Skimming your pages" },
  { icon: "🧵", label: "Untangling tables" },
  { icon: "🔍", label: "Spotting figures" },
  { icon: "🧩", label: "Connecting the dots" },
  { icon: "✨", label: "Putting on the finishing touches" },
];
const ACCEPTED_TYPES = "application/pdf,image/png,image/jpeg,image/webp,image/gif";
const IMAGE_EXTENSION = /\.(png|jpe?g|webp|gif)$/i;
interface ChatPanelProps { sessionId: string; documents: DocumentInfo[]; messages: Message[]; onMessagesChange: (messages: Message[]) => void; onDocumentsChange: (documents: DocumentInfo[]) => void; }

function MarkdownHeading({ children }: { children?: React.ReactNode }) {
  return <h3 className="mt-5 font-display text-xl text-[var(--text-heading)] first:mt-0">{children}</h3>;
}

const MARKDOWN_COMPONENTS = {
  h1: MarkdownHeading,
  h2: MarkdownHeading,
  h3: MarkdownHeading,
  p: ({ children }: { children?: React.ReactNode }) => <p className="mb-3 last:mb-0">{children}</p>,
  ul: ({ children }: { children?: React.ReactNode }) => <ul className="my-3 list-disc space-y-1.5 pl-5 marker:text-[#b56c4f]">{children}</ul>,
  ol: ({ children }: { children?: React.ReactNode }) => <ol className="my-3 list-decimal space-y-1.5 pl-5 marker:text-[#b56c4f]">{children}</ol>,
  li: ({ children }: { children?: React.ReactNode }) => <li className="pl-1">{children}</li>,
  strong: ({ children }: { children?: React.ReactNode }) => <strong className="font-semibold text-[var(--text-heading)]">{children}</strong>,
  em: ({ children }: { children?: React.ReactNode }) => <em className="italic">{children}</em>,
  a: ({ children, href }: { children?: React.ReactNode; href?: string }) => <a href={href} target="_blank" rel="noreferrer" className="text-[#b96f53] underline hover:text-[#a45f46]">{children}</a>,
  code: ({ children }: { children?: React.ReactNode }) => <code className="rounded bg-[var(--bg-surface-2)] px-1 py-0.5 font-mono text-[0.85em]">{children}</code>,
  pre: ({ children }: { children?: React.ReactNode }) => <pre className="my-3 overflow-x-auto rounded-lg bg-[var(--bg-surface-2)] p-3 font-mono text-xs leading-5">{children}</pre>,
  table: ({ children }: { children?: React.ReactNode }) => <div className="my-3 overflow-x-auto"><table className="w-full border-collapse text-sm">{children}</table></div>,
  thead: ({ children }: { children?: React.ReactNode }) => <thead className="border-b border-[var(--border-strong)] text-left font-semibold text-[var(--text-heading)]">{children}</thead>,
  th: ({ children }: { children?: React.ReactNode }) => <th className="px-3 py-1.5">{children}</th>,
  td: ({ children }: { children?: React.ReactNode }) => <td className="border-t border-[var(--border-soft)] px-3 py-1.5">{children}</td>,
};

function AnswerContent({ content }: { content: string }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={MARKDOWN_COMPONENTS}>
      {content}
    </ReactMarkdown>
  );
}

export default function ChatPanel({ sessionId, documents, messages, onMessagesChange, onDocumentsChange }: ChatPanelProps) {
  const [question, setQuestion] = useState("");
  const [isAsking, setIsAsking] = useState(false);
  const [removingId, setRemovingId] = useState<string | null>(null);
  const [pendingAttachments, setPendingAttachments] = useState<DocumentInfo[]>([]);
  const [uploadState, setUploadState] = useState<{ fileName: string; progress: number; step: number } | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [isProcessingFreshDoc, setIsProcessingFreshDoc] = useState(false);
  const [processingStep, setProcessingStep] = useState(0);
  const [showDocuments, setShowDocuments] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const messagesRef = useRef(messages);

  useEffect(() => { messagesRef.current = messages; }, [messages]);
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, isAsking]);

  useEffect(() => {
    if (!isProcessingFreshDoc) return;
    const timer = window.setInterval(() => setProcessingStep((step) => (step + 1) % UPLOAD_STEPS.length), 1300);
    return () => window.clearInterval(timer);
  }, [isProcessingFreshDoc]);

  function pushMessage(message: Message) {
    const next = [...messagesRef.current, message];
    messagesRef.current = next;
    onMessagesChange(next);
    return next;
  }

  async function handleAsk(event: React.FormEvent) {
    event.preventDefault();
    const query = question.trim();
    if (!query || isAsking) return;
    const attachedFilenames = pendingAttachments.map((d) => d.filename);
    const isFreshDoc = pendingAttachments.length > 0;
    pushMessage({ role: "user", content: query, attachedFilenames: attachedFilenames.length ? attachedFilenames : undefined });
    setQuestion("");
    setPendingAttachments([]);
    setIsAsking(true);
    setProcessingStep(0);
    setIsProcessingFreshDoc(isFreshDoc);
    try {
      const result = await askQuestion(sessionId, query);
      pushMessage({ role: "assistant", content: result.response, sources: result.sources });
    } catch {
      pushMessage({ role: "assistant", content: "I couldn't answer that right now. Please try again." });
    } finally {
      setIsAsking(false);
      setIsProcessingFreshDoc(false);
    }
  }

  async function handleFileSelected(file: File) {
    const isPdf = file.name.toLowerCase().endsWith(".pdf");
    const isImage = IMAGE_EXTENSION.test(file.name);
    if (!isPdf && !isImage) {
      setUploadError("Choose a PDF or image file to continue.");
      return;
    }
    setUploadError(null);
    setUploadState({ fileName: file.name, progress: 12, step: 0 });
    const timer = window.setInterval(() => {
      setUploadState((current) =>
        current && current.progress < 90
          ? { ...current, progress: current.progress + 12, step: Math.min(current.step + 1, UPLOAD_STEPS.length - 1) }
          : current
      );
    }, 1300);
    try {
      const result = await uploadFile(file);
      const updatedDocuments = await attachDocument(sessionId, result.document_id, result.filename, result.content_type);
      onDocumentsChange(updatedDocuments);
      setPendingAttachments((current) => [...current, { document_id: result.document_id, filename: result.filename, content_type: result.content_type }]);
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Upload failed.");
    } finally {
      window.clearInterval(timer);
      setUploadState(null);
    }
  }

  async function handleFilesSelected(files: File[]) {
    for (const file of files) {
      await handleFileSelected(file);
    }
  }

  async function handleRemove(documentId: string) {
    if (removingId) return;
    setRemovingId(documentId);
    try {
      const updatedDocuments = await removeDocument(sessionId, documentId);
      onDocumentsChange(updatedDocuments);
      setPendingAttachments((current) => current.filter((d) => d.document_id !== documentId));
    } finally {
      setRemovingId(null);
    }
  }

  return (
    <section className="flex min-h-[calc(100dvh-6rem)] min-w-0 flex-col sm:min-h-[calc(100dvh-7.5rem)] md:min-h-[calc(100dvh-3.5rem)]">
      <div className="fixed right-5 top-16 z-30">
          <button
            type="button"
            onClick={() => setShowDocuments((v) => !v)}
            aria-label={`Show attached documents (${documents.length})`}
            aria-expanded={showDocuments}
            title={`Attached documents (${documents.length})`}
            className="theme-card relative z-20 grid h-9 w-9 place-items-center rounded-full border border-[var(--border-strong)] text-[var(--text-body)] shadow-sm transition hover:bg-[var(--bg-surface-2)]"
          >
            <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" className="block shrink-0">
              <path transform="rotate(40 12 12)" d="M10 8v9a2 2 0 0 0 4 0V6a3.5 3.5 0 0 0-7 0v11a5 5 0 0 0 10 0V8" />
            </svg>
          </button>
          {showDocuments && (
            <>
              <div className="fixed inset-0 z-10" onClick={() => setShowDocuments(false)} />
              <div className="absolute right-0 top-full z-20 mt-2 w-72 max-w-[calc(100vw-2.5rem)] overflow-hidden rounded-xl border border-[var(--border-strong)] bg-[var(--bg-surface)] shadow-xl">
                {documents.length === 0 ? (
                  <p className="p-4 text-sm text-[var(--text-muted)]">No documents attached yet.</p>
                ) : (
                  <ul className="max-h-72 divide-y divide-[var(--border-soft)] overflow-y-auto">
                    {documents.map((doc) => (
                      <li key={doc.document_id} className="flex items-center gap-2 px-3 py-2.5">
                        <span className="shrink-0">{doc.content_type === "image" ? "🖼️" : "📄"}</span>
                        <span className="min-w-0 flex-1 truncate text-sm text-[var(--text-body)]">{doc.filename}</span>
                        <button
                          type="button"
                          onClick={() => handleRemove(doc.document_id)}
                          disabled={removingId === doc.document_id}
                          aria-label={`Remove ${doc.filename}`}
                          className="grid h-6 w-6 shrink-0 place-items-center rounded-full text-[var(--text-muted)] transition hover:bg-[var(--bg-surface-2)] hover:text-[var(--text-body)] disabled:opacity-50"
                        >
                          {removingId === doc.document_id ? "…" : "✕"}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </>
          )}
      </div>

      <div className="min-w-0 flex-1 py-7 pr-12">
        <div className="mx-auto w-full max-w-5xl space-y-8">
          {messages.length === 0 && (
            <div className="py-24 text-center">
              <div className="mx-auto grid h-14 w-14 place-items-center rounded-full bg-[#f3e4d3] font-display text-2xl text-[#9b6046]">✦</div>
              <p className="mt-5 font-display text-3xl text-[var(--text-heading)]">{documents.length > 0 ? "Let's explore your documents" : "Let's chat"}</p>
              <p className="mt-3 text-sm text-[var(--text-muted)]">{documents.length > 0 ? "Ask for a summary, key points, or details from any attached file." : "Ask anything — attach a document or image anytime to search its contents."}</p>
            </div>
          )}
          {messages.filter((message) => message.role !== "attachment" && message.role !== "attachment_removed").map((message, index) => message.role === "user" ? (
            <div key={index} className="flex flex-col items-end gap-1.5">
              {message.attachedFilenames && message.attachedFilenames.length > 0 && (
                <div className="flex flex-wrap justify-end gap-1.5">
                  {message.attachedFilenames.map((name, i) => (
                    <span key={i} className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--border-strong)] bg-[var(--bg-surface)] px-3 py-1 text-xs text-[var(--text-muted)]">
                      <span>📎</span>
                      <span className="font-medium text-[var(--text-body)]">{name}</span>
                    </span>
                  ))}
                </div>
              )}
              <div className="max-w-[85%] break-words rounded-2xl rounded-br-md bg-[#dfe9e4] px-5 py-3.5 text-sm leading-6 text-[#2d4740]">{message.content}</div>
            </div>
          ) : (
            <article key={index} className="flex w-full gap-3 sm:gap-4">
              <div className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-[#b96f53] font-display text-sm text-white">D</div>
              <div className="min-w-0 flex-1 break-words pt-1 text-sm leading-7 text-[var(--text-body)]">
                <AnswerContent content={message.content} />
                {message.sources && message.sources.length > 0 && (
                  <div className="mt-5 border-t border-[var(--border-soft)] pt-3">
                    <p className="mb-2 text-xs font-medium uppercase tracking-wide text-[var(--text-muted)]">Sources</p>
                    <div className="flex flex-col gap-2">{message.sources.map((source, sourceIndex) => <SourceCard key={`${source.chunk_id}-${sourceIndex}`} source={source} />)}</div>
                  </div>
                )}
              </div>
            </article>
          ))}
          {isAsking && (
            <div className="flex items-center gap-3">
              <div className="grid h-9 w-9 place-items-center rounded-full bg-[#b96f53] font-display text-sm text-white">D</div>
              <div className="rounded-xl bg-[var(--bg-surface)] px-4 py-2.5 text-sm text-[var(--text-muted)] shadow-sm">
                {isProcessingFreshDoc ? (
                  <span className="inline-flex items-center gap-2">
                    <span className="animate-bounce">{UPLOAD_STEPS[processingStep].icon}</span>
                    {UPLOAD_STEPS[processingStep].label}…
                  </span>
                ) : (
                  "Thinking…"
                )}
              </div>
            </div>
          )}
          <div ref={bottomRef} className="scroll-mb-40" />
        </div>
      </div>

      <form onSubmit={handleAsk} className="sticky bottom-0 z-10 shrink-0 border-t border-[var(--border-strong)] bg-[var(--bg-page)] py-4">
        {uploadState && (
          <div className="mb-2 rounded-lg border border-[var(--border-strong)] bg-[var(--bg-surface-2)] px-3 py-2 text-xs text-[var(--text-body)]">
            <div className="flex items-center gap-2">
              <span className="inline-block animate-bounce text-base">{UPLOAD_STEPS[uploadState.step].icon}</span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate font-medium transition-all duration-300">{UPLOAD_STEPS[uploadState.step].label}</span>
                  <span className="shrink-0 font-mono text-[var(--text-muted)]">{uploadState.progress}%</span>
                </div>
                <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-[var(--border-strong)]">
                  <div className="h-full rounded-full bg-gradient-to-r from-[#e5b88a] to-[#b96f53] transition-all duration-500 ease-out" style={{ width: `${uploadState.progress}%` }} />
                </div>
                <p className="mt-1 truncate text-[10px] text-[var(--text-muted)]">{uploadState.fileName}</p>
              </div>
            </div>
          </div>
        )}
        {uploadError && <p className="mb-2 text-xs font-medium text-[#a64f46]">{uploadError}</p>}
        {pendingAttachments.length > 0 && (
          <div className="mb-2 flex flex-wrap items-center gap-2">
            {pendingAttachments.map((doc) => (
              <span key={doc.document_id} className="inline-flex max-w-full items-center gap-2 rounded-lg border border-[var(--border-strong)] bg-[var(--bg-surface-2)] py-1.5 pl-3 pr-2 text-xs text-[var(--text-body)]">
                <span className="shrink-0">{doc.content_type === "image" ? "🖼️" : "📎"}</span>
                <span className="truncate font-medium">{doc.filename}</span>
                <button
                  type="button"
                  onClick={() => handleRemove(doc.document_id)}
                  disabled={removingId === doc.document_id}
                  aria-label={`Remove ${doc.filename}`}
                  title="Remove document"
                  className="grid h-5 w-5 shrink-0 place-items-center rounded-full text-[var(--text-muted)] transition hover:bg-[var(--bg-surface)] hover:text-[var(--text-body)] disabled:opacity-50"
                >
                  {removingId === doc.document_id ? "…" : "✕"}
                </button>
              </span>
            ))}
          </div>
        )}
        <div className="flex items-center gap-2 rounded-xl border border-[var(--border-strong)] bg-[var(--bg-surface)] p-2 shadow-[0_8px_24px_rgba(55,64,60,0.06)] transition focus-within:border-[#9eb5ad] focus-within:ring-4 focus-within:ring-[#e6eee9]">
          <input
            ref={fileInputRef}
            type="file"
            accept={ACCEPTED_TYPES}
            multiple
            className="hidden"
            onChange={(event) => {
              const files = Array.from(event.target.files ?? []);
              if (files.length) handleFilesSelected(files);
              event.target.value = "";
            }}
          />
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={!!uploadState}
            aria-label="Attach a document or image"
            title="Attach a document or image"
            className="grid h-9 w-9 shrink-0 place-items-center rounded-lg text-lg text-[var(--text-muted)] transition hover:bg-[var(--bg-surface-2)] hover:text-[var(--text-body)] disabled:opacity-50"
          >
            📎
          </button>
          <input
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder={documents.length > 0 ? "Ask anything about your documents…" : "Ask anything…"}
            className="min-w-0 flex-1 bg-transparent px-3 py-2.5 text-sm text-[var(--text-heading)] outline-none placeholder:text-[var(--text-muted)]"
          />
          <button type="submit" disabled={isAsking || !question.trim()} className="rounded-lg bg-[#b96f53] px-5 py-2.5 text-sm font-medium text-white transition hover:bg-[#a45f46] disabled:cursor-not-allowed disabled:opacity-40">Send</button>
        </div>
      </form>
    </section>
  );
}
