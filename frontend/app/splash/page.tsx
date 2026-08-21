"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

const SCENES = [
  "Forest — the eagle perches…",
  "Clouds — wings stretch wide…",
  "Pitch — circling the Starting XI…",
  "Peak — mastering the mountain…",
  "🦅 EAGLE-X",
];

export default function Splash() {
  const router = useRouter();
  const [progress, setProgress] = useState(0);

  useEffect(() => {
    const startedAt = Date.now();
    const interval = setInterval(() => {
      const elapsed = Date.now() - startedAt;
      const pct = Math.min(100, (elapsed / 8000) * 100);
      setProgress(pct);
      if (pct >= 100) {
        clearInterval(interval);
        router.push("/dashboard");
      }
    }, 100);
    return () => clearInterval(interval);
  }, [router]);

  const sceneIndex = Math.min(SCENES.length - 1, Math.floor(progress / (100 / SCENES.length)));

  return (
    <div
      style={{
        height: "100vh",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        background: "linear-gradient(180deg, #010409 0%, #0d1117 100%)",
        color: "#c9d1d9",
      }}
    >
      <div style={{ fontSize: "4rem", marginBottom: "1rem" }}>🦅</div>
      <div style={{ fontSize: "0.95rem", color: "#58a6ff", marginBottom: "2rem", letterSpacing: 2 }}>
        {SCENES[sceneIndex]}
      </div>
      <div style={{ width: 300, height: 4, background: "#30363d", borderRadius: 2, overflow: "hidden" }}>
        <div style={{ height: "100%", width: `${progress}%`, background: "#58a6ff", transition: "width 0.1s linear" }} />
      </div>
      <div style={{ marginTop: 12, fontSize: "0.7rem", color: "#8b949e" }}>{Math.round(progress)}%</div>
    </div>
  );
}
