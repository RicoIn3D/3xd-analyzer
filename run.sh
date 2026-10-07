#!/usr/bin/env bash
# Run the Analyzier pipeline in Docker. Arguments are passed to analyzier.run.
# Example: ./run.sh --bfe 5522814 --center 558376,6369071 --size 400
set -euo pipefail
cd "$(dirname "$0")"
docker build -q -t analyzier . >/dev/null
docker run --rm -u "$(id -u):$(id -g)" -v "$PWD:/work" -v /etc/localtime:/etc/localtime:ro \
  --env-file .env analyzier "$@"
