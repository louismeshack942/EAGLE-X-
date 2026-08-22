"use client";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

const VIDEO_SRC = "/videos/splash.mp4";
const POSTER_SRC = "/videos/splash-poster.jpg";
const SPLASH_MS = 15000; // exactly the length of the film

export default function Splash() {
  const router = useRouter();
  const videoRef = useRef<HTMLVideoElement>(null);
  const [fading, setFading] = useState(false);
  const [videoFailed, setVideoFailed] = useState(false);
  const [muted, setMuted] = useState(false);

  // Attempt unmuted autoplay; browsers that block it get muted playback plus a
  // tap-for-sound control (audio is embedded in the MP4, never a separate file).
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    v.volume = 0.9;
    const tryPlay = () => {
      v.muted = false;
      v.play().catch(() => {
        v.muted = true;
        setMuted(true);
        v.play().catch(() => setVideoFailed(true));
      });
    };
    if (v.readyState >= 2) tryPlay();
    else v.addEventListener("canplay", tryPlay, { once: true });
    return () => v.removeEventListener("canplay", tryPlay);
  }, []);

  // Leave for the dashboard the moment the film ends — with a hard fallback
  // timer so a stalled player can never trap the user on the splash screen.
  useEffect(() => {
    const leave = setTimeout(() => {
      setFading(true);
      setTimeout(() => router.push("/dashboard"), 450);
    }, SPLASH_MS + 700);
    return () => clearTimeout(leave);
  }, [router]);

  const finish = () => {
    setFading(true);
    setTimeout(() => router.push("/dashboard"), 450);
  };

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "#0d1117",
        opacity: fading ? 0 : 1,
        transition: "opacity 0.45s ease",
      }}
    >
      {!videoFailed ? (
        <video
          ref={videoRef}
          src={VIDEO_SRC}
          poster={POSTER_SRC}
          autoPlay
          playsInline
          preload="auto"
          onEnded={finish}
          onError={() => setVideoFailed(true)}
          style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
        />
      ) : (
        // Fallback: the eagle-on-the-peak poster with the logo, then straight in.
        <div
          onClick={finish}
          style={{
            width: "100%",
            height: "100%",
            backgroundImage: `url(${POSTER_SRC})`,
            backgroundSize: "cover",
            backgroundPosition: "center",
            cursor: "pointer",
          }}
        />
      )}

      {muted && !videoFailed && (
        <button
          onClick={() => {
            const v = videoRef.current;
            if (v) {
              v.muted = false;
              setMuted(false);
            }
          }}
          style={{
            position: "absolute",
            bottom: 24,
            right: 24,
            background: "rgba(13,17,23,0.72)",
            border: "1px solid #30363d",
            borderRadius: 20,
            color: "#c9d1d9",
            padding: "8px 14px",
            fontSize: "0.78rem",
            cursor: "pointer",
            backdropFilter: "blur(6px)",
          }}
        >
          🔊 Tap for sound
        </button>
      )}

      <button
        onClick={finish}
        style={{
          position: "absolute",
          top: 24,
          right: 24,
          background: "rgba(13,17,23,0.55)",
          border: "1px solid #30363d",
          borderRadius: 20,
          color: "#8b949e",
          padding: "6px 14px",
          fontSize: "0.75rem",
          cursor: "pointer",
          backdropFilter: "blur(6px)",
        }}
      >
        Skip →
      </button>
    </div>
  );
}
