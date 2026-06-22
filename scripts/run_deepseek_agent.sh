#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ -f "$ROOT_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
fi

export MODEL_PROVIDER="${MODEL_PROVIDER:-deepseek}"
export DEEPSEEK_MODEL_ID="${DEEPSEEK_MODEL_ID:-deepseek-v4-pro}"
export DEEPSEEK_CHEAP_MODEL_ID="${DEEPSEEK_CHEAP_MODEL_ID:-deepseek-v4-flash}"
export DEEPSEEK_BASE_URL="${DEEPSEEK_BASE_URL:-https://api.deepseek.com}"
export DEEPSEEK_THINKING="${DEEPSEEK_THINKING:-enabled}"
export DEEPSEEK_REASONING_EFFORT="${DEEPSEEK_REASONING_EFFORT:-high}"

if [ -z "${DEEPSEEK_API_KEY:-}" ]; then
  echo "DEEPSEEK_API_KEY is not set. Copy .env.example to .env and fill it locally." >&2
  exit 2
fi

cd "$ROOT_DIR"
python agents/s_full.py
