"""Tests must never touch the files that production reads.

This is not hypothetical. Two separate contamination incidents happened:

1. Test fixtures appended to backend/data/store.json -- the journal the
   scorecard and the CF's adaptive z-threshold read -- which manufactured a
   66.67% win rate out of nothing.
2. Chat tests wrote eight turns mentioning a fake symbol ("CHATX") into
   "chat_history_default" in that same store. Those turns rendered in the live
   UI's chat panel, so a real user saw a conversation they never had about a
   market that does not exist.

conftest.py redirects EAGLEX_STORE_PATH to a temp file to prevent this. These
tests assert the redirect actually holds, because the failure mode is silent:
nothing breaks, the data is just quietly wrong, and it is only discovered by
reading the live UI.
"""
from pathlib import Path




class TestStoreIsolation:
    def test_store_path_is_redirected_away_from_the_repo(self):
        """The whole suite's isolation rests on this one path."""
        from app.services.persistence import _STORE_PATH

        configured = Path(_STORE_PATH).resolve()
        repo_data = (Path(__file__).resolve().parent.parent / "data").resolve()
        assert repo_data not in configured.parents, (
            f"tests are pointed at the production store ({configured}); "
            "conftest must redirect EAGLEX_STORE_PATH"
        )
        assert "eaglex-test-" in str(configured), configured

    def test_writing_settings_does_not_touch_the_repo_store(self):
        """Exercise the real write path and confirm nothing lands in the repo."""
        from app.services.persistence import settings_store

        repo_store = Path(__file__).resolve().parent.parent / "data" / "store.json"
        before = repo_store.read_bytes() if repo_store.exists() else None

        settings_store.set("isolation_probe", {"written_by": "test_isolation"})

        after = repo_store.read_bytes() if repo_store.exists() else None
        assert before == after, (
            "a settings write reached the production store; isolation is broken"
        )

    def test_chat_writes_do_not_reach_the_repo_store(self):
        """The exact incident: a chat turn must not reach the repo store."""
        from app.services.chat import PlatformChat

        repo_store = Path(__file__).resolve().parent.parent / "data" / "store.json"
        before = repo_store.read_bytes() if repo_store.exists() else None

        PlatformChat().ask("what is this platform")

        after = repo_store.read_bytes() if repo_store.exists() else None
        assert before == after, "a chat turn reached the production store"
