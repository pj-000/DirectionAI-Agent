"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { StreamingIndicator } from "@/components/workspace/streaming-indicator";
import { cn } from "@/lib/utils";

interface ThinkingStep {
  step: number;
  node: string;
  content: string;
  status: "pending" | "active" | "done";
  _isScheduling?: boolean;
}

interface PreviewData {
  markdown_content: string;
  completed_slides: number;
  total_slides: number;
  current_title: string;
}

interface DoneData {
  markdown_content: string;
  display_url: string;
  download_url: string;
  preview_images: string[];
  preview_warning: string;
  biz_id: string;
}

type SSEEvent =
  | { event: "thinking_start"; data: { step: number; node: string } }
  | { event: "thinking_chunk"; data: string }
  | { event: "thinking_end"; data: { step: number; node: string } }
  | { event: "progress"; data: { step: number; total: number; message: string } }
  | { event: "preview"; data: PreviewData }
  | { event: "done"; data: DoneData }
  | { event: "error"; data: { detail: string } };

export default function PPTGenerationPage() {
  const searchParams = useSearchParams();
  const [steps, setSteps] = useState<ThinkingStep[]>([]);
  const [preview, setPreview] = useState<PreviewData | null>(null);
  const [doneData, setDoneData] = useState<DoneData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isStreaming, setIsStreaming] = useState(true);
  const [expectedTotal, setExpectedTotal] = useState(0);
  const eventSourceRef = useRef<EventSource | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const topic = searchParams.get("topic") || "未知主题";
  const minSlides = searchParams.get("min_slides") || "6";
  const maxSlides = searchParams.get("max_slides") || "10";

  // Determine pptagent URL - in production this would be configured
  const pptagentBase =
    typeof window !== "undefined"
      ? `${window.location.protocol}//${window.location.host}/pptagentapi`
      : "";

  const connectSSE = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }

    // Build the SSE URL with query params
    const params = new URLSearchParams({
      topic: topic,
      course: topic,
      output_language: searchParams.get("output_language") || "中文",
      target_audience: searchParams.get("target_audience") || "通用受众",
      style: searchParams.get("style") || "",
      enable_web_search: searchParams.get("enable_web_search") || "false",
      image_mode: searchParams.get("image_mode") || "generate",
      page_limit: searchParams.get("page_limit") || maxSlides,
      model_provider: searchParams.get("model_provider") || "minmax",
      debug_layout: "false",
    });

    const sseUrl = `${pptagentBase}/stream_ppt?${params.toString()}`;
    const es = new EventSource(sseUrl);
    eventSourceRef.current = es;

    es.onmessage = (event) => {
      const lines = event.data.split("\n");
      let eventType = "";
      let eventData = "";

      for (const line of lines) {
        if (line.startsWith("event:")) {
          eventType = line.slice(6).trim();
        } else if (line.startsWith("data:")) {
          eventData = line.slice(5).trim();
        }
      }

      if (!eventType || !eventData) return;

      // Parse JSON data
      let parsed: Record<string, unknown>;
      try {
        parsed = JSON.parse(eventData);
      } catch {
        return;
      }

      handleSSEEvent({ event: eventType, data: parsed } as SSEEvent);
    };

    es.addEventListener("thinking_start", (event) => {
      try {
        const data = JSON.parse((event as MessageEvent).data);
        handleSSEEvent({
          event: "thinking_start",
          data,
        });
      } catch {
        // ignore
      }
    });

    es.addEventListener("thinking_chunk", (event) => {
      const text = (event as MessageEvent).data;
      handleSSEEvent({ event: "thinking_chunk", data: text });
    });

    es.addEventListener("thinking_end", (event) => {
      try {
        const data = JSON.parse((event as MessageEvent).data);
        handleSSEEvent({ event: "thinking_end", data });
      } catch {
        // ignore
      }
    });

    es.addEventListener("progress", (event) => {
      try {
        const data = JSON.parse((event as MessageEvent).data);
        handleSSEEvent({ event: "progress", data });
      } catch {
        // ignore
      }
    });

    es.addEventListener("preview", (event) => {
      try {
        const data = JSON.parse((event as MessageEvent).data);
        handleSSEEvent({ event: "preview", data });
      } catch {
        // ignore
      }
    });

    es.addEventListener("done", (event) => {
      try {
        const data = JSON.parse((event as MessageEvent).data);
        handleSSEEvent({ event: "done", data });
      } catch {
        // ignore
      }
    });

    es.addEventListener("error", (event) => {
      const data = (event as MessageEvent).data;
      try {
        const parsed = JSON.parse(data);
        handleSSEEvent({ event: "error", data: parsed });
      } catch {
        setError("连接断开或发生错误");
        setIsStreaming(false);
      }
    });

    es.onerror = () => {
      // EventSource onerror is called for disconnects - don't treat as fatal
    };
  }, [topic, maxSlides, pptagentBase]);

  const handleSSEEvent = (msg: SSEEvent) => {
    switch (msg.event) {
      case "thinking_start": {
        const { step, node } = msg.data as { step: number; node: string };
        setSteps((prev) => {
          // Remove any scheduling step that was added
          const withoutScheduling = prev.filter((s) => !s._isScheduling);
          const normalizedStep = step > 0 ? step : withoutScheduling.length + 1;
          const last = withoutScheduling[withoutScheduling.length - 1];
          if (last && last.status === "active" && !last._isScheduling) {
            last.status = "done";
          }
          return [
            ...withoutScheduling,
            {
              step: normalizedStep,
              node,
              content: "",
              status: "active" as const,
            },
          ];
        });
        break;
      }

      case "thinking_chunk": {
        const text = msg.data as string;
        setSteps((prev) => {
          const updated = [...prev];
          const last = updated[updated.length - 1];
          if (last && last.status === "active") {
            last.content += text;
          }
          return updated;
        });
        break;
      }

      case "thinking_end": {
        setSteps((prev) => {
          const updated = [...prev];
          const last = updated[updated.length - 1];
          if (last && last.status === "active") {
            last.status = "done";
          }
          return updated;
        });
        break;
      }

      case "progress": {
        const { step, total, message } = msg.data as {
          step: number;
          total: number;
          message: string;
        };
        if (total > 0) {
          setExpectedTotal((prev) => Math.max(prev, total));
        }
        setSteps((prev) => {
          const updated = [...prev];
          const last = updated[updated.length - 1];
          if (last && last.status === "active" && !last._isScheduling) {
            last.status = "done";
          }
          const normalizedStep = step > 0 ? step : updated.filter((s) => !s._isScheduling).length + 1;
          const isFinal = step === total;
          if (!isFinal) {
            updated.push({
              step: normalizedStep,
              node: message,
              content: "",
              status: "active" as const,
              _isScheduling: true,
            });
          }
          return updated;
        });
        break;
      }

      case "preview": {
        const data = msg.data as PreviewData;
        setPreview(data);
        break;
      }

      case "done": {
        const data = msg.data as DoneData;
        setDoneData(data);
        setIsStreaming(false);
        eventSourceRef.current?.close();
        // Mark any active step as done
        setSteps((prev) =>
          prev.map((s) => (s.status === "active" ? { ...s, status: "done" as const } : s))
        );
        break;
      }

      case "error": {
        const { detail } = msg.data as { detail: string };
        setError(detail);
        setIsStreaming(false);
        eventSourceRef.current?.close();
        setSteps((prev) =>
          prev.map((s) => (s.status === "active" ? { ...s, status: "done" as const } : s))
        );
        break;
      }
    }
  };

  useEffect(() => {
    connectSSE();
    return () => {
      eventSourceRef.current?.close();
    };
  }, [connectSSE]);

  // Auto-scroll to bottom
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [steps, preview]);

  const completedCount = steps.filter((s) => s.status === "done" && !s._isScheduling).length;
  const displayTotalSteps = expectedTotal > 0 ? expectedTotal : steps.filter((s) => !s._isScheduling).length;

  return (
    <div className="flex h-full flex-col bg-background">
      {/* Header */}
      <header className="flex h-12 shrink-0 items-center justify-between border-b px-4">
        <div className="flex items-center gap-3">
          <a href="/workspace/chats" className="text-muted-foreground hover:text-foreground">
            ← 返回
          </a>
          <span className="text-sm font-medium">PPT 生成</span>
        </div>
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <span>主题：{topic}</span>
          <span>·</span>
          <span>{minSlides}-{maxSlides} 页</span>
        </div>
      </header>

      {/* Main content */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* Thinking Process Area */}
        <div
          ref={scrollRef}
          className="m-4 flex-1 overflow-y-auto rounded-xl border border-border/50 bg-card p-4 shadow-sm"
        >
          {/* Header */}
          <div className="mb-4 flex items-center justify-between">
            <div className="flex items-center gap-2">
              {isStreaming && <StreamingIndicator size="sm" />}
              <h3 className="text-sm font-semibold">
                {isStreaming
                  ? "正在生成中..."
                  : error
                    ? "生成出错"
                    : "✅ 生成完成"}
              </h3>
            </div>
            {completedCount > 0 && displayTotalSteps > 0 && (
              <span className="text-xs text-muted-foreground">
                {completedCount} / {displayTotalSteps} 步
              </span>
            )}
          </div>

          {/* Steps */}
          <div className="space-y-3">
            {steps
              .filter((s) => !s._isScheduling)
              .map((step) => (
                <ThinkingStepCard key={step.step} step={step} />
              ))}
          </div>

          {/* Error state */}
          {error && (
            <div className="mt-4 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
              <strong>错误：</strong>
              {error}
            </div>
          )}
        </div>

        {/* Preview Area */}
        {preview && !doneData && (
          <div className="mx-4 mb-4 flex-shrink-0 rounded-xl border border-border/50 bg-card p-4 shadow-sm">
            <div className="mb-2 flex items-center justify-between">
              <h3 className="text-sm font-semibold">
                预览 ({preview.completed_slides} / {preview.total_slides} 页)
              </h3>
              {preview.current_title && (
                <span className="text-xs text-muted-foreground">
                  当前：{preview.current_title}
                </span>
              )}
            </div>
            {preview.markdown_content && (
              <div
                className="prose prose-sm max-h-48 overflow-y-auto rounded-lg bg-muted/50 p-3 text-xs"
                dangerouslySetInnerHTML={{ __html: renderMarkdown(preview.markdown_content) }}
              />
            )}
          </div>
        )}

        {/* Preview Images */}
        {doneData && doneData.preview_images && doneData.preview_images.length > 0 && (
          <div className="mx-4 mb-4 flex-shrink-0 rounded-xl border border-border/50 bg-card p-4 shadow-sm">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-sm font-semibold">幻灯片预览</h3>
              <span className="text-xs text-muted-foreground">
                共 {doneData.preview_images.length} 页
              </span>
            </div>
            <div className="grid grid-cols-2 gap-3 overflow-y-auto md:grid-cols-3 lg:grid-cols-4">
              {doneData.preview_images.map((url, index) => (
                <div
                  key={index}
                  className="overflow-hidden rounded-lg border border-border bg-muted/50"
                >
                  <img
                    src={url}
                    alt={`第 ${index + 1} 页`}
                    className="w-full"
                    loading="lazy"
                  />
                  <div className="border-t px-2 py-1 text-center text-xs text-muted-foreground">
                    第 {index + 1} 页
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Done State Actions */}
        {doneData && (
          <div className="mx-4 mb-4 flex items-center gap-3">
            <a
              href={doneData.download_url || doneData.display_url || "#"}
              target="_blank"
              rel="noopener noreferrer"
              className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90"
            >
              📥 导出 PPT
            </a>
            <button
              onClick={() => {
                setDoneData(null);
                setPreview(null);
                setSteps([]);
                setIsStreaming(true);
                connectSSE();
              }}
              className="rounded-lg border border-border bg-background px-4 py-2 text-sm hover:bg-muted"
            >
              重新生成
            </button>
            <span className="ml-auto text-xs text-muted-foreground">
              本内容由 AI 生成，仅供参考
            </span>
          </div>
        )}

        {/* Preview Warning */}
        {doneData && doneData.preview_warning && (
          <div className="mx-4 mb-4 rounded-lg border border-yellow-200 bg-yellow-50 p-3 text-xs text-yellow-800">
            ⚠️ {doneData.preview_warning}
          </div>
        )}
      </div>
    </div>
  );
}

function ThinkingStepCard({ step }: { step: ThinkingStep }) {
  const [expanded, setExpanded] = useState(step.status === "active");

  return (
    <div className={cn("rounded-lg border", step.status === "done" ? "border-border/50 bg-muted/30" : "border-primary/20 bg-primary/5")}>
      <div
        className="flex cursor-pointer items-center gap-2 px-3 py-2"
        onClick={() => setExpanded(!expanded)}
      >
        {/* Status badge */}
        <div
          className={cn(
            "flex h-5 w-5 items-center justify-center rounded-full text-xs",
            step.status === "done" && "bg-green-100 text-green-700",
            step.status === "active" && "bg-primary/20 text-primary",
            step.status === "pending" && "bg-muted text-muted-foreground"
          )}
        >
          {step.status === "done" ? "✓" : step.status === "active" ? "●" : step.step}
        </div>

        {/* Step info */}
        <div className="flex-1">
          <span className="text-xs text-muted-foreground">步骤 {step.step}</span>
          <span className="ml-2 text-sm font-medium">{step.node}</span>
        </div>

        {/* Status */}
        <span
          className={cn(
            "rounded px-2 py-0.5 text-xs",
            step.status === "done" && "bg-green-100 text-green-700",
            step.status === "active" && "bg-primary/20 text-primary"
          )}
        >
          {step.status === "done" ? "完成" : step.status === "active" ? "进行中" : ""}
        </span>
      </div>

      {/* Content */}
      {step.content && expanded && (
        <div className="border-t border-border/30 px-3 py-2">
          <p className="whitespace-pre-wrap text-xs text-muted-foreground">{step.content}</p>
        </div>
      )}
    </div>
  );
}

// Simple markdown rendering for preview
function renderMarkdown(md: string): string {
  if (!md) return "";

  let html = md
    // Headers
    .replace(/^### (.+)$/gm, "<h3>$1</h3>")
    .replace(/^## (.+)$/gm, "<h2>$1</h2>")
    .replace(/^# (.+)$/gm, "<h1>$1</h1>")
    // Bold
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    // Lists
    .replace(/^- (.+)$/gm, "<li>$1</li>")
    // Code
    .replace(/`(.+?)`/g, "<code>$1</code>")
    // Line breaks
    .replace(/\n\n/g, "</p><p>")
    .replace(/\n/g, "<br>");

  // Wrap list items
  html = html.replace(/(<li>.+<\/li>)/gs, "<ul>$1</ul>");
  html = html.replace(/<\/ul>\s*<ul>/g, "");

  return `<p>${html}</p>`;
}
