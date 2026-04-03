"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/** 单条统一时间线条目：合并 thinking 和 progress */
interface TimelineItem {
  step: number;
  title: string;
  thinkingContent: string;
  // Which parts are done
  thinkingDone: boolean;
  progressDone: boolean;
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
  | { event: "done"; data: DoneData }
  | { event: "error"; data: { detail: string } };

interface PPTStreamingInlineProps {
  params: Record<string, string | number | boolean>;
}

export function PPTStreamingInline({ params }: PPTStreamingInlineProps) {
  const [timeline, setTimeline] = useState<TimelineItem[]>([]);
  const [previewImages, setPreviewImages] = useState<string[]>([]);
  const [doneData, setDoneData] = useState<DoneData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isStreaming, setIsStreaming] = useState(true);
  const eventSourceRef = useRef<EventSource | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  // Track whether we've already received a terminal event to prevent reconnections
  const terminalReceivedRef = useRef(false);

  const connectSSE = useCallback(() => {
    if (terminalReceivedRef.current) return;

    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }

    setTimeline([]);
    setPreviewImages([]);
    setDoneData(null);
    setError(null);
    setIsStreaming(true);
    terminalReceivedRef.current = false;

    const pptagentBase = `${window.location.protocol}//${window.location.host}/pptagentapi`;
    const queryParams = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      queryParams.set(key, String(value));
    }
    const sseUrl = `${pptagentBase}/stream_ppt?${queryParams.toString()}`;
    const es = new EventSource(sseUrl);
    eventSourceRef.current = es;

    const handleEvent = (msg: SSEEvent) => {
      if (terminalReceivedRef.current) return;

      switch (msg.event) {
        case "thinking_start": {
          const { step, node } = msg.data;
          setTimeline((prev) => {
            const exists = prev.find((i) => i.step === step);
            if (exists) {
              // Update title if it was set by progress first
              return prev.map((i) =>
                i.step === step
                  ? { ...i, title: node, thinkingDone: false }
                  : i
              );
            }
            return [
              ...prev.map((i) =>
                i.step === step - 1 && !i.thinkingDone
                  ? { ...i, thinkingDone: true }
                  : i
              ),
              {
                step,
                title: node,
                thinkingContent: "",
                thinkingDone: false,
                progressDone: false,
              },
            ];
          });
          break;
        }
        case "thinking_chunk": {
          const text = msg.data as string;
          setTimeline((prev) => {
            const active = prev.find((i) => !i.thinkingDone);
            if (!active) return prev;
            return prev.map((i) =>
              i.step === active.step
                ? { ...i, thinkingContent: i.thinkingContent + text }
                : i
            );
          });
          break;
        }
        case "thinking_end": {
          const { step } = msg.data;
          setTimeline((prev) =>
            prev.map((i) =>
              i.step === step ? { ...i, thinkingDone: true } : i
            )
          );
          break;
        }
        case "progress": {
          const { step, message } = msg.data;
          setTimeline((prev) => {
            const exists = prev.find((i) => i.step === step);
            if (exists) {
              return prev.map((i) =>
                i.step === step ? { ...i, title: message, progressDone: true } : i
              );
            }
            // Progress came before thinking_start for this step
            // Mark previous item as thinking done
            const updated = prev.map((i) =>
              !i.thinkingDone ? { ...i, thinkingDone: true } : i
            );
            return [
              ...updated,
              {
                step,
                title: message,
                thinkingContent: "",
                thinkingDone: true,
                progressDone: false,
              },
            ];
          });
          break;
        }
        case "done": {
          terminalReceivedRef.current = true;
          const data = msg.data as DoneData;
          setDoneData(data);
          setPreviewImages(data.preview_images || []);
          setIsStreaming(false);
          es.close();
          eventSourceRef.current = null;
          // Mark all pending progress as done
          setTimeline((prev) =>
            prev.map((i) =>
              !i.progressDone ? { ...i, progressDone: true } : i
            )
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
          break;
        }
      }
    };

    es.addEventListener("thinking_start", (e) => {
      try {
        handleEvent({ event: "thinking_start", data: JSON.parse((e as MessageEvent).data) });
      } catch {}
    });
    es.addEventListener("thinking_chunk", (e) => {
      try {
        handleEvent({ event: "thinking_chunk", data: (e as MessageEvent).data as string });
      } catch {}
    });
    es.addEventListener("thinking_end", (e) => {
      try {
        handleEvent({ event: "thinking_end", data: JSON.parse((e as MessageEvent).data) });
      } catch {}
    });
    es.addEventListener("progress", (e) => {
      try {
        handleEvent({ event: "progress", data: JSON.parse((e as MessageEvent).data) });
      } catch {}
    });
    es.addEventListener("done", (e) => {
      try {
        handleEvent({ event: "done", data: JSON.parse((e as MessageEvent).data) });
      } catch {}
    });
    es.addEventListener("error", (e) => {
      try {
        handleEvent({ event: "error", data: JSON.parse((e as MessageEvent).data) });
      } catch {}
    });

    es.onerror = () => {
      if (terminalReceivedRef.current) es.close();
    };
  }, [params]);

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
  }, [timeline]);

  const doneCount = timeline.filter((i) => i.progressDone).length;
  const totalCount = timeline.length;

  if (error) {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
        <strong>错误：</strong>{error}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3" ref={scrollRef}>
      {/* 统一时间线 */}
      {timeline.length > 0 && (
        <div className="space-y-2">
          {timeline.map((item) => (
            <div
              key={item.step}
              className={`flex items-start gap-2 rounded-md border px-3 py-2 text-xs ${
                item.progressDone
                  ? "border-green-200 bg-green-50"
                  : item.thinkingDone
                    ? "border-blue-200 bg-blue-50"
                    : "border-blue-100 bg-blue-50"
              }`}
            >
              {/* 状态图标 */}
              <span
                className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[10px] ${
                  item.progressDone
                    ? "bg-green-200 text-green-700"
                    : item.thinkingDone
                      ? "bg-blue-200 text-blue-700"
                      : "bg-blue-100 text-blue-600"
                }`}
              >
                {item.progressDone
                  ? "✓"
                  : item.thinkingDone
                    ? "●"
                    : "○"}
              </span>

              <div className="min-w-0 flex-1">
                {/* 标题行 */}
                <div className="font-medium text-foreground">{item.title}</div>
                {/* 思考内容（仅在思考完成后显示，防止抖动） */}
                {item.thinkingContent && (
                  <div className="mt-1 whitespace-pre-wrap text-muted-foreground">
                    {item.thinkingContent}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* 生成中指示器 */}
      {isStreaming && totalCount > 0 && (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <div className="h-2 w-2 animate-pulse rounded-full bg-blue-500" />
          生成中... ({doneCount}/{totalCount})
        </div>
      )}

      {/* 预览图 */}
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

      {/* 下载按钮 */}
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
