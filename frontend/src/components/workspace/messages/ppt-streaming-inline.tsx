"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

interface ThinkingStep {
  step: number;
  node: string;
  content: string;
  status: "active" | "done";
  progressMessage?: string;
}

interface DoneData {
  markdown_content: string;
  display_url: string;
  download_url: string;
  preview_images: string[];
  preview_warning: string;
}

type SSEEvent =
  | { event: "thinking_start"; data: { step: number; node: string } }
  | { event: "thinking_chunk"; data: string }
  | { event: "thinking_end"; data: { step: number; node: string } }
  | { event: "progress"; data: { step: number; total: number; message: string } }
  | { event: "preview"; data: unknown }
  | { event: "done"; data: DoneData }
  | { event: "error"; data: { detail: string } };

interface PPTStreamingInlineProps {
  params: Record<string, string | number | boolean>;
}

export function PPTStreamingInline({ params }: PPTStreamingInlineProps) {
  const [steps, setSteps] = useState<ThinkingStep[]>([]);
  const [previewImages, setPreviewImages] = useState<string[]>([]);
  const [doneData, setDoneData] = useState<DoneData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isStreaming, setIsStreaming] = useState(true);
  const [expectedTotal, setExpectedTotal] = useState(0);
  const eventSourceRef = useRef<EventSource | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const terminalReceivedRef = useRef(false);
  const queryString = useMemo(() => {
    const queryParams = new URLSearchParams();
    for (const [key, value] of Object.entries(params).sort(([left], [right]) =>
      left.localeCompare(right),
    )) {
      queryParams.set(key, String(value));
    }
    return queryParams.toString();
  }, [params]);

  const connectSSE = useCallback(() => {
    if (terminalReceivedRef.current) return;

    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }

    setSteps([]);
    setPreviewImages([]);
    setDoneData(null);
    setError(null);
    setIsStreaming(true);
    setExpectedTotal(0);
    terminalReceivedRef.current = false;

    const pptagentBase = `${window.location.protocol}//${window.location.host}/pptagentapi`;
    const sseUrl = `${pptagentBase}/stream_ppt?${queryString}`;
    const es = new EventSource(sseUrl);
    eventSourceRef.current = es;

    const handleEvent = (msg: SSEEvent) => {
      if (terminalReceivedRef.current) return;

      switch (msg.event) {
        case "thinking_start": {
          const { step, node } = msg.data;
          setSteps((prev) => {
            const next = prev.map((item) =>
              item.status === "active" ? { ...item, status: "done" as const } : item,
            );
            const exists = next.find((item) => item.step === step);
            if (exists) {
              return next.map((item) =>
                item.step === step
                  ? { ...item, node, status: "active" as const }
                  : item,
              );
            }
            return [
              ...next,
              {
                step,
                node,
                content: "",
                status: "active",
              },
            ];
          });
          break;
        }
        case "thinking_chunk": {
          const text = msg.data;
          setSteps((prev) => {
            const active = [...prev].reverse().find((item) => item.status === "active");
            if (!active) return prev;
            return prev.map((item) =>
              item.step === active.step
                ? { ...item, content: item.content + text }
                : item,
            );
          });
          break;
        }
        case "thinking_end": {
          const { step } = msg.data;
          setSteps((prev) =>
            prev.map((item) =>
              item.step === step ? { ...item, status: "done" as const } : item,
            ),
          );
          break;
        }
        case "progress": {
          const { step, total, message } = msg.data;
          if (total > 0) {
            setExpectedTotal((prev) => Math.max(prev, total));
          }
          setSteps((prev) => {
            const exists = prev.find((item) => item.step === step);
            if (exists) {
              return prev.map((item) =>
                item.step === step
                  ? {
                      ...item,
                      progressMessage: message,
                      status: item.status === "active" ? "done" : item.status,
                    }
                  : item,
              );
            }
            return [
              ...prev,
              {
                step,
                node: message,
                content: "",
                status: "done",
                progressMessage: message,
              },
            ];
          });
          break;
        }
        case "preview": {
          break;
        }
        case "done": {
          terminalReceivedRef.current = true;
          const data = msg.data;
          setDoneData(data);
          setPreviewImages(data.preview_images || []);
          setIsStreaming(false);
          es.close();
          eventSourceRef.current = null;
          setSteps((prev) =>
            prev.map((item) =>
              item.status === "active" ? { ...item, status: "done" as const } : item,
            ),
          );
          break;
        }
        case "error": {
          terminalReceivedRef.current = true;
          const { detail } = msg.data as { detail: string };
          setError(detail);
          setIsStreaming(false);
          es.close();
          eventSourceRef.current = null;
          setSteps((prev) =>
            prev.map((item) =>
              item.status === "active" ? { ...item, status: "done" as const } : item,
            ),
          );
          break;
        }
      }
    };

    es.addEventListener("thinking_start", (e) => {
      try {
        handleEvent({ event: "thinking_start", data: JSON.parse(e.data) });
      } catch {}
    });
    es.addEventListener("thinking_chunk", (e) => {
      try {
        handleEvent({ event: "thinking_chunk", data: e.data });
      } catch {}
    });
    es.addEventListener("thinking_end", (e) => {
      try {
        handleEvent({ event: "thinking_end", data: JSON.parse(e.data) });
      } catch {}
    });
    es.addEventListener("progress", (e) => {
      try {
        handleEvent({ event: "progress", data: JSON.parse(e.data) });
      } catch {}
    });
    es.addEventListener("preview", (e) => {
      try {
        handleEvent({ event: "preview", data: JSON.parse(e.data) });
      } catch {}
    });
    es.addEventListener("done", (e) => {
      try {
        handleEvent({ event: "done", data: JSON.parse(e.data) });
      } catch {}
    });
    es.addEventListener("error", (e) => {
      try {
        if ("data" in e && typeof e.data === "string") {
          handleEvent({ event: "error", data: JSON.parse(e.data) });
        }
      } catch {}
    });

    es.onerror = () => {
      if (terminalReceivedRef.current) es.close();
    };
  }, [queryString]);

  useEffect(() => {
    connectSSE();
    return () => {
      terminalReceivedRef.current = true;
      eventSourceRef.current?.close();
      eventSourceRef.current = null;
    };
  }, [connectSSE]);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [steps, previewImages, doneData]);

  const completedCount = steps.filter((item) => item.status === "done").length;
  const totalCount = expectedTotal > 0 ? expectedTotal : steps.length;

  if (error) {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
        <strong>错误：</strong>{error}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3" ref={scrollRef}>
      {steps.length > 0 && (
        <div className="space-y-2">
          {steps.map((item) => (
            <div
              key={item.step}
              className={`flex items-start gap-2 rounded-md border px-3 py-2 text-xs ${
                item.status === "done"
                  ? "border-green-200 bg-green-50"
                  : "border-blue-100 bg-blue-50"
              }`}
            >
              <span
                className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[10px] ${
                  item.status === "done"
                    ? "bg-green-200 text-green-700"
                    : "bg-blue-100 text-blue-600"
                }`}
              >
                {item.status === "done" ? "✓" : "●"}
              </span>

              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between gap-2">
                  <div className="font-medium text-foreground">{item.node}</div>
                  <span className="shrink-0 text-[11px] text-muted-foreground">
                    步骤 {item.step}
                  </span>
                </div>
                {item.progressMessage && item.progressMessage !== item.node && (
                  <div className="mt-1 text-[11px] text-green-700">
                    {item.progressMessage}
                  </div>
                )}
                {item.content && (
                  <div className="mt-1 whitespace-pre-wrap text-muted-foreground">
                    {item.content}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {isStreaming && totalCount > 0 && (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <div className="h-2 w-2 animate-pulse rounded-full bg-blue-500" />
          生成中... ({completedCount}/{totalCount})
        </div>
      )}

      {doneData && previewImages.length > 0 && (
        <div className="space-y-2">
          <div className="text-xs font-medium text-muted-foreground">
            共 {previewImages.length} 页预览
          </div>
          <div className="grid grid-cols-3 gap-2">
            {previewImages.map((url, idx) => (
              <img
                key={idx}
                src={url}
                alt={`第 ${idx + 1} 页`}
                className="w-full rounded-md border"
                loading="lazy"
              />
            ))}
          </div>
        </div>
      )}

      {doneData && (
        <div className="flex items-center gap-2">
          <a
            href={doneData.download_url || doneData.display_url || "#"}
            target="_blank"
            rel="noopener noreferrer"
            className="rounded-lg bg-blue-500 px-4 py-2 text-sm font-medium text-white hover:bg-blue-600"
          >
            📥 导出 PPT
          </a>
          {doneData.preview_warning && (
            <span className="text-xs text-yellow-600">{doneData.preview_warning}</span>
          )}
        </div>
      )}
    </div>
  );
}
