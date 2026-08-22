"use client";
import { useEffect, useState } from "react";
import Splash from "./splash/page";

export default function Home() {
  const [show, setShow] = useState(false);

  // First visit in a tab → play the film. Returning navigations to "/" in the
  // same tab go straight to the dashboard (sessionStorage).
  useEffect(() => {
    let seen = false;
    try {
      seen = sessionStorage.getItem("eaglex_splash_seen") === "1";
    } catch {
      seen = false; // storage unavailable → default to showing the film
    }
    if (!seen) {
      setShow(true);
      try {
        sessionStorage.setItem("eaglex_splash_seen", "1");
      } catch {}
      return;
    }
    window.location.replace("/dashboard");
  }, []);

  if (show) return <Splash />;
  return null;
}
