"use client";
import React from "react";

import { ReactNode } from "react";

type BtnVariant = "primary" | "success" | "danger" | "secondary";

const btnPalette: Record<BtnVariant, string> = {
  primary: "#58a6ff",
  success: "#3fb950",
  danger: "#f85149",
  secondary: "#2d333b",
};

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
      style={{
        background: btnPalette[variant],
        color: variant === "secondary" ? "#c9d1d9" : "#0d1117",
        border: "1px solid #30363d",
        borderRadius: 6,
        padding: small ? "2px 8px" : "6px 12px",
        fontWeight: 700,
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.5 : 1,
        fontSize: small ? "0.75rem" : "0.9rem",
      }}
    >
      {children}
    </button>
  );
}

export function Pill({ label, color = "#30363d" }: { label: string; color?: string }) {
  return (
    <span
      style={{
        background: `${color}33`,
        border: `1px solid ${color}`,
        color,
        borderRadius: 999,
        padding: "2px 8px",
        fontSize: "0.75rem",
        fontWeight: 700,
      }}
    >
      {label}
    </span>
  );
}

export function Card({ title, children, actions }: { title: string; children: ReactNode; actions?: ReactNode }) {
  return (
    <div
      style={{
        background: "#161b22",
        border: "1px solid #30363d",
        borderRadius: 8,
        padding: "1rem",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <div style={{ fontWeight: 700, fontSize: "0.9rem" }}>{title}</div>
        {actions}
      </div>
      {children}
    </div>
  );
}

export function Row({ label, value, accent }: { label: string; value: ReactNode; accent?: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.8rem", padding: "2px 0" }}>
      <span style={{ color: "#8b949e" }}>{label}</span>
      <span style={{ color: accent ?? "#c9d1d9", fontWeight: 600 }}>{value}</span>
    </div>
  );
}
