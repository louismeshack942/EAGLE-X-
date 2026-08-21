"use client";
import { useEffect, useState } from "react";
import { apiGet, apiPost } from "@/lib/api";
import { Card, Btn } from "@/components/ui";

export default function SocialFeed({ refreshMs = 5000 }: { refreshMs?: number }) {
  const [posts, setPosts] = useState<any[]>([]);
  const [newPost, setNewPost] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    try {
      const r = await apiGet<any>("/social/posts?limit=20");
      setPosts(r.posts ?? []); setError(null);
    } catch (e: any) { setError(String(e.message ?? e)); }
  };

  useEffect(() => {
    load();
    const t = setInterval(load, refreshMs);
    return () => clearInterval(t);
  }, [refreshMs]);

  const publish = async () => {
    if (!newPost.trim()) return;
    setBusy(true);
    try {
      await apiPost("/social/posts", { content: newPost, post_type: "post", user_id: "user-1" });
      setNewPost("");
      await load();
    } catch (e: any) { setError(String(e.message ?? e)); }
    finally { setBusy(false); }
  };

  const like = async (id: string) => {
    try { await apiPost(`/social/posts/${id}/like`); await load(); }
    catch (e: any) { setError(String(e.message ?? e)); }
  };

  return (
    <Card title="💬 SOCIAL FEED">
      {error && <div style={{ color: "#f85149", fontSize: "0.75rem" }}>{error}</div>}
      <div style={{ display: "flex", gap: 6, marginBottom: 6 }}>
        <input value={newPost} onChange={(e) => setNewPost(e.target.value)}
          placeholder="Share a thought or trade idea…"
          style={{ flex: 1, background: "#010409", color: "#c9d1d9", border: "1px solid #30363d", borderRadius: 4, padding: "4px 8px" }}
        />
        <Btn small variant="primary" disabled={busy} onClick={publish}>POST</Btn>
      </div>
      <div style={{ maxHeight: 220, overflow: "auto", fontSize: "0.75rem" }}>
        {posts.map((p: any) => (
          <div key={p.id} style={{ padding: "4px 0", borderBottom: "1px solid #21262d" }}>
            <div style={{ color: "#c9d1d9" }}>{p.content}</div>
            <div style={{ display: "flex", gap: 8, color: "#8b949e", fontSize: "0.7rem", marginTop: 2 }}>
              <button onClick={() => like(p.id)} style={{ background: "none", border: "none", color: "#58a6ff", cursor: "pointer" }}>
                ♥ {p.likes}
              </button>
              <span>{p.comments?.length ?? 0} comments</span>
            </div>
          </div>
        ))}
        {posts.length === 0 && <div style={{ color: "#8b949e" }}>No posts yet.</div>}
      </div>
    </Card>
  );
}
