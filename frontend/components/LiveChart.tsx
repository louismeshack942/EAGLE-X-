"use client";

import { useEffect, useRef } from "react";
import {
  createChart,
  ColorType,
  type IChartApi,
  type ISeriesApi,
  type Time,
} from "lightweight-charts";

export interface ChartPoint {
  time: number; // unix seconds
  value: number;
}

export default function LiveChart({
  points,
  loading,
  empty,
}: {
  points: ChartPoint[];
  loading?: boolean;
  empty?: boolean;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Line"> | null>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const chart = createChart(el, {
      layout: {
        background: { type: ColorType.Solid, color: "#141926" },
        textColor: "#8a93a8",
      },
      grid: {
        vertLines: { color: "#1b2132" },
        horzLines: { color: "#1b2132" },
      },
      width: el.clientWidth,
      height: 260,
      timeScale: { timeVisible: true, secondsVisible: false },
    });

    const series = chart.addLineSeries({ color: "#4f6df5", lineWidth: 2 });
    chartRef.current = chart;
    seriesRef.current = series;

    const onResize = () => {
      if (chartRef.current && containerRef.current) {
        chartRef.current.applyOptions({
          width: containerRef.current.clientWidth,
        });
      }
    };
    window.addEventListener("resize", onResize);

    return () => {
      window.removeEventListener("resize", onResize);
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, []);

  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;
    series.setData(
      points.map((p) => ({
        time: p.time as Time,
        value: p.value,
      })),
    );
  }, [points]);

  return (
    <div style={{ position: "relative" }}>
      <div ref={containerRef} style={{ width: "100%", height: 260 }} />
      {loading && <div className="state">LOADING DATA…</div>}
      {!loading && empty && <div className="state">NO DATA — connect a market</div>}
    </div>
  );
}