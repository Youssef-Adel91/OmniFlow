/** Authenticated SSE lifecycle: fresh URL on retry and cancellation while awaiting auth. */
export type StreamStatus = "connecting" | "open" | "closed" | "error";

export function createInboxStream(options: {
  getUrl: () => Promise<string>;
  subscribe: (source: EventSource) => void;
  onStatus: (status: StreamStatus) => void;
  onOpen: () => void;
  createSource?: (url: string) => EventSource;
}) {
  let source: EventSource | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let generation = 0;
  let failures = 0;
  let stopped = false;

  function cleanup() {
    clearTimeout(timer);
    source?.close();
    source = null;
  }

  function retry(current: number) {
    if (stopped || current !== generation) return;
    cleanup();
    options.onStatus("error");
    timer = setTimeout(() => void connect(), Math.min(1000 * 2 ** failures++, 30000));
  }

  async function connect() {
    stopped = false;
    const current = ++generation;
    cleanup();
    options.onStatus("connecting");
    try {
      const url = await options.getUrl();
      if (stopped || current !== generation) return;
      const next = (options.createSource ?? ((address) => new EventSource(address)))(url);
      source = next;
      options.subscribe(next);
      next.addEventListener("open", () => {
        if (stopped || current !== generation || source !== next) return;
        failures = 0;
        options.onStatus("open");
        options.onOpen();
      });
      next.addEventListener("error", () => retry(current));
    } catch {
      retry(current);
    }
  }

  function disconnect() {
    stopped = true;
    generation++;
    failures = 0;
    cleanup();
    options.onStatus("closed");
  }

  return { connect, disconnect };
}
