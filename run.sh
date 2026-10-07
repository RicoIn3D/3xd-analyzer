#!/usr/bin/env bash
# Run the Analyzer pipeline in Docker. Arguments are passed to analyzer.run.
# Example: ./run.sh --bfe 5522814 --center 558376,6369071 --size 400
set -euo pipefail
cd "$(dirname "$0")"
docker build -q -t analyzer . >/dev/null
docker run --rm -u "$(id -u):$(id -g)" -v "$PWD:/work" -v /etc/localtime:/etc/localtime:ro \
  --env-file .env analyzer "$@"
