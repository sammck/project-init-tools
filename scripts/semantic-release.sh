#!/bin/bash

set -eo pipefail

SCRIPT_DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_DIR="$( dirname "SCRIPT_DIR" )"

cd "$PROJECT_DIR"

git_status="$(git status --porcelain)"
if [ -n "$git_status" ]; then
  echo "ERROR: You have uncommitted git changes" >&2
  exit 1
fi
git push

GH_TOKEN="$(cat "$HOME/.private/github-semantic-versioning_token.txt")" semantic-release "$@"
git push
