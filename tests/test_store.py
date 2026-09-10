"""Persistence backends, including the GitHub Contents API client.

The GitHub tests run against an in-memory fake that implements the subset of
the API the client uses, so base64 encoding, sha handling, branch bootstrap and
conflict retries are all exercised without network access.
"""

import base64
import json

import pytest
import requests

from core.store import (
    DEFAULT_DATA_BRANCH, GitHubStore, LocalStore, StoreError, build_store,
)


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = json.dumps(self._payload)

    @property
    def ok(self):
        return 200 <= self.status_code < 300

    def json(self):
        return self._payload

    @property
    def headers(self):
        return {}


class FakeGitHub:
    """Minimal stand-in for the GitHub REST API, backed by dicts."""

    def __init__(self, repo="owner/repo", branches=None, permissions=None,
                 fail_next_put_with=None):
        self.repo = repo
        self.branches = branches if branches is not None else {}
        self.permissions = permissions if permissions is not None else {"push": True}
        self.files: dict[str, dict[str, str]] = {}   # branch -> path -> content
        self.commit_messages: list[str] = []
        self.fail_next_put_with = fail_next_put_with
        self.requests: list[tuple[str, str]] = []
        self.blobs: dict[str, str] = {}
        self.trees: dict[str, list[dict]] = {}
        self.commits: dict[str, str] = {}   # commit sha -> tree sha

    # -- the single entry point the client uses ---------------------------

    def request(self, method, url, headers=None, timeout=None, **kwargs):
        self.requests.append((method, url))
        assert headers["Authorization"].startswith("Bearer "), "token must be sent"
        path = url.replace("https://api.github.com", "")
        params = kwargs.get("params") or {}
        body = kwargs.get("json") or {}

        if method == "GET" and path == f"/repos/{self.repo}":
            return FakeResponse(200, {"permissions": self.permissions,
                                      "default_branch": "main"})

        if path.startswith(f"/repos/{self.repo}/git/ref/heads/"):
            branch = path.rsplit("/", 1)[-1]
            if branch in self.branches:
                return FakeResponse(200, {"object": {"sha": self.branches[branch]}})
            return FakeResponse(404, {"message": "Branch not found"})

        if method == "POST" and path == f"/repos/{self.repo}/git/blobs":
            sha = f"blob{len(self.blobs)}"
            self.blobs[sha] = body.get("content", "")
            return FakeResponse(201, {"sha": sha})

        if method == "POST" and path == f"/repos/{self.repo}/git/trees":
            sha = f"tree{len(self.trees)}"
            self.trees[sha] = body.get("tree", [])
            return FakeResponse(201, {"sha": sha})

        if method == "POST" and path == f"/repos/{self.repo}/git/commits":
            assert body.get("parents") == [], "the data branch must be an orphan"
            sha = f"commit{len(self.commits)}"
            self.commits[sha] = body["tree"]
            return FakeResponse(201, {"sha": sha})

        if method == "POST" and path == f"/repos/{self.repo}/git/refs":
            branch = body["ref"].replace("refs/heads/", "")
            self.branches[branch] = body["sha"]
            # Materialize the commit's tree, the way a real branch would carry it.
            contents = self.files.setdefault(branch, {})
            for entry in self.trees.get(self.commits.get(body["sha"], ""), []):
                contents[entry["path"]] = self.blobs.get(entry["sha"], "")
            return FakeResponse(201, {})

        if path.startswith(f"/repos/{self.repo}/contents/"):
            target = path.replace(f"/repos/{self.repo}/contents/", "")
            if method == "GET":
                branch = params.get("ref")
                return self._get_contents(branch, target)
            if method == "PUT":
                return self._put_contents(body, target)

        return FakeResponse(404, {"message": f"no fake route for {method} {path}"})

    # -- helpers ----------------------------------------------------------

    def _get_contents(self, branch, target):
        files = self.files.get(branch, {})
        if target in files:
            content = files[target]
            return FakeResponse(200, {
                "type": "file", "path": target,
                "sha": f"sha-{hash(content) & 0xffff}",
                "content": base64.b64encode(content.encode()).decode(),
            })
        children = [p for p in files if p.startswith(f"{target}/")]
        if children:
            return FakeResponse(200, [
                {"type": "file", "name": p.rsplit("/", 1)[-1], "path": p}
                for p in sorted(children)
            ])
        return FakeResponse(404, {"message": "Not Found"})

    def _put_contents(self, body, target):
        if self.fail_next_put_with is not None:
            status = self.fail_next_put_with
            self.fail_next_put_with = None
            return FakeResponse(status, {"message": "conflict"})
        branch = body["branch"]
        self.files.setdefault(branch, {})[target] = base64.b64decode(
            body["content"]).decode()
        self.commit_messages.append(body["message"])
        return FakeResponse(200, {"content": {"path": target}})


@pytest.fixture
def fake():
    return FakeGitHub()


@pytest.fixture
def github(fake):
    session = requests.Session()
    session.request = fake.request
    return GitHubStore("tok_secret", "owner/repo", session=session)


class TestLocalStore:
    def test_round_trip(self, tmp_path):
        store = LocalStore(tmp_path)
        assert store.write_json("runs/a.json", {"x": 1}, "msg")
        assert store.read_json("runs/a.json") == {"x": 1}

    def test_missing_file_is_none(self, tmp_path):
        assert LocalStore(tmp_path).read_json("nope.json") is None

    def test_corrupt_file_is_none(self, tmp_path):
        (tmp_path / "bad.json").write_text("{ not json")
        assert LocalStore(tmp_path).read_json("bad.json") is None

    def test_listing_is_newest_first(self, tmp_path):
        store = LocalStore(tmp_path)
        for name in ("2026-W01", "2026-W03", "2026-W02"):
            store.write_json(f"runs/{name}.json", {}, "m")
        assert store.list_json("runs") == [
            "runs/2026-W03.json", "runs/2026-W02.json", "runs/2026-W01.json"]

    def test_listing_a_missing_directory(self, tmp_path):
        assert LocalStore(tmp_path).list_json("runs") == []

    def test_is_not_marked_persistent(self, tmp_path):
        assert LocalStore(tmp_path).persistent is False


class TestGitHubStoreWrites:
    def test_creates_the_data_branch_on_first_write(self, github, fake):
        assert DEFAULT_DATA_BRANCH not in fake.branches
        github.write_json("runs/a.json", {"x": 1}, "Save run")
        assert DEFAULT_DATA_BRANCH in fake.branches
        assert fake.files[DEFAULT_DATA_BRANCH]["README.md"].startswith("# parlay-data")

    def test_branch_is_orphaned_so_no_code_lives_on_it(self, github, fake):
        """Asserted inside the fake: the bootstrap commit must have no parents."""
        github.write_json("runs/a.json", {"x": 1}, "Save run")
        assert set(fake.files[DEFAULT_DATA_BRANCH]) == {"README.md", "runs/a.json"}

    def test_branch_is_created_only_once(self, github, fake):
        github.write_json("runs/a.json", {"x": 1}, "Save run")
        before = sum(1 for m, _ in fake.requests if m == "POST")
        github.write_json("runs/b.json", {"x": 2}, "Save run")
        after = sum(1 for m, _ in fake.requests if m == "POST")
        assert after == before, "the second write must not re-create the branch"

    def test_round_trip(self, github):
        github.write_json("runs/a.json", {"legs": 10}, "Save run")
        assert github.read_json("runs/a.json") == {"legs": 10}

    def test_commit_message_is_used(self, github, fake):
        github.write_json("runs/a.json", {}, "Save parlay run 2026-W37")
        assert "Save parlay run 2026-W37" in fake.commit_messages

    def test_update_sends_the_existing_sha(self, github, fake):
        """Without the sha, GitHub rejects an overwrite."""
        github.write_json("runs/a.json", {"v": 1}, "first")
        seen = {}
        original = fake.request

        def spy(method, url, **kwargs):
            if method == "PUT":
                seen.update(kwargs.get("json") or {})
            return original(method, url, **kwargs)

        github.session.request = spy
        github.write_json("runs/a.json", {"v": 2}, "second")
        assert "sha" in seen
        assert github.read_json("runs/a.json") == {"v": 2}

    def test_conflict_is_retried_once(self, github, fake):
        fake.fail_next_put_with = 409
        assert github.write_json("runs/a.json", {"v": 1}, "save") is True

    def test_repeated_conflict_raises(self, github, fake):
        original = fake.request

        def always_conflict(method, url, **kwargs):
            if method == "PUT":
                return FakeResponse(409, {"message": "conflict"})
            return original(method, url, **kwargs)

        github.session.request = always_conflict
        with pytest.raises(StoreError):
            github.write_json("runs/a.json", {"v": 1}, "save")

    def test_listing(self, github):
        github.write_json("runs/2026-W01.json", {}, "m")
        github.write_json("runs/2026-W02.json", {}, "m")
        assert github.list_json("runs") == ["runs/2026-W02.json", "runs/2026-W01.json"]

    def test_missing_document_is_none(self, github):
        github.write_json("runs/a.json", {}, "m")     # bootstrap the branch
        assert github.read_json("runs/missing.json") is None

    def test_listing_a_missing_directory(self, github):
        assert github.list_json("runs") == []


class TestGitHubStoreErrors:
    def _store(self, status, payload=None):
        session = requests.Session()
        session.request = lambda *a, **k: FakeResponse(status, payload or {})
        return GitHubStore("tok", "owner/repo", session=session)

    def test_bad_token_message_is_actionable(self):
        healthy, message = self._store(401).check()
        assert not healthy and "regenerate" in message.lower()

    def test_missing_permission_message_names_the_scope(self):
        healthy, message = self._store(403).check()
        assert not healthy and "Contents: Read and write" in message

    def test_missing_repo_message_names_the_setting(self):
        healthy, message = self._store(404).check()
        assert not healthy and "GITHUB_REPO" in message

    def test_read_only_token_is_reported(self):
        session = requests.Session()
        session.request = lambda *a, **k: FakeResponse(200, {"permissions": {"push": False}})
        healthy, message = GitHubStore("tok", "owner/repo", session=session).check()
        assert not healthy and "not write" in message

    def test_healthy_check(self, github):
        healthy, message = github.check()
        assert healthy and "owner/repo" in message

    def test_network_failure_is_wrapped(self):
        session = requests.Session()

        def boom(*args, **kwargs):
            raise requests.ConnectionError("dns go boom")

        session.request = boom
        with pytest.raises(StoreError, match="Could not reach GitHub"):
            GitHubStore("tok", "owner/repo", session=session).read_json("a.json")

    def test_rejects_a_malformed_repo(self):
        with pytest.raises(StoreError, match="owner/name"):
            GitHubStore("tok", "not-a-repo")

    def test_rejects_an_empty_token(self):
        with pytest.raises(StoreError):
            GitHubStore("", "owner/repo")


class TestBuildStore:
    def test_no_token_falls_back_to_local(self, tmp_path):
        store, warning = build_store({}, tmp_path)
        assert isinstance(store, LocalStore)
        assert warning is None

    def test_token_without_repo_warns_but_still_works(self, tmp_path):
        store, warning = build_store({"GITHUB_TOKEN": "tok"}, tmp_path)
        assert isinstance(store, LocalStore)
        assert "GITHUB_REPO" in warning

    def test_full_configuration_selects_github(self, tmp_path):
        store, warning = build_store(
            {"GITHUB_TOKEN": "tok", "GITHUB_REPO": "owner/repo"}, tmp_path)
        assert isinstance(store, GitHubStore)
        assert store.persistent is True
        assert warning is None

    def test_custom_branch(self, tmp_path):
        store, _ = build_store(
            {"GITHUB_TOKEN": "tok", "GITHUB_REPO": "owner/repo",
             "GITHUB_DATA_BRANCH": "history"}, tmp_path)
        assert store.branch == "history"

    def test_malformed_repo_degrades_to_local(self, tmp_path):
        store, warning = build_store(
            {"GITHUB_TOKEN": "tok", "GITHUB_REPO": "garbage"}, tmp_path)
        assert isinstance(store, LocalStore)
        assert "Falling back to local disk" in warning
