from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

STATE_DIR = ".porting"
STATE_FILE = "state.json"


@dataclass
class TargetBranch:
    alias: str
    branch: str


@dataclass
class PortingState:
    original_pr_url: str
    original_pr_title: str
    original_pr_description: str
    commit_hash: str
    source_branch: str
    approved_reviewers: list[dict[str, str]]
    current_branch: str
    current_target: str
    current_alias: str
    remaining_targets: list[dict[str, str]]
    bitbucket_base_url: str
    project_key: str
    repo_slug: str
    auto_reviewers: bool = False


def _state_path() -> Path:
    return Path.cwd() / STATE_DIR / STATE_FILE


def save_state(state: PortingState) -> Path:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(asdict(state), f, indent=2, ensure_ascii=False)
    return path


def load_state() -> PortingState:
    path = _state_path()
    if not path.is_file():
        raise FileNotFoundError(
            "No porting session in progress.\n"
            "There is no saved state to continue from.\n"
            "Start a new porting session with: port --pr <URL> --to <branches>"
        )
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return PortingState(**data)


def clear_state() -> None:
    path = _state_path()
    if path.is_file():
        path.unlink()
    state_dir = path.parent
    if state_dir.is_dir() and not any(state_dir.iterdir()):
        state_dir.rmdir()


def has_state() -> bool:
    return _state_path().is_file()
