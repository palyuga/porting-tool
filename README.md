# Port

CLI tool for porting Bitbucket pull requests across release branches.

Automates the tedious process of cherry-picking a commit to multiple release branches
and creating pull requests with the correct description.

## What the tool does

For each target branch, the tool:

1. Creates a new branch from the target, reusing the original branch name with
   the target alias swapped in (e.g. `bugfix/jira-7777-fix-156` becomes
   `bugfix/jira-7777-fix-176` when porting to the `176` branch)
2. Cherry-picks the commit from the original PR
3. Pushes the new branch to origin
4. Creates a Bitbucket PR with:
   - Same title as the original PR
   - Original description + a clickable porting reference link at the bottom
   - Approved reviewers from the original PR (only when `--ar` is used)

## Requirements

- Python 3.11+
- git
- Tested with Bitbucket v9.4.17

## Configuration Setup

### 1. Branches' configuration

Copy `example.config.toml` to `~/.porting/config.toml`:

```bash
mkdir ~/.porting
cp example.config.toml ~/.porting/config.toml
```

Set the `[repo]` path to your git project and edit the branches:

```toml
[repo]
path = "C:/projects/tdcore"    # absolute path to your git repository

[branches]
"156" = "release/tdcore-156-branch"
"168" = "release/tdcore-168-branch"
"176" = "release/tdcore-176-branch"
"m" = "master"
```

The `[repo]` path can be absolute or relative to the config file. It's required
when the config is not inside the git repo. The tool will automatically switch
to this directory before running git commands.

### 2. Bitbucket Access Token

The tool needs an Access Token to call the Bitbucket REST API. Create one in Bitbucket:

1. Click your avatar (top-right) -> **Manage account**
2. In the left sidebar, click **HTTP access tokens**
3. Click **Create a token**
4. Name it, grant **Project read** and **Repository write** permissions
5. Click **Create** and copy the token

Then configure it (choose one):

**Option A — Environment variable (recommended):**

```bash
# Windows (permanent, restart terminal after)
setx BITBUCKET_PAT "your-token-here"

# Linux/macOS
echo 'export BITBUCKET_PAT="your-token-here"' >> ~/.bashrc
source ~/.bashrc
```

**Option B — Auth file:**

Create `~/.porting/auth.toml`:

```toml
pat = "your-token-here"
```

Make sure your SSH key is configured in git for push/fetch operations.
The tool uses your existing git/SSH setup — no additional SSH configuration is needed.

## Usage

### Port a PR to one or more branches

```bash
# Using short aliases
python -m port --pr https://bitbucket.example.com/projects/PROJ/repos/my-repo/pull-requests/123 --to 168 176

# Using full branch names
python -m port --pr https://bitbucket.example.com/projects/PROJ/repos/my-repo/pull-requests/123 --to release/tdcore-168-branch

# Mix aliases and full names
python -m port --pr https://bitbucket.example.com/projects/PROJ/repos/my-repo/pull-requests/123 --to 168 release/tdcore-176-branch m
```

### Auto-add reviewers

By default, ported PRs are created without reviewers. Use `--ar` to automatically
add the reviewers who already approved the original PR:

```bash
port --pr https://bitbucket.example.com/projects/PROJ/repos/my-repo/pull-requests/123 --to 168 176 --ar
```

### Handle conflicts

If a cherry-pick has conflicts, the tool stops and tells you which files to fix:

```
  CONFLICT: Cherry-pick has merge conflicts!
  Conflicted files:
    - src/main/java/com/example/Service.java

  To resolve:
    1. Open the conflicted files in your IDE
    2. Resolve all conflicts and save
    3. Run: port --continue
```

After resolving conflicts in your IDE, resume:

```bash
port --continue
```

The tool will complete the cherry-pick, push the branch, create the PR,
and continue with any remaining target branches.

## Adding 'port' as a global command

You can run directly as a python module from the tool's root directory:

```bash/cmd
python -m port --help
```

If you want a shorter `port` command to be available from any directory, 
add the `porting-tool/bin` directory to your PATH:

**Windows:**

1. Press Win+R, run `SystemPropertiesAdvanced`
2. Click **Environment Variables**, under **User variables**, select **Path** -> **Edit**
4. Add a full path, e.g. `C:\tools\porting-tool\bin`
5. Click **OK**, restart your terminal

**macOS/Linux:**

```bash
echo 'export PATH="$PATH:/path/to/porting-tool/bin"' >> ~/.bashrc
source ~/.bashrc
```

After that, the `port` command works from any directory:

```bash
port --help
```