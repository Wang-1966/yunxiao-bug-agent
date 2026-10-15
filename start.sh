#!/usr/bin/env bash

set -e

cd "$(dirname "$0")"
exec bash ./scripts/start.sh "$@"
