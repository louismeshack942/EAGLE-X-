"use client";
import React, { ReactNode } from "react";

type BtnVariant = "primary" | "success" | "danger" | "secondary";

export function Btn({
  children,
  onClick,
  variant = "primary",
  disabled,
  title,
  small,
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: BtnVariant;
  disabled?: boolean;
  title?: string;
  small?: boolean;
}) {
  return (
    <button
      title={title}
      onClick={onClick}
      disabled={disabled}
      className={`ex-btn ex-btn-${variant}`}
      style={small ? { padding: "3px 10px", fontSize: "0.75rem" } : undefined}
    >
      {children}
    </button>
  );
}

/** Status badge with the design-system colour + optional pulse. */
export type PillStatus =
  | "live" | "demo" | "strong" | "weak" | "neutral"
  | "running" | "stopped" | "idle";

export function Pill({
  label,
  status,
  pulse,
}: {
  label: string;
  status?: PillStatus;
  pulse?: boolean;
}) {
  const cls = status ? `ex-pill-${status}` : "ex-pill-neutral";
  return <span className={`ex-pill ${cls}${pulse ? " ex-pill-pulse" : ""}`}>{label}</span>;
}

/** Legacy colour-string Pill kept for panels not yet migrated. */
export function PillColor({ label, color = "#8b949e" }: { label: string; color?: string }) {
  return (
    <span
      className="ex-pill"
      style={{ background: `${color}26`, color, border: `1px solid ${color}80` }}
    >
      {label}
    </span>
  );
}

/** Position badge — GK / CB / LB / RB / DMF / RMF / LMF / AMF / SS / CF. */
export function PositionTag({ pos }: { pos: string }) {
  return <span className="ex-card-pos">{pos}</span>;
}

/**
 * Card — the core surface. Supports a football position label, emoji, and a
 * status pill on the right.
 */
export function Card({
  title,
  emoji,
  pos,
  statusLabel,
  status,
  pulse,
  children,
  actions,
}: {
  title: string;
  emoji?: string;
  pos?: string;
  statusLabel?: string;
  status?: PillStatus;
  pulse?: boolean;
  children: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="ex-card">
      <div className="ex-card-head">
        <div className="ex-card-title-wrap">
          {pos && <PositionTag pos={pos} />}
          {emoji && <span className="ex-card-emoji">{emoji}</span>}
          <span className="ex-card-title">{title}</span>
        </div>
        {statusLabel ? (
          <Pill label={statusLabel} status={status} pulse={pulse} />
        ) : (
          actions
        )}
      </div>
      {children}
    </div>
  );
}

export function Row({ label, value, accent }: { label: string; value: ReactNode; accent?: string }) {
  return (
    <div className="ex-row">
      <span className="ex-row-label">{label}</span>
      <span className="ex-row-value" style={accent ? { color: accent } : undefined}>{value}</span>
    </div>
  );
}

/** Confidence bar with the spec gradient tiers. */
export function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(100, value));
  const tier = pct > 70 ? "conf-high" : pct >= 50 ? "conf-mid" : "conf-low";
  const color = pct > 70 ? "#3fb950" : pct >= 50 ? "#d29922" : "#f85149";
  return (
    <div className="conf-bar-wrap">
      <div className="conf-bar-track">
        <div className={`conf-bar-fill ${tier}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="conf-label" style={{ color }}>{Math.round(pct)}%</span>
    </div>
  );
}

/** Skeleton placeholder while data loads. */
export function Skeleton({ lines = 4 }: { lines?: number }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {Array.from({ length: lines }).map((_, i) => (
        <div key={i} className="skeleton" style={{ height: 14, width: `${88 - i * 10}%` }} />
      ))}
    </div>
  );
}
