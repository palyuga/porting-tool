from __future__ import annotations

import argparse
import os
import sys

from port.bitbucket import BitbucketClient, BitbucketError, PullRequestInfo, parse_pr_url
from port.config import PortConfig, load_branches_only, load_config
from port.git_ops import (
    CommitNotAvailable,
    CherryPickConflict,
    GitError,
    abort_cherry_pick,
    branch_exists_locally,
    branch_exists_remotely,
    cherry_pick,
    cherry_pick_continue,
    checkout,
    checkout_new_branch,
    delete_local_branch,
    ensure_git_repo,
    fetch,
    get_conflicted_files,
    get_current_branch,
    has_cherry_pick_in_progress,
    has_dirty_tracked_files,
    push_branch,
    revert_changes,
    stash_changes,
)
from port.state import PortingState, clear_state, has_state, load_state, save_state


def _error(msg: str) -> None:
    print(f"\nERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def _info(msg: str) -> None:
    print(f"  {msg}")


def _success(msg: str) -> None:
    print(f"  OK: {msg}")


def _header(msg: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {msg}")
    print(f"{'=' * 60}")



def _switch_to_repo(config: PortConfig) -> None:
    """Change working directory to the configured repo path, if set."""
    if config.repo_dir:
        _info(f"Working directory: {config.repo_dir}")
        os.chdir(config.repo_dir)


def _handle_dirty_tree() -> None:
    """Check for uncommitted tracked-file changes and let the user decide."""
    if not has_dirty_tracked_files():
        return

    print()
    print("  WARNING: You have uncommitted changes in tracked files.")
    print()
    print("    (A)bort  — stop, make no changes")
    print("    (S)tash  — stash changes and continue (restore with 'git stash pop')")
    print("    (R)evert — discard all changes (CANNOT BE UNDONE)")
    print()

    while True:
        choice = input("  Your choice [A/s/r]: ").strip().lower()
        if choice in ("", "a"):
            print("  Aborted.")
            sys.exit(0)
        elif choice == "s":
            stash_changes()
            _success("Changes stashed. Restore later with: git stash pop")
            return
        elif choice == "r":
            confirm = input("  Are you sure? All changes will be lost. Type 'yes': ").strip()
            if confirm.lower() == "yes":
                revert_changes()
                _success("Changes reverted.")
                return
            print("  Revert cancelled, choose again.")
        else:
            print("  Invalid choice. Enter A, S, or R.")


def _build_porting_description(
    original_description: str,
    original_pr_url: str,
    pr_id: int,
    target_branch: str,
) -> str:
    link = f"[PR-{pr_id}]({original_pr_url})"
    porting_line = f"Porting {link} to {target_branch}"
    if original_description.strip():
        return f"{original_description.rstrip()}\n\n{porting_line}"
    return porting_line


def _derive_new_branch_name(
    source_branch: str, target_alias: str, known_aliases: list[str]
) -> str:
    """Replace or append the target alias on the source branch name.

    If the source branch already ends with a known alias (e.g. -156),
    replace it with the target alias. Otherwise append it.

    e.g. bugfix/jira-7777-fix-156 + 176 -> bugfix/jira-7777-fix-176
         bugfix/jira-7777-fix     + 168 -> bugfix/jira-7777-fix-168
    """
    for alias in sorted(known_aliases, key=len, reverse=True):
        suffix = f"-{alias}"
        if source_branch.endswith(suffix):
            return source_branch[: -len(suffix)] + f"-{target_alias}"
    return f"{source_branch}-{target_alias}"


def _process_single_target(
    client: BitbucketClient,
    pr_info: PullRequestInfo,
    target_branch: str,
    alias: str,
    remaining: list[dict[str, str]],
    known_aliases: list[str] | None = None,
    auto_reviewers: bool = False,
) -> bool:
    """Port the change onto the target branch, push, and open a PR.

    Uses ``git cherry-pick`` when the commit is already in the local object
    database; otherwise downloads the PR diff over HTTPS (Bitbucket REST API)
    and applies it with ``git apply``.

    Returns True if successful, False if conflicts occurred (state saved).
    """
    new_branch = _derive_new_branch_name(
        pr_info.source_branch, alias, known_aliases or []
    )

    _header(f"Porting to {target_branch} (alias: {alias})")
    _info(f"New branch: {new_branch}")

    if branch_exists_locally(new_branch):
        _info(f"Local branch '{new_branch}' already exists, deleting it...")
        delete_local_branch(new_branch)

    if branch_exists_remotely(new_branch):
        _info(f"WARNING: Remote branch '{new_branch}' already exists.")
        _info("Skipping this target to avoid overwriting existing work.")
        _info("Delete the remote branch manually if you want to re-port.")
        return True

    _info(f"Creating branch from origin/{target_branch}...")
    checkout_new_branch(new_branch, f"origin/{target_branch}")

    _info(f"Cherry-picking commit {pr_info.commit_hash[:12]}...")
    try:
        cherry_pick(pr_info.commit_hash)
    except CommitNotAvailable:
        _error(
            f"Commit {pr_info.commit_hash[:12]} is not in this local repository.\n\n"
            "Make sure [repo] path in ~/.porting/config.toml points to the clone where "
            "you normally work — the one you ran 'git fetch' in before cherry-picking manually.\n"
            f"  git cat-file -e {pr_info.commit_hash[:12]}   # must exit 0 in that clone"
        )
    except CherryPickConflict as exc:
        _save_conflict_state(
            pr_info, new_branch, target_branch, alias, remaining, auto_reviewers
        )
        print()
        print("  CONFLICT: Cherry-pick has merge conflicts!")
        print("  Conflicted files:")
        for f in exc.conflicted_files:
            print(f"    - {f}")
        print()
        print("  To resolve:")
        print("    1. Open the conflicted files in your IDE")
        print("    2. Resolve all conflicts and save")
        print("    3. Stage resolved files:")
        print("       git add <conflicted-file-1> <conflicted-file-2> ...")
        print("    4. Run: port --continue")
        print("       Or:  port --abort    (to cancel porting for this branch)")
        print()
        return False

    reviewers = pr_info.approved_reviewers if auto_reviewers else None
    return _push_and_create_pr(
        client, pr_info, new_branch, target_branch, alias, reviewers
    )


def _push_and_create_pr(
    client: BitbucketClient,
    pr_info: PullRequestInfo,
    new_branch: str,
    target_branch: str,
    alias: str,
    reviewers: list[dict[str, str]] | None = None,
) -> bool:
    _info(f"Pushing {new_branch}...")
    push_branch(new_branch)

    description = _build_porting_description(
        pr_info.description, pr_info.url, pr_info.pr_id, target_branch
    )

    _info("Creating pull request...")
    try:
        pr_data = client.create_pull_request(
            owner_type=pr_info.owner_type,
            owner=pr_info.project_key,
            repo=pr_info.repo_slug,
            title=pr_info.title,
            description=description,
            from_branch=new_branch,
            to_branch=target_branch,
            reviewers=reviewers,
        )
        pr_link = pr_data.get("links", {}).get("self", [{}])
        if isinstance(pr_link, list) and pr_link:
            pr_url = pr_link[0].get("href", "")
        else:
            new_id = pr_data.get("id", "?")
            pr_url = (
                f"{pr_info.base_url}/{pr_info.owner_type}/{pr_info.project_key}"
                f"/repos/{pr_info.repo_slug}/pull-requests/{new_id}"
            )
        _success(f"Pull request created: {pr_url}")
    except BitbucketError as exc:
        print(f"\n  WARNING: Push succeeded but PR creation failed:\n  {exc}")
        print(f"  You can create the PR manually for branch '{new_branch}'.")

    return True


def _save_conflict_state(
    pr_info: PullRequestInfo,
    current_branch: str,
    current_target: str,
    current_alias: str,
    remaining: list[dict[str, str]],
    auto_reviewers: bool = False,
) -> None:
    state = PortingState(
        original_pr_url=pr_info.url,
        original_pr_title=pr_info.title,
        original_pr_description=pr_info.description,
        commit_hash=pr_info.commit_hash,
        source_branch=pr_info.source_branch,
        approved_reviewers=pr_info.approved_reviewers,
        current_branch=current_branch,
        current_target=current_target,
        current_alias=current_alias,
        remaining_targets=remaining,
        bitbucket_base_url=pr_info.base_url,
        owner_type=pr_info.owner_type,
        project_key=pr_info.project_key,
        repo_slug=pr_info.repo_slug,
        auto_reviewers=auto_reviewers,
        pr_id=pr_info.pr_id,
    )
    save_state(state)


def _run_normal(args: argparse.Namespace) -> None:
    config = load_config()
    _switch_to_repo(config)

    if has_state():
        _error(
            "A porting session is already in progress.\n"
            "Run 'port --continue' to resume, or 'port --abort' to cancel."
        )

    pr_url = args.pr
    target_args: list[str] = args.to

    base_url, owner_type, owner, repo, pr_id = parse_pr_url(pr_url)
    client = BitbucketClient(base_url, config.pat)

    _info("Validating Bitbucket access token...")
    if not client.validate_token():
        _error(
            "Could not validate your Bitbucket Personal Access Token.\n"
            "Check that it is correct and not expired."
        )
    _success("Token is valid.")

    resolved_targets: list[tuple[str, str]] = []
    for t in target_args:
        try:
            alias, full_name = config.resolve_branch(t)
            resolved_targets.append((alias, full_name))
        except ValueError as exc:
            _error(str(exc))

    _info(f"Fetching PR #{pr_id} details...")
    try:
        pr_info = client.get_pull_request(owner_type, owner, repo, pr_id)
    except BitbucketError as exc:
        _error(str(exc))

    pr_info.base_url = base_url
    pr_info.owner_type = owner_type
    pr_info.project_key = owner
    pr_info.repo_slug = repo

    auto_reviewers = args.ar

    _header("Porting Summary")
    _info(f"PR:     #{pr_info.pr_id} — {pr_info.title}")
    _info(f"Commit: {pr_info.commit_hash[:12]}")
    _info(f"Source: {pr_info.source_branch}")
    _info(f"Targets: {', '.join(f'{a} ({b})' for a, b in resolved_targets)}")
    if auto_reviewers and pr_info.approved_reviewers:
        names = ", ".join(r.get("displayName", r["name"]) for r in pr_info.approved_reviewers)
        _info(f"Reviewers (approved): {names}")
    elif auto_reviewers:
        _info("Reviewers: none (no approved reviewers on the original PR)")
    else:
        _info("Reviewers: not adding (use --ar to auto-add approved reviewers)")

    ensure_git_repo()
    _handle_dirty_tree()
    fetch()

    all_aliases = list(config.branches.keys())
    for i, (alias, target_branch) in enumerate(resolved_targets):
        remaining = [
            {"alias": a, "branch": b} for a, b in resolved_targets[i + 1 :]
        ]
        ok = _process_single_target(
            client, pr_info, target_branch, alias, remaining,
            all_aliases, auto_reviewers,
        )
        if not ok:
            sys.exit(1)

    clear_state()
    _header("Porting completed!")


def _run_continue() -> None:
    config = load_config()
    _switch_to_repo(config)
    ensure_git_repo()

    try:
        state = load_state()
    except FileNotFoundError as exc:
        _error(str(exc))

    client = BitbucketClient(state.bitbucket_base_url, config.pat)

    current = get_current_branch()
    if current != state.current_branch:
        _error(
            f"Expected to be on branch '{state.current_branch}', "
            f"but currently on '{current}'.\n"
            f"Please checkout the correct branch: git checkout {state.current_branch}"
        )

    if has_cherry_pick_in_progress():
        _info("Completing cherry-pick...")
        try:
            cherry_pick_continue()
        except CherryPickConflict as exc:
            _error(
                "Cherry-pick still has conflicts:\n"
                + "\n".join(f"  - {f}" for f in exc.conflicted_files)
                + "\n\nResolve them and run 'port --continue', or 'port --abort' to cancel."
            )
    else:
        conflicted = get_conflicted_files()
        if conflicted:
            _error(
                "There are unresolved conflicts, but no cherry-pick is in progress:\n"
                + "\n".join(f"  - {f}" for f in conflicted)
                + "\n\nResolve them or clean up your working tree, then try again."
            )

    pr_info = PullRequestInfo(
        pr_id=int(state.original_pr_url.rstrip("/").split("/")[-1]),
        title=state.original_pr_title,
        description=state.original_pr_description,
        commit_hash=state.commit_hash,
        source_branch=state.source_branch,
        approved_reviewers=state.approved_reviewers,
        base_url=state.bitbucket_base_url,
        owner_type=state.owner_type,
        project_key=state.project_key,
        repo_slug=state.repo_slug,
    )

    auto_reviewers = state.auto_reviewers
    reviewers = pr_info.approved_reviewers if auto_reviewers else None
    _push_and_create_pr(
        client, pr_info, state.current_branch, state.current_target,
        state.current_alias, reviewers,
    )

    remaining = state.remaining_targets
    all_aliases = list(config.branches.keys())
    if remaining:
        _info(f"\n{len(remaining)} target(s) remaining...")
        fetch()

    for i, target_dict in enumerate(remaining):
        alias = target_dict["alias"]
        target_branch = target_dict["branch"]
        rest = remaining[i + 1 :]
        ok = _process_single_target(
            client, pr_info, target_branch, alias, rest,
            all_aliases, auto_reviewers,
        )
        if not ok:
            sys.exit(1)

    clear_state()
    _header("Porting completed!")


def _run_abort() -> None:
    config = load_config()
    _switch_to_repo(config)
    ensure_git_repo()

    if not has_state():
        _error("Nothing to abort — no porting session in progress.")

    state = load_state()

    if has_cherry_pick_in_progress():
        abort_cherry_pick()

    current_branch = get_current_branch()
    safe_branch = state.current_target
    if current_branch == state.current_branch:
        checkout(safe_branch)

    try:
        delete_local_branch(state.current_branch)
    except GitError:
        pass

    clear_state()

    print()
    _success(f"Aborted porting for branch: {state.current_branch}")
    _info(f"Switched to: {safe_branch}")
    _info("Previously ported branches and pull requests are untouched.")


class _HelpAction(argparse.Action):
    """Custom help action that also prints branch aliases."""

    def __init__(self, option_strings, dest=argparse.SUPPRESS, default=argparse.SUPPRESS, **kwargs):
        super().__init__(option_strings=option_strings, dest=dest, default=default, nargs=0, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        parser.print_help()
        print()
        try:
            config = load_branches_only()
            print(config.format_branches_table())
        except (FileNotFoundError, ValueError):
            print("(No config found — branch aliases not available)")
        parser.exit()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="port",
        description="Port pull requests across Bitbucket release branches.",
        add_help=False,
    )

    parser.add_argument(
        "-h", "--help",
        action=_HelpAction,
        help="Show this help message, including configured branch aliases.",
    )

    parser.add_argument(
        "--pr",
        metavar="URL",
        help="Bitbucket pull request URL to port.",
    )
    parser.add_argument(
        "--to",
        nargs="+",
        metavar="BRANCH",
        help="Target branches (full names or short aliases).",
    )
    parser.add_argument(
        "--ar",
        action="store_true",
        default=False,
        help="Auto-add approved reviewers from the original PR.",
    )
    parser.add_argument(
        "--continue",
        dest="do_continue",
        action="store_true",
        default=False,
        help="Continue after resolving cherry-pick conflicts.",
    )
    parser.add_argument(
        "--abort",
        dest="do_abort",
        action="store_true",
        default=False,
        help="Abort the current porting session (delete conflicted branch, keep previous ports).",
    )

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    try:
        if args.do_continue and args.do_abort:
            _error("Cannot use --continue and --abort together.")
        elif args.do_continue:
            _run_continue()
        elif args.do_abort:
            _run_abort()
        elif args.pr and args.to:
            _run_normal(args)
        else:
            parser.print_help()
            print()
            try:
                config = load_branches_only()
                print(config.format_branches_table())
            except (FileNotFoundError, ValueError):
                pass
            sys.exit(1)
    except GitError as exc:
        _error(str(exc))
    except BitbucketError as exc:
        _error(str(exc))
    except RuntimeError as exc:
        _error(str(exc))
    except FileNotFoundError as exc:
        _error(str(exc))
    except ValueError as exc:
        _error(str(exc))
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(130)
