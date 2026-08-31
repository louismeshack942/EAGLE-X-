import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Pro Trader - Live Digit & Market Analytics",
  description: "Real-time analysis for synthetic markets: digit frequency, over/under, even/odd, rise/fall and tick streaming.",
  authors: [{ name: "Pro Trader" }],
  manifest: "/manifest.json",
  icons: {
    icon: [
      { url: "/icon-192.png", sizes: "192x192", type: "image/png" },
      { url: "/icon-512.png", sizes: "512x512", type: "image/png" },
    ],
    apple: "/icon-192.png",
  },
  other: {
    "theme-color": "#0a0e1a",
    "mobile-web-app-capable": "yes",
    "apple-mobile-web-app-capable": "yes",
    "apple-mobile-web-app-status-bar-style": "black-translucent",
    "apple-mobile-web-app-title": "Pro Trader",
    "og:type": "website",
    "og:title": "Pro Trader - Live Digit & Market Analytics",
    "og:description": "Real-time analysis for synthetic markets: digit frequency, over/under, even/odd, rise/fall and tick streaming.",
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}