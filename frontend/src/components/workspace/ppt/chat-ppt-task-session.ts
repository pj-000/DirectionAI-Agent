"use client";

import type { Message } from "@langchain/langgraph-sdk";

const ACTIVE_CHAT_PPT_TASK_KEY = "directionai.active-chat-ppt-task";
const AWAITING_CHAT_PPT_TASK_KEY = "directionai.awaiting-chat-ppt-task";
const PPTGEN_MARKER_RE = /__PPTGEN_START__(.+?)__PPTGEN_END__/s;

type ActiveChatPPTTasks = Record<string, string>;

function canUseSessionStorage() {
  return (
    typeof window !== "undefined" &&
    typeof window.sessionStorage !== "undefined"
  );
}

function readStoredTasks(): ActiveChatPPTTasks {
  if (!canUseSessionStorage()) {
    return {};
  }

  try {
    const raw = window.sessionStorage.getItem(ACTIVE_CHAT_PPT_TASK_KEY);
    if (!raw) {
      return {};
    }
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const tasks: ActiveChatPPTTasks = {};
    for (const [threadId, taskId] of Object.entries(parsed)) {
      if (typeof taskId === "string") {
        tasks[threadId] = taskId;
      }
    }
    return tasks;
  } catch {
    return {};
  }
}

function readAwaitingTasks(): Record<string, boolean> {
  if (!canUseSessionStorage()) {
    return {};
  }

  try {
    const raw = window.sessionStorage.getItem(AWAITING_CHAT_PPT_TASK_KEY);
    if (!raw) {
      return {};
    }
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const awaiting: Record<string, boolean> = {};
    for (const [threadId, value] of Object.entries(parsed)) {
      if (value === true) {
        awaiting[threadId] = true;
      }
    }
    return awaiting;
  } catch {
    return {};
  }
}

export function extractPPTTaskIdFromPayload(payload: unknown): string | null {
  if (
    payload &&
    typeof payload === "object" &&
    "task_id" in payload &&
    typeof payload.task_id === "string"
  ) {
    return payload.task_id;
  }

  const text = typeof payload === "string" ? payload : JSON.stringify(payload);
  if (!text) {
    return null;
  }

  const markerMatch = PPTGEN_MARKER_RE.exec(text);
  if (!markerMatch?.[1]) {
    return null;
  }

  try {
    const parsed = JSON.parse(markerMatch[1]) as { task_id?: unknown };
    return typeof parsed.task_id === "string" ? parsed.task_id : null;
  } catch {
    return null;
  }
}

export function readActiveChatPPTTask(threadId: string): string | null {
  return readStoredTasks()[threadId] ?? null;
}

export function writeActiveChatPPTTask(threadId: string, taskId: string) {
  if (!canUseSessionStorage()) {
    return;
  }

  const tasks = readStoredTasks();
  tasks[threadId] = taskId;
  window.sessionStorage.setItem(
    ACTIVE_CHAT_PPT_TASK_KEY,
    JSON.stringify(tasks),
  );
  clearAwaitingChatPPTTask(threadId);
}

export function clearActiveChatPPTTask(threadId: string, taskId?: string) {
  if (!canUseSessionStorage()) {
    return;
  }

  const tasks = readStoredTasks();
  const storedTaskId = tasks[threadId];
  if (!storedTaskId) {
    return;
  }
  if (taskId && storedTaskId !== taskId) {
    return;
  }

  delete tasks[threadId];
  if (Object.keys(tasks).length === 0) {
    window.sessionStorage.removeItem(ACTIVE_CHAT_PPT_TASK_KEY);
    return;
  }

  window.sessionStorage.setItem(
    ACTIVE_CHAT_PPT_TASK_KEY,
    JSON.stringify(tasks),
  );
  clearAwaitingChatPPTTask(threadId);
}

export function readAwaitingChatPPTTask(threadId: string): boolean {
  return readAwaitingTasks()[threadId] === true;
}

export function markAwaitingChatPPTTask(threadId: string) {
  if (!canUseSessionStorage()) {
    return;
  }

  const awaiting = readAwaitingTasks();
  awaiting[threadId] = true;
  window.sessionStorage.setItem(
    AWAITING_CHAT_PPT_TASK_KEY,
    JSON.stringify(awaiting),
  );
}

export function clearAwaitingChatPPTTask(threadId: string) {
  if (!canUseSessionStorage()) {
    return;
  }

  const awaiting = readAwaitingTasks();
  if (!(threadId in awaiting)) {
    return;
  }

  delete awaiting[threadId];
  if (Object.keys(awaiting).length === 0) {
    window.sessionStorage.removeItem(AWAITING_CHAT_PPT_TASK_KEY);
    return;
  }

  window.sessionStorage.setItem(
    AWAITING_CHAT_PPT_TASK_KEY,
    JSON.stringify(awaiting),
  );
}

function messageTextContent(message: Message): string {
  if (typeof message.content === "string") {
    return message.content;
  }
  if (Array.isArray(message.content)) {
    return message.content
      .map((part) => (part.type === "text" ? part.text : ""))
      .join("\n");
  }
  return "";
}

export function extractLatestPPTTaskIdFromMessages(
  messages: Message[],
): string | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (!message) {
      continue;
    }
    const taskId = extractPPTTaskIdFromPayload(messageTextContent(message));
    if (taskId) {
      return taskId;
    }
  }
  return null;
}
