#!/bin/bash

# Backup tag refs into refs/original-tags/, because `git filter-repo` doesn't do it
#
# Usage: tag_refs_backup.sh

for TAG_REF in $(git for-each-ref --format="%(refname)" refs/tags/); do
    git update-ref refs/original-tags/$TAG_REF $TAG_REF
done
