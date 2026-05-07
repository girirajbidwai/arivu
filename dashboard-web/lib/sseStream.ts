// Minimal SSE-over-fetch consumer.
//
// EventSource is GET-only and we need POST-multipart, so we read the
// stream manually. Each `data: <json>\n\n` block is dispatched to the
// handler keyed by the most recent `event: <name>` line.

type Handler = (data: any) => void | Promise<void>;

export interface SSEHandlers {
  transcript?: Handler;
  brain_result?: Handler;
  audio_chunk?: Handler;
  done?: Handler;
  error?: Handler;
  // Catch-all for unknown events.
  any?: (event: string, data: any) => void;
}

export async function postSSE(
  url: string,
  body: FormData,
  handlers: SSEHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const r = await fetch(url, { method: "POST", body, signal });
  if (!r.ok || !r.body) {
    throw new Error(`${r.status} ${r.statusText}`);
  }
  const reader = r.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let event = "message";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // Split on the SSE event delimiter: blank line.
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const dispatched = await dispatch(block, handlers);
      if (dispatched === "stop") return;
    }
  }
  // Final flush.
  if (buffer.trim()) {
    await dispatch(buffer, handlers);
  }
}

async function dispatch(block: string, handlers: SSEHandlers): Promise<"stop" | undefined> {
  let event = "message";
  let dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
    // ignore id:/retry:/comments
  }
  if (dataLines.length === 0) return;
  let data: any;
  try {
    data = JSON.parse(dataLines.join("\n"));
  } catch {
    data = { raw: dataLines.join("\n") };
  }
  const h = (handlers as any)[event] as Handler | undefined;
  if (h) await h(data);
  if (handlers.any) handlers.any(event, data);
  if (event === "done") return "stop";
  return undefined;
}
