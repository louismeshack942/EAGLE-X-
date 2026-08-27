import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "EAGLE-X — Trading Intelligence",
  description: "EAGLE-X observable parity foundation — batch 1 (phase 0 + 1).",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}