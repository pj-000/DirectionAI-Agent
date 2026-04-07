"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type MutableRefObject } from "react";

export interface ThinkingStep {
  step: number;
  node: string;
  content: string;
  status: "active" | "done";
  progressMessage?: string;
}

export interface PreviewData {
  markdown_content: string;
  completed_slides: number;
  total_slides: number;
  current_title: string;
}

export interface DoneData {
  markdown_content: string;
  display_url: string;
  download_url: string;
  preview_images: string[];
  preview_warning: string;
  biz_id?: string;
}

interface SnapshotData {
  steps: ThinkingStep[];
  preview: PreviewData | null;
  doneData: DoneData | null;
  error: string | null;
  expectedTotal: number;
  isStreaming: boolean;
}

type SSEEvent =
  | { event: "snapshot"; data: SnapshotData }
  | { event: "thinking_start"; data: { step: number; node: string } }
  | { event: "thinking_chunk"; data: string }
  | { event: "thinking_end"; data: { step?: number; node?: string } }
  | { event: "progress"; data: { step: number; total: number; message: string } }
  | { event: "preview"; data: PreviewData }
  | { event: "done"; data: DoneData }
  | { event: "error"; data: { detail: string } };

interface UsePPTStreamOptions {
  params?: Record<string, string | number | boolean>;
  taskId?: string;
  enabled?: boolean;
}

function closeEventSource(eventSourceRef: MutableRefObject<EventSource | null>) {
  eventSourceRef.current?.close();
  eventSourceRef.current = null;
}

export function usePPTStream({
  params,
  taskId,
  enabled = true,
}: UsePPTStreamOptions) {
  const [steps, setSteps] = useState<ThinkingStep[]>([]);
  const [preview, setPreview] = useState<PreviewData | null>(null);
  const [doneData, setDoneData] = useState<DoneData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isStreaming, setIsStreaming] = useState(true);
  const [expectedTotal, setExpectedTotal] = useState(0);

  const eventSourceRef = useRef<EventSource | null>(null);
  const terminalReceivedRef = useRef(false);

  const queryString = useMemo(() => {
    if (!params) return "";
    const queryParams = new URLSearchParams();
    for (const [key, value] of Object.entries(params).sort(([left], [right]) =>
      left.localeCompare(right),
    )) {
      queryParams.set(key, String(value));
    }
    return queryParams.toString();
  }, [params]);

  const finalizeActiveSteps = useCallback(() => {
    setSteps((prev) =>
      prev.map((item) =>
        item.status === "active" ? { ...item, status: "done" as const } : item,
      ),
    );
  }, []);

  const resetState = useCallback(() => {
    setSteps([]);
    setPreview(null);
    setDoneData(null);
    setError(null);
    setIsStreaming(true);
    setExpectedTotal(0);
  }, []);

  const handleEvent = useCallback(
    (msg: SSEEvent, eventSource: EventSource) => {
      if (terminalReceivedRef.current) return;

      switch (msg.event) {
        case "snapshot": {
          setSteps(msg.data.steps);
          setPreview(msg.data.preview);
          setDoneData(msg.data.doneData);
          setError(msg.data.error);
          setExpectedTotal(msg.data.expectedTotal);
          setIsStreaming(msg.data.isStreaming);
          if (!msg.data.isStreaming) {
            terminalReceivedRef.current = true;
            closeEventSource(eventSourceRef);
            finalizeActiveSteps();
          }
          break;
        }
        case "thinking_start": {
          const { step, node } = msg.data;
          setSteps((prev) => {
            const next = prev.map((item) =>
              item.status === "active" ? { ...item, status: "done" as const } : item,
            );
            const existingIndex = next.findIndex((item) => item.step === step);
            if (existingIndex >= 0) {
              const updated = [...next];
              const existing = updated[existingIndex]!;
              updated[existingIndex] = {
                ...existing,
                step: existing.step,
                node,
                status: "active",
              };
              return updated;
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
          const targetStep = msg.data.step;
          setSteps((prev) => {
            if (targetStep != null) {
              return prev.map((item) =>
                item.step === targetStep
                  ? { ...item, status: "done" as const }
                  : item,
              );
            }
            const active = [...prev].reverse().find((item) => item.status === "active");
            if (!active) return prev;
            return prev.map((item) =>
              item.step === active.step
                ? { ...item, status: "done" as const }
                : item,
            );
          });
          break;
        }
        case "progress": {
          const { step, total, message } = msg.data;
          if (total > 0) {
            setExpectedTotal((prev) => Math.max(prev, total));
          }
          setSteps((prev) => {
            const existingIndex = prev.findIndex((item) => item.step === step);
            if (existingIndex >= 0) {
              const updated = [...prev];
              const existing = updated[existingIndex]!;
              updated[existingIndex] = {
                ...existing,
                step: existing.step,
                node: existing.node || message,
                progressMessage: message,
                status:
                  existing.status === "active"
                    ? "done"
                    : existing.status,
              };
              return updated;
            }
            return [
              ...prev,
              {
                step: step > 0 ? step : prev.length + 1,
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
          setPreview(msg.data);
          break;
        }
        case "done": {
          terminalReceivedRef.current = true;
          setDoneData(msg.data);
          setIsStreaming(false);
          closeEventSource(eventSourceRef);
          finalizeActiveSteps();
          break;
        }
        case "error": {
          terminalReceivedRef.current = true;
          setError(msg.data.detail);
          setIsStreaming(false);
          closeEventSource(eventSourceRef);
          finalizeActiveSteps();
          break;
        }
      }

      if (msg.event === "done" || msg.event === "error") {
        eventSource.close();
      }
    },
    [finalizeActiveSteps],
  );

  const connect = useCallback(() => {
    closeEventSource(eventSourceRef);
    terminalReceivedRef.current = false;
    resetState();

    if (typeof window === "undefined") return;

    const pptagentBase = `${window.location.protocol}//${window.location.host}/pptagentapi`;
    const sseUrl = taskId
      ? `${pptagentBase}/stream_ppt/${encodeURIComponent(taskId)}`
      : `${pptagentBase}/stream_ppt?${queryString}`;
    const eventSource = new EventSource(sseUrl);
    eventSourceRef.current = eventSource;

    const parseJSON = <T,>(data: string): T | null => {
      try {
        return JSON.parse(data) as T;
      } catch {
        return null;
      }
    };

    eventSource.addEventListener("thinking_start", (event) => {
      const data = parseJSON<{ step: number; node: string }>((event as MessageEvent).data);
      if (data) {
        handleEvent({ event: "thinking_start", data }, eventSource);
      }
    });

    eventSource.addEventListener("snapshot", (event) => {
      const data = parseJSON<SnapshotData>((event as MessageEvent).data);
      if (data) {
        handleEvent({ event: "snapshot", data }, eventSource);
      }
    });

    eventSource.addEventListener("thinking_chunk", (event) => {
      handleEvent(
        { event: "thinking_chunk", data: (event as MessageEvent).data },
        eventSource,
      );
    });

    eventSource.addEventListener("thinking_end", (event) => {
      const data =
        parseJSON<{ step?: number; node?: string }>((event as MessageEvent).data) ?? {};
      handleEvent({ event: "thinking_end", data }, eventSource);
    });

    eventSource.addEventListener("progress", (event) => {
      const data = parseJSON<{ step: number; total: number; message: string }>(
        (event as MessageEvent).data,
      );
      if (data) {
        handleEvent({ event: "progress", data }, eventSource);
      }
    });

    eventSource.addEventListener("preview", (event) => {
      const data = parseJSON<PreviewData>((event as MessageEvent).data);
      if (data) {
        handleEvent({ event: "preview", data }, eventSource);
      }
    });

    eventSource.addEventListener("done", (event) => {
      const data = parseJSON<DoneData>((event as MessageEvent).data);
      if (data) {
        handleEvent({ event: "done", data }, eventSource);
      }
    });

    eventSource.addEventListener("error", (event) => {
      const data = parseJSON<{ detail: string }>((event as MessageEvent).data);
      if (data) {
        handleEvent({ event: "error", data }, eventSource);
      }
    });

    eventSource.onerror = () => {
      if (terminalReceivedRef.current) {
        closeEventSource(eventSourceRef);
      }
    };
  }, [handleEvent, queryString, resetState, taskId]);

  useEffect(() => {
    if (!enabled || (!taskId && !queryString)) {
      terminalReceivedRef.current = true;
      closeEventSource(eventSourceRef);
      setIsStreaming(false);
      return;
    }

    connect();
    return () => {
      terminalReceivedRef.current = true;
      closeEventSource(eventSourceRef);
    };
  }, [connect, enabled, queryString, taskId]);

  const restart = useCallback(() => {
    connect();
  }, [connect]);

  return {
    steps,
    preview,
    doneData,
    error,
    isStreaming,
    expectedTotal,
    restart,
  };
}
