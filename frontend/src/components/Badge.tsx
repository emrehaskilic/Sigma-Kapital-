interface BadgeProps {
  status: "WS LIVE" | "REST" | "LIVE" | "STALE" | "CONNECTING" | "DISCONNECTED";
  label?: string;
}

const STYLES: Record<string, string> = {
  "WS LIVE": "bg-emerald-400/15 text-emerald-400 border-emerald-400/25",
  LIVE: "bg-emerald-400/15 text-emerald-400 border-emerald-400/25",
  REST: "bg-yellow-400/15 text-yellow-400 border-yellow-400/25",
  STALE: "bg-red-400/15 text-red-400 border-red-400/25",
  CONNECTING: "bg-amber-400/15 text-amber-400 border-amber-400/25",
  DISCONNECTED: "bg-slate-500/15 text-slate-500 border-slate-500/25",
};

export function Badge({ status, label }: BadgeProps) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-medium border ${STYLES[status] || STYLES.DISCONNECTED}`}
    >
      <span
        className={`w-1.5 h-1.5 rounded-full ${
          status === "WS LIVE" || status === "LIVE" ? "bg-emerald-400 animate-pulse" :
          status === "REST" ? "bg-yellow-400 animate-pulse" :
          status === "STALE" ? "bg-red-400" :
          status === "CONNECTING" ? "bg-amber-400 animate-pulse" :
          "bg-slate-500"
        }`}
      />
      {label || status}
    </span>
  );
}
