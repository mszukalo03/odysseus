#!/usr/bin/env bash
# devbox-setup.sh — make this machine (pc / laptop) an Odysseus dev box.
#
# Layout:
#   pc, laptop  ~/omegaV2/code/odysseus   develop + Claude Code; push to GitHub
#   xps         ~/omega/odysseus          deploy-only; runs origin/stable via ody-deploy
#
# Code moves through GitHub only (origin = your fork, upstream = odysseus-dev).
# ~/omegaV2 is a Syncthing vault whose shared .stglobalignore excludes /code, so
# the checkout here is never synced file-by-file between machines.
#
# Safe to re-run. It never discards work: anything unpushed is reported and
# left alone.
set -euo pipefail

VAULT=${ODY_VAULT:-$HOME/omegaV2}
DEST=${ODY_DEST:-$VAULT/code/odysseus}
ORIGIN=${ODY_ORIGIN:-git@github.com:mszukalo03/odysseus.git}
UPSTREAM=${ODY_UPSTREAM:-https://github.com/odysseus-dev/odysseus.git}

say() { printf '\n== %s\n' "$*"; }

# 1. Syncthing must leave code/ alone on this device too.
say "Syncthing ignores"
if [[ -d $VAULT ]]; then
    if ! grep -qxF '#include .stglobalignore' "$VAULT/.stignore" 2>/dev/null; then
        echo '#include .stglobalignore' >> "$VAULT/.stignore"
        echo "added '#include .stglobalignore' to $VAULT/.stignore"
    fi
    if ! grep -qxF '/code' "$VAULT/.stglobalignore" 2>/dev/null; then
        echo "STOP: $VAULT/.stglobalignore has no '/code' line yet." >&2
        echo "Let Syncthing finish syncing the vault, then re-run." >&2
        exit 1
    fi
    echo "ok: /code is excluded from Syncthing"
else
    echo "no vault at $VAULT — skipping"
fi

# 2. Other Odysseus clones on this machine holding work that isn't on GitHub.
say "Other Odysseus checkouts"
found=0
while IFS= read -r gitdir; do
    repo=${gitdir%/.git}
    [[ $repo == "$DEST" ]] && continue
    git -C "$repo" remote -v 2>/dev/null | grep -q 'odysseus' || continue
    found=1
    git -C "$repo" fetch --quiet --all 2>/dev/null || true
    unpushed=$( (git -C "$repo" log --branches --not --remotes --oneline || true) | wc -l)
    dirty=$( (git -C "$repo" status --porcelain || true) | wc -l)
    stashes=$( (git -C "$repo" stash list || true) | wc -l)
    if (( unpushed + dirty + stashes )); then
        echo "!! $repo: $unpushed unpushed commit(s), $dirty changed file(s), $stashes stash(es)"
        git -C "$repo" log --branches --not --remotes --format='     %h %d %s' | head -20
        echo "   Push it as a branch before retiring this copy, e.g.:"
        echo "   cd $repo && git switch -c rescue/$(hostname)-old && git add -A && git commit -m wip && git push -u origin HEAD"
    else
        echo "ok: $repo has nothing unpushed (safe to delete)"
    fi
done < <(find "$HOME" -maxdepth 6 \( -name node_modules -o -name .cache -o -name .local -o -name .venv \) -prune \
              -o -type d -name .git -print 2>/dev/null)
(( found )) || echo "none"

# 3. Clone, or adopt an existing checkout.
say "Checkout at $DEST"
if [[ ! -e $DEST/.git ]]; then
    if [[ -d $DEST && -n $(ls -A "$DEST") ]]; then
        echo "STOP: $DEST exists but is not a git checkout. Move it aside and re-run." >&2
        exit 1
    fi
    mkdir -p "$DEST"
    git clone "$ORIGIN" "$DEST"
fi
cd "$DEST"
git remote set-url origin "$ORIGIN"
git remote get-url upstream >/dev/null 2>&1 || git remote add upstream "$UPSTREAM"
git remote set-url --push upstream no_push   # PRs go through your fork, never direct
git config pull.rebase true                  # hand-offs between machines stay linear
git config rebase.autoStash true
git config fetch.prune true
git config push.autoSetupRemote true
git fetch --all --prune --quiet

# 4. Bring dev up to date without touching local work.
say "Branches"
current=$(git branch --show-current)
if ! git show-ref --verify --quiet refs/heads/dev; then
    git branch --quiet --track dev origin/dev
    echo "created dev -> origin/dev"
fi
ahead=$(git rev-list --count origin/dev..dev)
if (( ahead )); then
    echo "!! dev has $ahead commit(s) not on GitHub — push them (git pull --rebase && git push):"
    git log --format='     %h %s' origin/dev..dev
elif [[ $current == dev ]]; then
    git merge --ff-only --quiet origin/dev && echo "dev fast-forwarded to origin/dev"
else
    git branch --quiet -f dev origin/dev && echo "dev reset to origin/dev (had no local commits)"
fi

# Local branches whose remote is gone and whose commits are all in stable.
for b in $(git for-each-ref --format='%(refname:short) %(upstream:track)' refs/heads | awk '$2=="[gone]"{print $1}'); do
    [[ $b == "$current" ]] && continue
    if git merge-base --is-ancestor "$b" origin/stable; then
        git branch --quiet -D "$b" && echo "deleted merged branch $b"
    else
        echo "!! $b: its remote branch is gone but it has commits not in stable — kept"
    fi
done

say "Done"
git status --short --branch | head -1
echo "Workflow: branch off dev -> commit -> push -> merge to dev; release = fast-forward stable to dev,"
echo "then on xps: ody-deploy. Switching machines: push first (a wip/ branch is fine), pull on the other."
