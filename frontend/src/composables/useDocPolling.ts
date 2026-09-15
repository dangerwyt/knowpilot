import { ref, onUnmounted } from "vue";

const POLL_MS = 3000;
const PENDING = ["parsing", "embedding"];

export const useDocPolling = (fetch: () => Promise<void>) => {
  let timer: ReturnType<typeof setInterval> | null = null;
  let busy = false;
  const isPolling = ref(false);

  const stop = () => {
    if (timer === null) return;
    clearInterval(timer);
    timer = null;
    isPolling.value = false;
  };

  const start = () => {
    if (timer !== null) return;
    timer = setInterval(async () => {
      if (busy) return;
      busy = true;
      try {
        await fetch();
      } finally {
        busy = false;
      }
    }, POLL_MS);
    isPolling.value = true;
  };

  const syncPending = (list: { status: string }[]) => {
    if (list.some((item) => PENDING.includes(item.status))) {
      start();
    } else {
      stop();
    }
  };

  onUnmounted(stop);

  return {
    stop,
    start,
    syncPending,
  };
};
