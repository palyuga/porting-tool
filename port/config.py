from __future__ import annotations

import os
import tomllib
from pathlib import Path

CONFIG_FILENAME = ".porting.toml"
GLOBAL_CONFIG_DIR = Path.home() / ".porting"
GLOBAL_CONFIG_FILE = GLOBAL_CONFIG_DIR / "config.toml"
AUTH_FILENAME = "auth.toml"
ENV_VAR_PAT = "BITBUCKET_PAT"

PAT_HELP = f"""\
Bitbucket Personal Access Token (PAT) is not configured.

The port tool needs a PAT to interact with the Bitbucket REST API
(fetching PR details, creating new pull requests, adding reviewers).

How to create a PAT:
  1. Open Bitbucket in your browser
  2. Click your avatar (top-right) -> Manage account
  3. In the left sidebar, click "Personal access tokens"
  4. Click "Create a token"
  5. Give it a name (e.g. "port-tool")
  6. Grant permissions: Project read, Repository write
  7. Click "Create" and copy the token

Then choose ONE of these options to configure it:

  Option A — environment variable (recommended):
    Set {ENV_VAR_PAT}=<your-token>

    Windows (permanent):
      setx {ENV_VAR_PAT} "<your-token>"

    Linux/macOS:
      echo 'export {ENV_VAR_PAT}="<your-token>"' >> ~/.bashrc

  Option B — auth file:
    Create {GLOBAL_CONFIG_DIR / AUTH_FILENAME}
    with contents:

      pat = "<your-token>"
"""

CONFIG_NOT_FOUND_HELP = f"""\
Could not find the port tool configuration.

The tool looks for config in two places (in order):
  1. A file named {CONFIG_FILENAME} in the current directory or any parent
  2. {GLOBAL_CONFIG_FILE}

To get started:
  1. Find example.porting.toml in the port tool installation directory
  2. Copy it to one of the locations above
  3. Edit the [branches] section to match your release branches
  4. Set the [repo] path to your git repository (required when the config
     is not inside the repo):

     [repo]
     path = "C:/projects/project-1"   # absolute path to your git project

     [branches]
     "128" = "release/my-128-branch"
     "m" = "master"

Recommended setup: copy to {GLOBAL_CONFIG_FILE}
with an absolute [repo] path. This works from any directory."""


class PortConfig:
    """Holds resolved configuration: branch mappings, PAT, and repo directory."""

    def __init__(
        self,
        branches: dict[str, str],
        pat: str,
        config_path: Path,
        repo_dir: Path | None = None,
    ) -> None:
        self.branches = branches
        self.pat = pat
        self.config_path = config_path
        self.repo_dir = repo_dir

    def resolve_branch(self, name_or_alias: str) -> tuple[str, str]:
        """Resolve a branch name or alias to (alias, full_branch_name).

        Raises ValueError if not found.
        """
        if name_or_alias in self.branches:
            return name_or_alias, self.branches[name_or_alias]

        for alias, full_name in self.branches.items():
            if full_name == name_or_alias:
                return alias, full_name

        available = "\n".join(
            f"  {alias:>10}  ->  {full}" for alias, full in self.branches.items()
        )
        raise ValueError(
            f"Unknown branch or alias: '{name_or_alias}'\n"
            f"Configured branches:\n{available}"
        )

    def format_branches_table(self) -> str:
        lines = ["Configured release branches:"]
        for alias, full in self.branches.items():
            lines.append(f"  {alias:>10}  ->  {full}")
        return "\n".join(lines)


def find_config_file(start_dir: Path | None = None) -> Path:
    """Find config: walk up from cwd, then fall back to ~/.porting/config.toml."""
    current = (start_dir or Path.cwd()).resolve()
    while True:
        candidate = current / CONFIG_FILENAME
        if candidate.is_file():
            return candidate
        parent = current.parent
        if parent == current:
            break
        current = parent

    if GLOBAL_CONFIG_FILE.is_file():
        return GLOBAL_CONFIG_FILE

    raise FileNotFoundError(CONFIG_NOT_FOUND_HELP)


def _load_toml(config_path: Path) -> dict:
    with open(config_path, "rb") as f:
        return tomllib.load(f)


def _load_branches(data: dict, config_path: Path) -> dict[str, str]:
    branches = data.get("branches")
    if not branches or not isinstance(branches, dict):
        raise ValueError(
            f"{config_path} must contain a [branches] section with alias = branch mappings."
        )

    result: dict[str, str] = {}
    for alias, full_name in branches.items():
        if not isinstance(full_name, str):
            raise ValueError(
                f"Invalid value for alias '{alias}' in {config_path}: expected a string."
            )
        result[str(alias)] = full_name

    return result


def _load_repo_dir(data: dict, config_path: Path) -> Path | None:
    """Load optional [repo] path, resolved relative to the config file."""
    repo_section = data.get("repo")
    if not repo_section or not isinstance(repo_section, dict):
        return None

    raw_path = repo_section.get("path", "").strip()
    if not raw_path:
        return None

    repo_path = Path(raw_path)
    if not repo_path.is_absolute():
        repo_path = (config_path.parent / repo_path).resolve()

    if not repo_path.is_dir():
        raise ValueError(
            f"The repo path specified in {config_path} does not exist:\n"
            f"  {repo_path}\n"
            f"Check the [repo] path value in your config."
        )

    return repo_path


def _load_pat() -> str:
    pat = os.environ.get(ENV_VAR_PAT, "").strip()
    if pat:
        return pat

    auth_file = GLOBAL_CONFIG_DIR / AUTH_FILENAME
    if auth_file.is_file():
        with open(auth_file, "rb") as f:
            data = tomllib.load(f)
        pat = data.get("pat", "").strip()
        if pat:
            return pat

    raise RuntimeError(PAT_HELP)


def load_branches_only(start_dir: Path | None = None) -> PortConfig:
    """Load only branch config (no PAT). Used for --help display."""
    config_path = find_config_file(start_dir)
    data = _load_toml(config_path)
    branches = _load_branches(data, config_path)
    repo_dir = _load_repo_dir(data, config_path)
    return PortConfig(branches=branches, pat="", config_path=config_path, repo_dir=repo_dir)


def load_config(start_dir: Path | None = None) -> PortConfig:
    config_path = find_config_file(start_dir)
    data = _load_toml(config_path)
    branches = _load_branches(data, config_path)
    repo_dir = _load_repo_dir(data, config_path)
    pat = _load_pat()
    return PortConfig(branches=branches, pat=pat, config_path=config_path, repo_dir=repo_dir)
