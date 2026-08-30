"use client";

import { useEffect, useRef } from "react";
import {
  createChart,
  ColorType,
  type IChartApi,
  type ISeriesApi,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";

export type ChartPoint = {
  time: number; // unix seconds
  value: number;
};

type Candle = { t: number; o: number; h: number; l: number; c: number };

const EMAPeriod = 20;
const BBPeriod = 20;
const BBMult = 2;

function ema(values: number[], period: number): (number | null)[] {
  const k = 2 / (period + 1);
  const out: (number | null)[] = [];
  let prev: number | null = null;
  for (let i = 0; i < values.length; i++) {
    if (prev === null) {
      prev = values[i];
      out.push(null);
    } else {
      prev = values[i] * k +prev * (1 - k);
      out.push(prev);
    }
  }
  return out;
}

function smaSplice(values: number[], period: number, bias: number): (number | null)[] {
  const out: (number | null)[] = [];
  for (let i = 0; i < values.length; i++) {
    if (i < period -  1) {
      out.push(null);
    } else {
      let s = 0;
      for (let j = i - period +  1; j <= i; j++) s += values[j];
      out.push(s / period +bias);
    }
  }
  return out;
}

function rsi14(values: number[]): (number | null)[] {
  const out: (number | null)[] = [];
  let g =  0, l =  0;
  for (let i = 0; i < values.length; i++) {
    if (i === 0) { out.push(null); continue; }
    const d = values[i] - values[i -  1];
    if (d >  0) g += d; else l += -d;
    if (i <  14) { out.push(null); continue; }
    if (i ===  14) {
      const ag = g /  14, al = l /  14;
      const rs = al ===  0 ? 100 : ag / al;
      out.push(100 - (100 / (1 +rs)));
      g = ag; l = al;
    } else {
      g = (g * (14 -  1) + d >  0 ? d :  0) /  14;
      l = (l * (14 -  1) + d <  0 ? -d :  0) /  14;
      const rs = l ===  0 ?  100 : g / l;
      out.push(100 - (100 / (1 +rs)));
    }
  }
  return out;
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
  const rsiRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const rsiChartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const emaRef = useRef<ISeriesApi<"Line"> | null>(null);
  const bbURef = useRef<ISeriesApi<"Line"> | null>(null);
  const bbRef = useRef<ISeriesApi<"Line"> | null>(null);
  const bbLRef = useRef<ISeriesApi<"Line"> | null>(null);
  const rsiRef2 = useRef<ISeriesApi<"Line"> | null>(null);

  useEffect(() => {
    const el = containerRef.current;
    const rsel = rsiRef.current;
    if (!el) return;

    const mk = (target: HTMLElement, h: number) => createChart(target, {
      layout: { background: { type: ColorType.Solid, color: "#0f1626" }, textColor: "#8b97b0" },
      grid: { vertLines: { color: "#1a2740" }, horzLines: { color: "#1a2740" } },
      width: target.clientWidth,
      height: h,
      timeScale: { timeVisible: true, secondsVisible: false },
      rightPriceScale: { borderColor: "#22304a" },
    });

    const chart = mk(el, 300);
    const candle = chart.addCandlestickSeries({
      upColor: "#2ee6a8",
      downColor: "#ff5d6c",
      borderUpColor: "#2ee6a8",
      borderDownColor: "#ff5d6c",
      wickUpColor: "#2ee6a8",
      wickDownColor: "#ff5d6c",
    });
    const emaS = chart.addLineSeries({ color: "#f7c948", lineWidth: 2, priceLineVisible: false });
    const bbU = chart.addLineSeries({ color: "rgba(127,178,255,.55)", lineWidth: 1 });
    const bbM = chart.addLineSeries({ color: "rgba(127,178,255,.85)", lineWidth: 1 });
    const bbL = chart.addLineSeries({ color: "rgba(127,178,255,.55)", lineWidth: 1 });

    let rchart: IChartApi | null = null;
    let rser: ISeriesApi<"Line"> | null = null;
    if (rsel) {
      rchart = mk(rsel, 90);
      rser = rchart.addLineSeries({ color: "#8b7cf8", lineWidth: 2 });
      rser.createPriceLine({ price: 70, color: "rgba(46,230,168,.35)", lineWidth: 1, lineStyle: { type: 2 } as any });
      rser.createPriceLine({ price:  30, color: "rgba(255,93,108,.35)", lineWidth:  1, lineStyle: { type:  2 } as any });
    }

    chartRef.current = chart;
    rsiChartRef.current = rchart;
    candleRef.current = candle;
    emaRef.current = emaS;
    bbURef.current = bbU;
    bbRef.current = bbM;
    bbLRef.current = bbL;
    rsiRef2.current = rser;

    const onResize = () => {
      if (chartRef.current && containerRef.current) {
        chartRef.current.applyOptions({ width: containerRef.current.clientWidth });
      }
      if (rsiChartRef.current && rsiRef.current) {
        rsiChartRef.current.applyOptions({ width: rsiRef.current.clientWidth });
      }
    };
    window.addEventListener("resize", onResize);

    return () => {
      window.removeEventListener("resize", onResize);
      chart.remove();
      if (rchart) rchart.remove();
      chartRef.current = null;
      rsiChartRef.current = null;
      candleRef.current = null;
      emaRef.current = null;
      bbURef.current = null;
      bbRef.current = null;
      bbLRef.current = null;
      rsiRef2.current = null;
    };
  }, []);

  useEffect(() => {
    if (points.length === 0) return;

    // fold ticks into 1s candles
    const bySec = new Map<number, Candle>();
    for (const p of points) {
      const t = Math.floor(p.time);
      const cur = bySec.get(t);
      if (!cur) {
        bySec.set(t, { t, o: p.value, h: p.value, l: p.value, c: p.value });
      } else {
        cur.h = Math.max(cur.h, p.value);
        cur.l = Math.min(cur.l, p.value);
        cur.c = p.value;
      }
    }
    const candles = [...bySec.values()].sort((a, b) => a.t - b.t);
    const closes = candles.map((c) => c.c);
    const e = ema(closes, EMAPeriod);
    const bu = smaSplice(closes, BBPeriod, BBMult * std(closes, BBPeriod));
    const bm = smaSplice(closes, BBPeriod, 0);
    const bl = smaSplice(closes, BBPeriod, -BBMult * std(closes, BBPeriod));
    const r = rsi14(closes);

    candleRef.current?.setData(
      candles.map((c) => ({
        time: c.t as Time,
        open: c.o,
        high: c.h,
        low: c.l,
        close: c.c,
      })),
    );
    emaRef.current?.setData(
      candles.map((c, i) => ({ time: c.t as Time, value: e[i] })).filter((x) => x.value !== null) as any,
    );
    bbURef.current?.setData(
      candles.map((c, i) => ({ time: c.t as Time, value: bu[i] })).filter((x) => x.value !== null) as any,
    );
    bbRef.current?.setData(
      candles.map((c, i) => ({ time: c.t as Time, value: bm[i] })).filter((x) => x.value !== null) as any,
    );
    bbLRef.current?.setData(
      candles.map((c, i) => ({ time: c.t as Time, value: bl[i] })).filter((x) => x.value !== null) as any,
    );
    rsiRef2.current?.setData(
      candles.map((c, i) => ({ time: c.t as Time, value: r[i] })).filter((x) => x.value !== null) as any,
    );
  }, [points]);

  return (
    <div>
      <div ref={containerRef} style={{ width: "100%", height: 300 }} />
      <div ref={rsiRef} style={{ width: "100%", height: 90, marginTop: 8 }} />
      {loading && <div className="state">LOADING DATA…</div>}
      {!loading && empty && <div className="state">NO DATA — connect a market</div>}
    </div>
  );
}

function std(vals: number[], period: number): number {
  const slice = vals.slice(-period);
  const n = slice.length;
  if (n < 2) return 0;
  const m = slice.reduce((a, b) => a + b, 0) / n;
  const v = slice.reduce((a, b) => a + (b - m) ** 2,  0) / n;
  return Math.sqrt(v);
}
