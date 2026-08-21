/**
 * Runtime API proxy — resolves the backend URL at REQUEST time, never at
 * build time. The browser always calls same-origin /api/proxy/*, so there is
 * no CORS, no NEXT_PUBLIC_* inlining, and no build-frozen bad URL.
 *
 * Resolution order:
 *   1. BACKEND_URL (runtime env, e.g. set on Render)
 *   2. BACKEND_HOST + BACKEND_PORT (Render fromService values)
 *   3. https://eaglex-backend.onrender.com (production default)
 *   4. http://localhost:8000 (local dev)
 */
import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

function backendBase(): string {
  if (process.env.BACKEND_URL) return process.env.BACKEND_URL.replace(/\/$/, "");
  if (process.env.BACKEND_HOST) {
    const port = process.env.BACKEND_PORT || "8000";
    return `http://${process.env.BACKEND_HOST}:${port}`;
  }
  if (process.env.NODE_ENV === "production") return "https://eaglex-backend.onrender.com";
  return "http://localhost:8000";
}

async function handler(req: NextRequest, { params }: { params: { path: string[] } }) {
  const path = params.path.join("/");
  const url = `${backendBase()}/${path}${req.nextUrl.search}`;

  const headers = new Headers();
  req.headers.forEach((v, k) => {
    if (!["host", "connection", "content-length"].includes(k.toLowerCase())) headers.set(k, v);
  });

  let body: BodyInit | undefined;
  if (!["GET", "HEAD"].includes(req.method)) {
    body = await req.arrayBuffer();
  }

  try {
    // redirect: "manual" so the backend's 307 (OAuth login) reaches the browser
    const res = await fetch(url, { method: req.method, headers, body, cache: "no-store", redirect: "manual" });
    const resHeaders = new Headers();
    res.headers.forEach((v, k) => {
      if (!["content-encoding", "transfer-encoding", "connection"].includes(k.toLowerCase())) resHeaders.set(k, v);
    });
    resHeaders.set("Access-Control-Allow-Origin", "*");
    return new NextResponse(res.body, { status: res.status, headers: resHeaders });
  } catch (err: any) {
    return NextResponse.json(
      { error: "backend unreachable", detail: String(err?.message ?? err), url: backendBase() },
      { status: 502 }
    );
  }
}

export { handler as GET, handler as POST, handler as PUT, handler as PATCH, handler as DELETE, handler as OPTIONS };
