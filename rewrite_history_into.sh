#!/bin/bash -ex

# Rewrite git history so that all filepaths are in a specific subdirectory
# You can specify additional refs to further control what commits to rewrite.
# (defaults to rewriting history of the checked-out branch)
# All tags in the provided range will be rewritten as well
#
# Usage: rewrite_history_into.sh <subdirectory> [<ref list>]
#
# Example: rewrite_history_into.sh packages/alpha
# Example: rewrite_history_into.sh main-repository --branches

SUBDIRECTORY=$1
REF_LIST=${@:2}
echo "Rewriting history into a subdirectory '$SUBDIRECTORY'"
# All paths in the index are prefixed with a subdirectory and the index is updated
git-filter-repo --to-subdirectory-filter $SUBDIRECTORY --force $REF_LIST
