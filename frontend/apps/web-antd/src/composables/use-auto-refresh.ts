import { onMounted, onUnmounted } from 'vue';

interface AutoRefreshOptions {
  immediate?: boolean;
  pauseWhenHidden?: boolean;
}

export function useAutoRefresh(
  callback: () => void | Promise<void>,
  intervalMs: number,
  options: AutoRefreshOptions = {},
) {
  const immediate = options.immediate ?? false;
  const pauseWhenHidden = options.pauseWhenHidden ?? true;
  let timer: ReturnType<typeof globalThis.setInterval> | undefined;
  let running = false;

  async function run() {
    if (running) {
      return;
    }
    running = true;
    try {
      await callback();
    } finally {
      running = false;
    }
  }

  function tick() {
    if (pauseWhenHidden && typeof document !== 'undefined' && document.visibilityState === 'hidden') {
      return;
    }
    void run();
  }

  function start() {
    if (timer || intervalMs <= 0) {
      return;
    }
    if (immediate) {
      void run();
    }
    timer = window.setInterval(tick, intervalMs) as unknown as ReturnType<typeof globalThis.setInterval>;
  }

  function stop() {
    if (!timer) {
      return;
    }
    window.clearInterval(timer);
    timer = undefined;
  }

  onMounted(start);
  onUnmounted(stop);

  return {
    refresh: run,
    start,
    stop,
  };
}
