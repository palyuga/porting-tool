# Porting Tool

Cherry-picks a commit from a Bitbucket PR to one or more release branches and opens a new PR for each one.  
Note: currently can port only one-commit PRs.

## Quick setup

**1. Python 3.11+** — [python.org/downloads](https://www.python.org/downloads/)

**2. Clone and configure**

```bash
git clone <this-repo-url>
mkdir ~/.porting
cp example.config.toml ~/.porting/config.toml
```

Edit `~/.porting/config.toml` — set `path` to your local repo clone and adjust the branch aliases:

```toml
[repo]
path = "C:/projects/tdcore"

[branches]
"168" = "release/tdcore-168-branch"
"176" = "release/tdcore-176-branch"
"m"   = "master"
```

**3. Bitbucket HTTP access token** — Bitbucket → avatar → Manage account → HTTP access tokens → Create token (grant *Project read* + *Repository write*). Then:

```bash
# Windows (restart terminal after)
setx BITBUCKET_PAT "your-token-here"

# macOS/Linux
echo 'export BITBUCKET_PAT="your-token-here"' >> ~/.bashrc && source ~/.bashrc
```

**4. Run**

```bash
python -m port --pr https://bitbucket.example.com/.../pull-requests/123 --to 168 176 m
```

Add `--ar` to carry over approved reviewers from the original PR.

**Optional: `port` as a global command** — add `porting-tool/bin` to your `PATH` (Windows: System Properties → Environment Variables → Path; macOS/Linux: `export PATH="$PATH:/path/to/porting-tool/bin"`), then use `port` instead of `python -m port` from any directory.

---

## How it works

For each target branch the tool:

1. Creates a new branch from the target, replacing the source alias in the name (e.g. `bugfix/PROJ-1-fix-156` → `bugfix/PROJ-1-fix-176`)
2. Cherry-picks the commit from the original PR
3. Pushes the branch and opens a Bitbucket PR with the same title, original description plus a porting reference link, and optionally the approved reviewers (`--ar`)

## Usage

```bash
# Port to multiple branches using aliases
port --pr https://bitbucket.example.com/projects/PROJ/repos/my-repo/pull-requests/123 --to 168 176 m

# Also carry over approved reviewers
port --pr <url> --to 168 176 --ar

# Full branch names work too
port --pr <url> --to release/tdcore-168-branch
```

### Conflict resolution

If cherry-pick has conflicts the tool pauses and lists the affected files. Resolve them in your IDE, then:

```bash
git add <resolved-files>
port --continue   # completes the cherry-pick, pushes, creates the PR, and moves on
```

To cancel the current branch and leave previously ported branches untouched:

```bash
port --abort
```

## Auth token alternative

If you prefer a file over an environment variable, create `~/.porting/auth.toml`:

```toml
pat = "your-token-here"
```

The tool checks `BITBUCKET_PAT` first, then falls back to this file.