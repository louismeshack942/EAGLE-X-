"""Community features — copy trading, social feed, leaderboards, trading rooms."""
import uuid
import time
from typing import List, Optional

# -------------------- Copy Trading --------------------
_leaders: dict[str, dict] = {}
_follows: dict[str, dict] = {}


def register_leader(name: str, copy_ratio: float = 0.1, bio: str = "") -> dict:
    lid = str(uuid.uuid4())
    _leaders[lid] = {
        "id": lid,
        "name": name,
        "bio": bio,
        "copy_ratio": copy_ratio,
        "total_pnl": 0.0,
        "win_rate": 0.0,
        "profit_factor": 0.0,
        "max_drawdown": 0.0,
        "followers": 0,
        "created_at": time.time(),
    }
    return _leaders[lid]


def list_leaders() -> List[dict]:
    return sorted(_leaders.values(), key=lambda l: l.get("followers", 0), reverse=True)


def follow_leader(user_id: str, leader_id: str, allocation: float = 0.5) -> Optional[dict]:
    leader = _leaders.get(leader_id)
    if not leader:
        return None
    fid = str(uuid.uuid4())
    _follows[fid] = {
        "id": fid,
        "user_id": user_id,
        "leader_id": leader_id,
        "allocation": allocation,
        "created_at": time.time(),
    }
    leader["followers"] = leader.get("followers", 0) + 1
    return _follows[fid]


def unfollow(follow_id: str) -> Optional[dict]:
    return _follows.pop(follow_id, None)


def get_leader(leader_id: str) -> Optional[dict]:
    return _leaders.get(leader_id)


def list_follows(user_id: Optional[str] = None) -> List[dict]:
    return list(_follows.values()) if not user_id else [f for f in _follows.values() if f["user_id"] == user_id]


# -------------------- Social Feed --------------------
_posts: list[dict] = []


def create_post(user_id: str, content: str, post_type: str = "post") -> dict:
    post = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "content": content,
        "post_type": post_type,
        "likes": 0,
        "comments": [],
        "created_at": time.time(),
    }
    _posts.append(post)
    return post


def list_posts(limit: int = 50) -> List[dict]:
    return list(reversed(_posts[-limit:]))


def like_post(post_id: str) -> Optional[dict]:
    for p in _posts:
        if p["id"] == post_id:
            p["likes"] += 1
            return p
    return None


def comment_post(post_id: str, user_id: str, content: str) -> Optional[dict]:
    for p in _posts:
        if p["id"] == post_id:
            comment = {"id": str(uuid.uuid4()), "user_id": user_id, "content": content, "created_at": time.time()}
            p["comments"].append(comment)
            return comment
    return None


# -------------------- Leaderboards --------------------
def leaderboard(entries: List[dict], metric: str = "pnl", limit: int = 20) -> List[dict]:
    ranked = sorted(entries, key=lambda e: e.get(metric, 0), reverse=True)[:limit]
    for i, e in enumerate(ranked):
        e["rank"] = i + 1
    return ranked


# -------------------- Trading Rooms --------------------
_rooms: dict[str, dict] = {}


def create_room(name: str, created_by: str, is_private: bool = False, password: str = "") -> dict:
    rid = str(uuid.uuid4())
    _rooms[rid] = {
        "id": rid,
        "name": name,
        "created_by": created_by,
        "is_private": is_private,
        "password": password,
        "members": [],
        "messages": [],
        "created_at": time.time(),
    }
    return _rooms[rid]


def list_rooms() -> List[dict]:
    return [{k: v for k, v in r.items() if k != "password"} for r in _rooms.values()]


def join_room(room_id: str, user_id: str, password: str = "") -> Optional[dict]:
    room = _rooms.get(room_id)
    if not room:
        return None
    if room["is_private"] and room["password"] and room["password"] != password:
        return None
    if user_id not in room["members"]:
        room["members"].append(user_id)
    return {k: v for k, v in room.items() if k != "password"}


def post_message(room_id: str, user_id: str, message: str) -> Optional[dict]:
    room = _rooms.get(room_id)
    if not room:
        return None
    msg = {"id": str(uuid.uuid4()), "user_id": user_id, "message": message, "timestamp": time.time()}
    room["messages"].append(msg)
    room["messages"] = room["messages"][-100:]
    return msg


def list_messages(room_id: str, limit: int = 50) -> Optional[List[dict]]:
    room = _rooms.get(room_id)
    if not room:
        return None
    return room["messages"][-limit:]
