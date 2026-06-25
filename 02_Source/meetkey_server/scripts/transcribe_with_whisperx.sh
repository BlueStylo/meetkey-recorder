#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 AUDIO_PATH OUT_DIR" >&2
  exit 2
fi

AUDIO_PATH="$1"
OUT_DIR="$2"

WHISPERX_BASE="/data/datasets/whisperx"
WHISPERX_ENV="$WHISPERX_BASE/env"

mkdir -p "$OUT_DIR" "$WHISPERX_BASE/tmp" "$WHISPERX_BASE/cache"

export PATH="$WHISPERX_ENV/bin:$PATH"
export TMPDIR="$WHISPERX_BASE/tmp"
export HF_HOME="$WHISPERX_BASE/cache/huggingface"
export XDG_CACHE_HOME="$WHISPERX_BASE/cache/xdg"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-4}"

exec "$WHISPERX_ENV/bin/whisperx" "$AUDIO_PATH" \
  --model large-v3 \
  --language ko \
  --device cuda \
  --compute_type float16 \
  --batch_size "${MEETKEY_WHISPERX_BATCH_SIZE:-4}" \
  --threads "${MEETKEY_WHISPERX_THREADS:-4}" \
  --output_format txt \
  --output_dir "$OUT_DIR" \
  --no_align
