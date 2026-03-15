import { useEffect, useRef, useState } from "react";
import {
  createChart,
  ColorType,
  CrosshairMode,
  type IChartApi,
  type ISeriesApi,
  type Time,
  type SeriesMarker,
} from "lightweight-charts";

interface Candle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  pmax: number | null;
  mavg: number | null;
  kc_upper: number | null;
  kc_lower: number | null;
  direction: number;
}

interface MarkerData {
  time: number;
  position: string;
  color: string;
  shape: string;
  text: string;
  price: number;
}

interface ChartDataResponse {
  candles: Candle[];
  markers: MarkerData[];
  grid_levels: { price: number; label: string; filled: boolean }[];
  error?: string;
}

interface Props {
  symbol: string;
  botRunning: boolean;
}

const BASE = "http://localhost:8000";

export function PMaxChart({ symbol, botRunning }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const pmaxRef = useRef<ISeriesApi<"Line"> | null>(null);
  const kcUpperRef = useRef<ISeriesApi<"Line"> | null>(null);
  const kcLowerRef = useRef<ISeriesApi<"Line"> | null>(null);
  const [loading, setLoading] = useState(true);
  const pollRef = useRef<number | null>(null);

  // Create chart once
  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: "#0b1217" },
        textColor: "#94a3b8",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: "#1e293b30" },
        horzLines: { color: "#1e293b30" },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: "#1e293b" },
      timeScale: {
        borderColor: "#1e293b",
        timeVisible: true,
        secondsVisible: false,
      },
      width: containerRef.current.clientWidth,
      height: 500,
    });

    const candle = chart.addCandlestickSeries({
      upColor: "#22c55e",
      downColor: "#ef4444",
      borderUpColor: "#22c55e",
      borderDownColor: "#ef4444",
      wickUpColor: "#22c55e80",
      wickDownColor: "#ef444480",
    });

    const pmax = chart.addLineSeries({
      color: "#ef4444",
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });

    const kcUpper = chart.addLineSeries({
      color: "#f59e0b80",
      lineWidth: 1,
      lineStyle: 2,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });

    const kcLower = chart.addLineSeries({
      color: "#f59e0b80",
      lineWidth: 1,
      lineStyle: 2,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });

    chartRef.current = chart;
    candleRef.current = candle;
    pmaxRef.current = pmax;
    kcUpperRef.current = kcUpper;
    kcLowerRef.current = kcLower;

    const onResize = () => {
      if (containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth });
      }
    };
    window.addEventListener("resize", onResize);

    return () => {
      window.removeEventListener("resize", onResize);
      if (pollRef.current) clearInterval(pollRef.current);
      chart.remove();
    };
  }, []);

  // Fetch + render
  const fetchData = async () => {
    const cs = candleRef.current;
    const ps = pmaxRef.current;
    const ku = kcUpperRef.current;
    const kl = kcLowerRef.current;
    if (!cs || !ps) return;

    try {
      const res = await fetch(`${BASE}/api/chart-data?symbol=${symbol}&limit=500`);
      const data: ChartDataResponse = await res.json();
      if (data.error) return;

      // Candles
      cs.setData(
        data.candles.map((c) => ({
          time: c.time as Time,
          open: c.open,
          high: c.high,
          low: c.low,
          close: c.close,
        }))
      );

      // PMax
      const pmaxPts = data.candles
        .filter((c) => c.pmax != null)
        .map((c) => ({ time: c.time as Time, value: c.pmax! }));
      ps.setData(pmaxPts);

      const lastDir = data.candles[data.candles.length - 1]?.direction ?? 0;
      ps.applyOptions({ color: lastDir === 1 ? "#22c55e" : "#ef4444" });

      // Keltner Channel bands
      if (ku) {
        const kcUpperPts = data.candles
          .filter((c) => c.kc_upper != null)
          .map((c) => ({ time: c.time as Time, value: c.kc_upper! }));
        ku.setData(kcUpperPts);
      }
      if (kl) {
        const kcLowerPts = data.candles
          .filter((c) => c.kc_lower != null)
          .map((c) => ({ time: c.time as Time, value: c.kc_lower! }));
        kl.setData(kcLowerPts);
      }

      // Markers
      if (data.markers.length > 0) {
        const markers: SeriesMarker<Time>[] = data.markers.map((m) => ({
          time: m.time as Time,
          position: m.position as "aboveBar" | "belowBar" | "inBar",
          color: m.color,
          shape: m.shape as "circle" | "arrowUp" | "arrowDown" | "square",
          text: m.text,
        }));
        cs.setMarkers(markers);
      }

      setLoading(false);
    } catch {
      // ignore
    }
  };

  useEffect(() => {
    fetchData();
    if (botRunning) {
      pollRef.current = window.setInterval(fetchData, 10000);
    }
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [symbol, botRunning]);

  return (
    <div className="bg-[#131d2a]/80 rounded-xl border border-slate-700/20 p-4">
      <div className="flex items-center justify-between mb-2">
        <h2 className="text-sm font-semibold text-slate-300">{symbol} - 3m PMax</h2>
        <div className="flex items-center gap-3 text-[10px] text-slate-500">
          <span className="flex items-center gap-1">
            <span className="w-3 h-0.5 bg-[#22c55e] inline-block" /> PMax
          </span>
          <span className="flex items-center gap-1">
            <span className="w-3 h-0.5 bg-[#f59e0b] inline-block opacity-50" /> KC
          </span>
          <span className="text-emerald-400">&#9650; DCA</span>
          <span className="text-blue-400">&#9679; TP</span>
        </div>
      </div>
      {loading && (
        <div className="text-slate-500 text-xs text-center py-4">Grafik yukleniyor...</div>
      )}
      <div ref={containerRef} style={{ width: "100%", height: 500 }} />
    </div>
  );
}
