"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { StreamingIndicator } from "@/components/workspace/streaming-indicator";
import { usePPTStream, type ThinkingStep } from "@/components/workspace/ppt/use-ppt-stream";
import { cn } from "@/lib/utils";

export default function PPTGenerationPage() {
  const searchParams = useSearchParams();
  const scrollRef = useRef<HTMLDivElement>(null);

  const taskId = searchParams.get("task_id");
  const topic = searchParams.get("topic") || "未知主题";
  const minSlides = searchParams.get("min_slides") || "6";
  const maxSlides = searchParams.get("max_slides") || "10";

  const params = useMemo(
    () => ({
      topic,
      course: topic,
      output_language: searchParams.get("output_language") || "中文",
      target_audience: searchParams.get("target_audience") || "通用受众",
      style: searchParams.get("style") || "",
      enable_web_search: searchParams.get("enable_web_search") || "false",
      image_mode: searchParams.get("image_mode") || "generate",
      page_limit: searchParams.get("page_limit") || maxSlides,
      model_provider: searchParams.get("model_provider") || "minmax",
      debug_layout: "false",
    }),
    [maxSlides, searchParams, topic],
  );

  const { steps, doneData, error, isStreaming, expectedTotal, restart } =
    usePPTStream({
      taskId: taskId || undefined,
      params: taskId ? undefined : params,
    });

  // Auto-scroll to bottom
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [steps, doneData]);

  const completedCount = steps.filter((s) => s.status === "done").length;
  const displayTotalSteps = expectedTotal > 0 ? expectedTotal : steps.length;

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
            {steps.map((step) => (
              <ThinkingStepCard key={`${step.step}-${step.node}`} step={step} />
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
                restart();
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
          )}
        >
          {step.status === "done" ? "✓" : "●"}
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
          {step.status === "done" ? "完成" : "进行中"}
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
