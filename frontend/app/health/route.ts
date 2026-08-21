// Health endpoint for Render's load-balancer health check.
// render.yaml sets healthCheckPath: /health — without this route the
// check always fails and Render 502s the service.
import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export async function GET() {
  return NextResponse.json({ status: "healthy", service: "eaglex-frontend" });
}
