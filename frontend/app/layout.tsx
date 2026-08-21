import { ErrorBoundary } from "@/components/ErrorBoundary";
import "./globals.css";

export const metadata = {
  title: "EAGLE-X",
  description: "Trading intelligence platform for Deriv synthetic indices",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <ErrorBoundary>{children}</ErrorBoundary>
      </body>
    </html>
  );
}
