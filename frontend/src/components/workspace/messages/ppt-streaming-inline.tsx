"use client";

import { useEffect, useRef } from "react";

import { usePPTStream } from "@/components/workspace/ppt/use-ppt-stream";

interface PPTStreamingInlineProps {
  params?: Record<string, string | number | boolean>;
  taskId?: string;
  enabled?: boolean;
  onTerminal?: () => void;
}

export function PPTStreamingInline({
  params,
  taskId,
  enabled = true,
  onTerminal,
}: PPTStreamingInlineProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const { steps, doneData, error, isStreaming, expectedTotal } = usePPTStream({
    params,
    taskId,
    enabled,
  });

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [steps, doneData]);

  useEffect(() => {
    if (!onTerminal) {
      return;
    }
    if (doneData || error) {
      onTerminal();
    }
  }, [doneData, error, onTerminal]);

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
        </div>
      )}
    </div>
  );
}
