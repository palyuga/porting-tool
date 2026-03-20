from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


class GitError(Exception):
    pass


class CherryPickConflict(Exception):
    """Raised when cherry-pick results in merge conflicts."""

    def __init__(self, conflicted_files: list[str]) -> None:
        self.conflicted_files = conflicted_files
        super().__init__(f"Conflicts in: {', '.join(conflicted_files)}")


class CommitNotAvailable(Exception):
    """Raised when the commit hash is not in the local object database."""


@dataclass
class RemoteInfo:
    """Parsed components from git remote URL."""

    base_url: str
    project_key: str
    repo_slug: str


def _run(
    *args: str,
    check: bool = True,
    capture: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        run_env = None
        if env is not None:
            run_env = os.environ.copy()
            run_env.update(env)
        return subprocess.run(
            args,
            check=check,
            capture_output=capture,
            text=True,
            env=run_env,
        )
    except subprocess.CalledProcessError as exc:
        raise GitError(
            f"git command failed: {' '.join(args)}\n"
            f"Exit code: {exc.returncode}\n"
            f"stderr: {(exc.stderr or '').strip()}"
        ) from exc
    except FileNotFoundError:
        raise GitError(
            "git is not installed or not on PATH.\n"
            "Please install git and make sure it's available in your terminal."
        )


def ensure_git_repo() -> None:
    result = _run("git", "rev-parse", "--is-inside-work-tree", check=False)
    if result.returncode != 0:
        raise GitError(
            "Not inside a git repository.\n"
            "Please run this command from within your project's git directory."
        )


def get_remote_url(remote: str = "origin") -> str:
    result = _run("git", "remote", "get-url", remote)
    return result.stdout.strip()


def parse_remote_url(url: str) -> RemoteInfo:
    """Extract Bitbucket base URL, project key, and repo slug from a git remote URL.

    Handles:
      ssh://git@host:port/PROJ/repo.git
      git@host:port/PROJ/repo.git     (SCP-like with port)
      git@host:PROJ/repo.git          (SCP-like without port)
      https://host/scm/PROJ/repo.git
      https://host:port/scm/PROJ/repo.git
    """
    scp_match = re.match(
        r"^[\w.-]+@([\w.-]+):(?:(\d+)/)?(.+?)(?:\.git)?$", url
    )
    if scp_match:
        host = scp_match.group(1)
        port = scp_match.group(2)
        path = scp_match.group(3)
        parts = path.strip("/").split("/")
        if len(parts) < 2:
            raise GitError(f"Cannot parse project/repo from remote URL: {url}")
        base = f"https://{host}:{port}" if port else f"https://{host}"
        return RemoteInfo(
            base_url=base,
            project_key=parts[-2],
            repo_slug=parts[-1],
        )

    parsed = urlparse(url)
    host_port = parsed.hostname or ""
    if parsed.port:
        host_port = f"{host_port}:{parsed.port}"

    scheme = parsed.scheme or "https"
    if scheme == "ssh":
        scheme = "https"

    base_url = f"{scheme}://{host_port}"
    path = (parsed.path or "").strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    if path.startswith("scm/"):
        path = path[4:]

    parts = path.strip("/").split("/")
    if len(parts) < 2:
        raise GitError(f"Cannot parse project/repo from remote URL: {url}")

    return RemoteInfo(
        base_url=base_url,
        project_key=parts[-2],
        repo_slug=parts[-1],
    )


def fetch(remote: str = "origin") -> None:
    print(f"Fetching from {remote}...")
    _run("git", "fetch", remote)



def checkout_new_branch(branch_name: str, start_point: str) -> None:
    _run("git", "checkout", "-b", branch_name, start_point)


def cherry_pick(commit_hash: str) -> None:
    """Cherry-pick a commit.

    Raises:
        CommitNotAvailable  — commit is not in the local object database.
        CherryPickConflict  — merge conflicts that need manual resolution.
        GitError            — any other git failure.
    """
    result = _run("git", "cherry-pick", commit_hash, check=False)
    if result.returncode == 0:
        return

    stderr = (result.stderr or "").strip()
    if "bad object" in stderr or "unknown revision" in stderr:
        raise CommitNotAvailable(commit_hash)

    conflicted = get_conflicted_files()
    if conflicted:
        raise CherryPickConflict(conflicted)

    raise GitError(
        f"Cherry-pick failed for commit {commit_hash}.\n"
        f"stderr: {stderr}"
    )


def get_conflicted_files() -> list[str]:
    result = _run("git", "diff", "--name-only", "--diff-filter=U")
    return [f for f in result.stdout.strip().splitlines() if f]


def has_cherry_pick_in_progress() -> bool:
    result = _run("git", "rev-parse", "--git-dir", check=False)
    if result.returncode != 0:
        return False
    git_dir = Path(result.stdout.strip())
    return (git_dir / "CHERRY_PICK_HEAD").exists()


def cherry_pick_continue() -> None:
    """Continue cherry-pick after the user stages resolved files."""
    result = _run(
        "git", "cherry-pick", "--continue", "--no-edit",
        check=False,
    )
    if result.returncode != 0:
        conflicted = get_conflicted_files()
        if conflicted:
            raise CherryPickConflict(conflicted)
        raise GitError(
            f"cherry-pick --continue failed.\n"
            f"stderr: {(result.stderr or '').strip()}"
        )


def push_branch(branch_name: str, remote: str = "origin") -> str:
    """Push a branch and return any output (may contain PR creation URL)."""
    result = _run("git", "push", "-u", remote, branch_name)
    return (result.stderr or "") + (result.stdout or "")


def get_current_branch() -> str:
    result = _run("git", "rev-parse", "--abbrev-ref", "HEAD")
    return result.stdout.strip()


def branch_exists_locally(branch_name: str) -> bool:
    result = _run("git", "rev-parse", "--verify", branch_name, check=False)
    return result.returncode == 0


def branch_exists_remotely(branch_name: str, remote: str = "origin") -> bool:
    result = _run(
        "git", "ls-remote", "--heads", remote, branch_name, check=False
    )
    return bool(result.stdout.strip())


def has_dirty_tracked_files() -> bool:
    """Check for modified/staged tracked files (ignores untracked files)."""
    result = _run("git", "status", "--porcelain")
    for line in result.stdout.splitlines():
        if line and not line.startswith("??"):
            return True
    return False


def stash_changes() -> None:
    _run("git", "stash", "push", "-m", "port-tool: auto-stash before porting")


def revert_changes() -> None:
    """Discard all changes to tracked files."""
    _run("git", "reset", "HEAD", check=False)
    _run("git", "checkout", "--", ".")


def delete_local_branch(branch_name: str) -> None:
    """Delete a local branch, switching away first if it is currently checked out."""
    if get_current_branch() == branch_name:
        _run("git", "checkout", "--detach")
    _run("git", "branch", "-D", branch_name)


def abort_cherry_pick() -> None:
    _run("git", "cherry-pick", "--abort", check=False)


def checkout(branch_name: str) -> None:
    _run("git", "checkout", branch_name)
