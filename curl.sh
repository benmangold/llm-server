#!/usr/bin/env bash

set -eo pipefail

cd "$(dirname "$0")"

curl -s localhost:8000/generate \
    -H 'content-type: application/json' \
    -d '{"prompt":"Respond as fast as you can. What is 1 plus 1?","model":"llama3.2"}' \
    | python3 -m json.tool
