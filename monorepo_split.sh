#!/bin/bash

# Split monorepo and push all main branches and all tags into specified remotes
# You must first build the monorepo via "monorepo_build" (uses same parameters as "monorepo_split")
#
# If subdirectory is not specified remote name will be used instead.
#
# You can also override the branch to be merged in, if main is not appropriate.
# But you must specify a directory for this to work (this can match the remote name if this is the
# desired behavior).
#
# GPG Signature Preservation:
# This script automatically preserves GPG commit signatures during the split process by:
# 1. Storing signatures in commit messages before filtering (store_signatures.py)
# 2. Restoring signatures from messages after filtering (restore_signatures.py)
# Requires Python 3.6+ and Git 2.34+ for signature support.
#
# Usage: monorepo_split.sh <remote-name>[:<subdirectory>][:<branch>] <remote-name>[:<subdirectory>][:<branch>] ...
#
# Example: monorepo_split.sh main-repository package-alpha:packages/alpha:master \
#           package-beta:packages/beta:production
#
# Arguments
# `-t` takes the name of the branch to split from the monorepo.
#      If no -t option is specified, `main` is used.
#
# Positional Arguments
#
# All positional arguments follow the format <remote-name>[:<subdirectory>][:<branch>]
# - remote-name  takes the name of the remote to push to
# - subdirectory takes the name of the subdirectory that should be split to this remote
# - branch       takes the name of the branch that is to be merged in.
#                If no branch is provided, the TARGET_BRANCH is used.

TARGET_BRANCH=main

while getopts "t:" opt; do
  case $opt in
    t) TARGET_BRANCH="$OPTARG"
      ;;
    \?) echo "Invalid options -$OPTARG" >&2
      ;;
  esac
done

shift $((OPTIND -1))

# Check provided arguments
if [ "$#" -lt "1" ]; then
    echo 'Please provide at least 1 remote for splitting'
    echo 'Usage: monorepo_split.sh <remote-name>[:<subdirectory>] <remote-name>[:<subdirectory>] ...'
    echo 'Example: monorepo_split.sh main-repository package-alpha:packages/alpha package-beta:packages/beta'
    exit
fi
# Get directory of the other scripts
MONOREPO_SCRIPT_DIR=$(dirname "$0")

git branch main-checkpoint

for PARAM in $@; do
    # Parse parameters in format <remote-name>[:<subdirectory>]
    PARAM_ARR=(${PARAM//:/ })
    REMOTE=${PARAM_ARR[0]}
    SUBDIRECTORY=${PARAM_ARR[1]}
    if [ "$SUBDIRECTORY" == "" ]; then
        SUBDIRECTORY=$REMOTE
    fi
    BRANCH_TO_MERGE=${PARAM_ARR[2]}
    if [ "$BRANCH_TO_MERGE" == "" ]; then
        BRANCH_TO_MERGE=$TARGET_BRANCH
    fi

    # Capture remote URL before git-filter-repo removes it
    REMOTE_URL=$(git remote get-url $REMOTE 2>/dev/null)
    if [ $? -eq 0 ]; then
        echo "Captured remote URL for '$REMOTE': $REMOTE_URL"
    else
        echo "Warning: Remote '$REMOTE' does not exist. Skipping."
        continue
    fi

    # Rewrite git history of target branch
    echo "Splitting repository for the remote '$REMOTE' from subdirectory $SUBDIRECTORY"
    git checkout $TARGET_BRANCH

    # Clear any previous filter-repo metadata to avoid conflicts with signature restoration
    if [ -d .git/filter-repo ]; then
        echo "Clearing previous filter-repo metadata..."
        rm -rf .git/filter-repo
    fi

    # Check if there are any GPG signatures in the repository
    HAS_SIGNATURES=$(git log $TARGET_BRANCH $(git tag -l) --show-signature 2>&1 | grep -c "gpg: Signature made" || true)

    # Store GPG signatures in commit messages before filtering (only if signatures exist)
    if [ "$HAS_SIGNATURES" -gt 0 ]; then
        echo "Found $HAS_SIGNATURES GPG signature(s), storing in commit messages..."
        python3 $MONOREPO_SCRIPT_DIR/store_signatures.py --refs $TARGET_BRANCH $(git tag -l)
        if [ $? -ne 0 ]; then
            echo "Warning: Failed to store signatures, continuing anyway..."
        fi
    else
        echo "No GPG signatures found, skipping signature preservation..."
    fi

    $MONOREPO_SCRIPT_DIR/rewrite_history_from.sh $SUBDIRECTORY $TARGET_BRANCH $(git tag -l)
    if [ $? -eq 0 ]; then
        # Restore GPG signatures from commit messages after filtering (only if they were stored)
        if [ "$HAS_SIGNATURES" -gt 0 ]; then
            echo "Restoring GPG signatures from commit messages..."
            python3 $MONOREPO_SCRIPT_DIR/restore_signatures.py --refs $TARGET_BRANCH $(git tag -l)
            if [ $? -ne 0 ]; then
                echo "Warning: Failed to restore signatures, continuing anyway..."
            fi
        fi

        # Restore remote URL after git-filter-repo removed it
        if [ ! -z "$REMOTE_URL" ]; then
            git remote remove $REMOTE 2>/dev/null
            git remote add $REMOTE "$REMOTE_URL"
            echo "Restored remote '$REMOTE' with URL: $REMOTE_URL"
        fi

        echo "Pushing changes made on '$TARGET_BRANCH' and all tags into '$REMOTE/$BRANCH_TO_MERGE'"
        git push --tags $REMOTE $TARGET_BRANCH:$BRANCH_TO_MERGE
        git lfs push $REMOTE $TARGET_BRANCH
    else
        echo "Splitting repository for the remote '$REMOTE' failed! Not pushing anything into it."
    fi
    git reset main-checkpoint --hard
    $MONOREPO_SCRIPT_DIR/tag_refs_restore.sh
done

git branch -d main-checkpoint
