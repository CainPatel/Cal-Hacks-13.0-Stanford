"use client";

import { useEffect, useState } from "react";
import { API_URL } from "@/lib/api";

const KINDS = ["fact", "kernel_trace", "signature", "execution"];

/** A counter that ticks whenever the event log changes, so views refetch while the fuzzer or a judge runs. */
export function useLive() {
  const [version, setVersion] = useState(0);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    const source = new EventSource(`${API_URL}/events?after=-1`);
    let timer: ReturnType<typeof setTimeout> | undefined;
    const bump = () => {
      clearTimeout(timer);
      timer = setTimeout(() => setVersion((v) => v + 1), 250); // a replay writes dozens of rows at once
    };
    KINDS.forEach((k) => source.addEventListener(k, bump));
    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false); // EventSource reconnects on its own, resuming from Last-Event-ID
    return () => {
      clearTimeout(timer);
      source.close();
    };
  }, []);

  return { version, connected };
}
