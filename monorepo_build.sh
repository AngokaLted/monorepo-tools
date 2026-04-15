#!/bin/bash

# Build monorepo from specified remotes
# You must first add the remotes by "git remote add <remote-name> <repository-url>" and fetch from them by "git fetch --all"
# Final monorepo will contain all branches from the first remote and main branches of all remotes will be merged.
#
# The repository's combined branch will be main, not master.
#
# If subdirectory is not specified remote name will be used instead
#
# You can also override the branch to be merged in, if main is not appropriate.
# But you must specify a directory for this to work (this can match the remote name if this is the
# desired behavior).
#
# Usage: monorepo_build.sh <remote-name>[:<subdirectory>][:<branch>] <remote-name>[:<subdirectory>][:<branch>]  ...
#
# Example: monorepo_build.sh main-repository package-alpha:packages/alpha:master package-beta:packages/beta:production

# Check provided arguments
if [ "$#" -lt "2" ]; then
    echo 'Please provide at least 2 remotes to be merged into a new monorepo'
    echo 'Usage: monorepo_build.sh <remote-name>[:<subdirectory>] <remote-name>[:<subdirectory>] ...'
    echo 'Example: monorepo_build.sh main-repository package-alpha:packages/alpha package-beta:packages/beta'
    exit
fi
# Get directory of the other scripts
MONOREPO_SCRIPT_DIR=$(dirname "$0")
# Wipe original refs (possible left-over back-up after rewriting git history)
$MONOREPO_SCRIPT_DIR/original_refs_wipe.sh
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
        BRANCH_TO_MERGE=main
    fi

    echo "Fetching LFS files for remote '$REMOTE'"
    git lfs fetch --all $REMOTE $REMOTE/$BRANCH_TO_MERGE

    # Rewrite all branches from the first remote, only main/target branch from others
    if [ "$PARAM" == "$1" ]; then
        echo "Building all branches of the remote '$REMOTE'"
        $MONOREPO_SCRIPT_DIR/load_branches_from_remote.sh $REMOTE
        $MONOREPO_SCRIPT_DIR/rewrite_history_into.sh $SUBDIRECTORY --refs $(git branch --format='%(refname:short)')
        MERGE_REFS=$BRANCH_TO_MERGE
    else
        echo "Building branch '$BRANCH_TO_MERGE' of the remote '$REMOTE'"
        git checkout --detach $REMOTE/$BRANCH_TO_MERGE
        # NOTE: Because we're checking out a detached branch, we don't end up on anything with a name,
        # so selecting "main" is meaningless. We need to specify HEAD to keep the history here.
        $MONOREPO_SCRIPT_DIR/rewrite_history_into.sh $SUBDIRECTORY --refs HEAD
        MERGE_REFS="$MERGE_REFS $(git rev-parse HEAD)"
    fi
    # Wipe the back-up of original history
    $MONOREPO_SCRIPT_DIR/original_refs_wipe.sh
done
# Merge all target branches
COMMIT_MSG="merge multiple repositories into a monorepo"$'\n'$'\n'"- merged using: 'monorepo_build.sh $@'"$'\n'"- see https://github.com/embeddedartistry/monorepo-tools"
git checkout main
echo "Merging refs: $MERGE_REFS"
git merge --no-commit -q $MERGE_REFS --allow-unrelated-histories
echo 'Resolving conflicts using trees of all parents'
for REF in $MERGE_REFS; do
    # Add all files from all target branches into index
    # "git read-tree" with multiple refs cannot be used as it is limited to 8 refs
    git ls-tree -r $REF | git update-index --index-info
done
git commit -m "$COMMIT_MSG"
git reset --hard

