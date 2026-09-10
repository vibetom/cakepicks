"""Persistence, behind a small interface with two backends.

`LocalStore` writes to the working directory. That is correct when you run the
app on your own machine, but hosted Streamlit containers have an ephemeral
disk, so anything saved there disappears on the next restart.

`GitHubStore` commits the same files to a dedicated branch of the repository
through the GitHub Contents API, which makes the history genuinely durable and
readable in the browser. It writes to its own orphan branch (no code on it) so
that saving data never touches the branch the app is deployed from -- a commit
to the deployed branch would make Streamlit Cloud redeploy the app underneath
the user.

Only deliberate user actions write: saving a run, grading legs, adding an
alias, or pressing "Save settings". Nothing autosaves on a slider drag.
"""

from __future__ import annotations

import base64
import json
from abc import ABC, abstractmethod
from pathlib import Path

import requests

API_BASE = "https://api.github.com"
DEFAULT_DATA_BRANCH = "parlay-data"
BRANCH_README = """# parlay-data

Saved state for the League Parlay Bot, written by the app.

- `runs/` — one JSON per saved run: config, picks, and Win/Loss/Push grades.
- `odds/latest.json` — the last odds snapshot, so a restart costs no API credits.
- `rosters/latest.json` — the last ESPN roster pull.
- `projections/latest.json` — the last PFF file, so it need not be re-uploaded.
- `aliases.json` — manual name-match overrides.
- `config.json` — saved slider settings.

This branch deliberately holds no application code, so writing to it never
triggers a redeploy of the app. Editing these files by hand is fine.
"""


class StoreError(Exception):
    """A persistence failure that the user should be told about."""


def _serialize(data: dict, compact: bool) -> str:
    if compact:
        return json.dumps(data, separators=(",", ":"))
    return json.dumps(data, indent=2)


class Store(ABC):
    """Read/write a handful of small JSON documents by path."""

    #: True when writes survive an app restart.
    persistent = False
    #: Short human-readable description of where data is going.
    label = "nowhere"

    @abstractmethod
    def read_json(self, path: str) -> dict | None:
        """Parsed document, or None when it does not exist."""

    @abstractmethod
    def write_json(self, path: str, data: dict, message: str,
                   compact: bool = False) -> bool:
        """Persist a document. Returns False when the write was not possible.

        `compact` drops the indentation. Readability is worth the bytes for
        small documents, but not for the odds snapshot, where indentation is
        about a third of the size and the file has to stay under GitHub's 1 MB
        Contents API read ceiling.
        """

    @abstractmethod
    def list_json(self, prefix: str) -> list[str]:
        """Paths of the JSON documents under a directory prefix."""

    def check(self) -> tuple[bool, str]:
        """(healthy, message) for display in the UI."""
        return True, self.label


class LocalStore(Store):
    """Filesystem-backed store rooted at the working directory."""

    persistent = False

    def __init__(self, root: str | Path = "."):
        self.root = Path(root)
        self.label = f"local disk ({self.root.resolve()})"

    def _path(self, path: str) -> Path:
        return self.root / path

    def read_json(self, path: str) -> dict | None:
        target = self._path(path)
        if not target.exists():
            return None
        try:
            return json.loads(target.read_text() or "null")
        except (json.JSONDecodeError, OSError):
            return None

    def write_json(self, path: str, data: dict, message: str,
                   compact: bool = False) -> bool:
        target = self._path(path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_serialize(data, compact))
            return True
        except OSError:
            return False

    def list_json(self, prefix: str) -> list[str]:
        directory = self._path(prefix)
        if not directory.exists():
            return []
        return sorted(
            (f"{prefix.rstrip('/')}/{p.name}" for p in directory.glob("*.json")),
            reverse=True,
        )

    def check(self) -> tuple[bool, str]:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            probe = self.root / ".write-probe"
            probe.write_text("ok")
            probe.unlink()
            return True, (
                "Saving to local disk. On a hosted app this is wiped on every "
                "restart — use the download buttons to keep anything important."
            )
        except OSError:
            return False, "The working directory is read-only; nothing can be saved."


class GitHubStore(Store):
    """Commits JSON documents to a dedicated branch via the Contents API."""

    persistent = True

    def __init__(self, token: str, repo: str, branch: str = DEFAULT_DATA_BRANCH,
                 timeout: int = 20, session: requests.Session | None = None):
        if not token:
            raise StoreError("No GitHub token supplied.")
        if not repo or "/" not in repo:
            raise StoreError(f"Repository must look like 'owner/name', got {repo!r}.")
        self.token = token
        self.repo = repo.strip("/")
        self.branch = branch
        self.timeout = timeout
        self.session = session or requests.Session()
        self.label = f"GitHub · {self.repo} @ {self.branch}"
        self._branch_ready = False

    # -- HTTP -------------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs):
        url = path if path.startswith("http") else f"{API_BASE}{path}"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        try:
            return self.session.request(
                method, url, headers=headers, timeout=self.timeout, **kwargs
            )
        except requests.RequestException as exc:
            raise StoreError(f"Could not reach GitHub: {exc}") from exc

    def _fail(self, response, action: str) -> StoreError:
        """Turn an API error into something a non-expert can act on."""
        status = response.status_code
        if status == 401:
            return StoreError(
                "GitHub rejected the token (401). It may be expired or mistyped — "
                "regenerate it and update GITHUB_TOKEN in your app's secrets."
            )
        if status == 403:
            return StoreError(
                "GitHub refused the request (403). The token most likely lacks "
                "'Contents: Read and write' permission on this repository."
            )
        if status == 404:
            return StoreError(
                f"GitHub could not find {self.repo} (404). Check GITHUB_REPO, and "
                "that the token grants access to this specific repository."
            )
        detail = ""
        try:
            detail = response.json().get("message", "")
        except ValueError:
            detail = response.text[:200]
        return StoreError(f"GitHub error {status} while {action}: {detail}")

    # -- branch bootstrap -------------------------------------------------

    def _ensure_branch(self) -> None:
        """Create the data branch on first use, as an orphan with a README.

        An orphan branch keeps application code off the data branch entirely,
        so the branch's purpose stays obvious and a data commit can never be
        mistaken for a code change.
        """
        if self._branch_ready:
            return
        response = self._request("GET", f"/repos/{self.repo}/git/ref/heads/{self.branch}")
        if response.status_code == 200:
            self._branch_ready = True
            return
        if response.status_code != 404:
            raise self._fail(response, "looking up the data branch")

        blob = self._request(
            "POST", f"/repos/{self.repo}/git/blobs",
            json={"content": BRANCH_README, "encoding": "utf-8"},
        )
        if not blob.ok:
            raise self._fail(blob, "creating the data branch")

        tree = self._request(
            "POST", f"/repos/{self.repo}/git/trees",
            json={"tree": [{"path": "README.md", "mode": "100644",
                            "type": "blob", "sha": blob.json()["sha"]}]},
        )
        if not tree.ok:
            raise self._fail(tree, "creating the data branch")

        commit = self._request(
            "POST", f"/repos/{self.repo}/git/commits",
            json={"message": "Start parlay-data branch", "tree": tree.json()["sha"],
                  "parents": []},
        )
        if not commit.ok:
            raise self._fail(commit, "creating the data branch")

        ref = self._request(
            "POST", f"/repos/{self.repo}/git/refs",
            json={"ref": f"refs/heads/{self.branch}", "sha": commit.json()["sha"]},
        )
        # 422 means another session created it first, which is fine.
        if not ref.ok and ref.status_code != 422:
            raise self._fail(ref, "creating the data branch")
        self._branch_ready = True

    # -- Store interface --------------------------------------------------

    def _contents(self, path: str):
        return self._request(
            "GET", f"/repos/{self.repo}/contents/{path}", params={"ref": self.branch}
        )

    def read_json(self, path: str) -> dict | None:
        response = self._contents(path)
        if response.status_code == 404:
            return None
        if not response.ok:
            raise self._fail(response, f"reading {path}")
        payload = response.json()
        if isinstance(payload, list):        # a directory, not a file
            return None
        try:
            raw = base64.b64decode(payload.get("content", "")).decode("utf-8")
            return json.loads(raw or "null")
        except (ValueError, UnicodeDecodeError):
            return None

    def _sha(self, path: str) -> str | None:
        response = self._contents(path)
        if response.status_code == 404:
            return None
        if not response.ok:
            raise self._fail(response, f"reading {path}")
        payload = response.json()
        return payload.get("sha") if isinstance(payload, dict) else None

    def write_json(self, path: str, data: dict, message: str,
                   compact: bool = False) -> bool:
        self._ensure_branch()
        body = _serialize(data, compact)
        encoded = base64.b64encode(body.encode("utf-8")).decode("ascii")

        for attempt in (1, 2):
            payload = {"message": message, "content": encoded, "branch": self.branch}
            sha = self._sha(path)
            if sha:
                payload["sha"] = sha
            response = self._request(
                "PUT", f"/repos/{self.repo}/contents/{path}", json=payload
            )
            if response.ok:
                return True
            # 409/422 means someone else committed between our read and write.
            if response.status_code in (409, 422) and attempt == 1:
                continue
            raise self._fail(response, f"saving {path}")
        return False

    def list_json(self, prefix: str) -> list[str]:
        response = self._contents(prefix.rstrip("/"))
        if response.status_code == 404:
            return []
        if not response.ok:
            raise self._fail(response, f"listing {prefix}")
        payload = response.json()
        if not isinstance(payload, list):
            return []
        return sorted(
            (item["path"] for item in payload
             if item.get("type") == "file" and item.get("name", "").endswith(".json")),
            reverse=True,
        )

    def check(self) -> tuple[bool, str]:
        response = self._request("GET", f"/repos/{self.repo}")
        if response.status_code == 200:
            if not response.json().get("permissions", {}).get("push", True):
                return False, (
                    f"The token can read {self.repo} but not write to it. Give it "
                    "'Contents: Read and write'."
                )
            return True, f"Saving to GitHub → {self.repo}, branch `{self.branch}`."
        try:
            raise self._fail(response, "checking access")
        except StoreError as exc:
            return False, str(exc)


def build_store(secrets: dict, root: str | Path = ".") -> tuple[Store, str | None]:
    """Pick a backend from configuration.

    Returns the store and an optional warning explaining why the durable
    backend was not used. Falling back to local disk is never fatal: the app
    must keep working without any persistence configured.
    """
    token = (secrets.get("GITHUB_TOKEN") or "").strip()
    repo = (secrets.get("GITHUB_REPO") or "").strip()
    branch = (secrets.get("GITHUB_DATA_BRANCH") or DEFAULT_DATA_BRANCH).strip()

    if not token:
        return LocalStore(root), None
    if not repo:
        return LocalStore(root), (
            "GITHUB_TOKEN is set but GITHUB_REPO is missing, so history is being "
            "saved to local disk instead. Add GITHUB_REPO = \"owner/name\"."
        )
    try:
        return GitHubStore(token, repo, branch), None
    except StoreError as exc:
        return LocalStore(root), f"{exc} Falling back to local disk."
