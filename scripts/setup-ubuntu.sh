#!/usr/bin/env bash
# Set up Kizami on an Ubuntu machine: system packages, a virtual environment and the
# timing models. Run it from the repository folder:
#
#     scripts/setup-ubuntu.sh --model-dir ~/kizami-models --dedicated
#
# It can be run again at any time: it skips what is already in place. It needs sudo
# for the system packages (--no-apt skips that step) and about 2 GB of downloads per
# model. It never touches audio or lyrics.
set -euo pipefail

MODEL_DIR="${PWD}/models"
WITH_DEDICATED=0
WITH_SEPARATE=0
RUN_APT=1
RUN_TESTS=1

usage() {
    cat <<'TEXT'
Usage: scripts/setup-ubuntu.sh [options]

  --model-dir DIR   where the models go (default: ./models)
  --dedicated       also fetch the English, Spanish and Korean models (3.8 GB)
  --separate        also install Demucs, to separate the voice from full songs
  --no-apt          do not install system packages (python3-venv, ffmpeg, curl)
  --no-tests        do not run the test suite at the end
  -h, --help        show this text

The models are large: the Japanese and letter models take 2.6 GB, and all five
take 6.4 GB. Reserve about 15 GB for the models and the model-output cache.
TEXT
}

while [ $# -gt 0 ]; do
    case "$1" in
        --model-dir) MODEL_DIR="${2:?--model-dir needs a folder}"; shift 2 ;;
        --dedicated) WITH_DEDICATED=1; shift ;;
        --separate) WITH_SEPARATE=1; shift ;;
        --no-apt) RUN_APT=0; shift ;;
        --no-tests) RUN_TESTS=0; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
[ -f pyproject.toml ] || { echo "run this from the Kizami repository" >&2; exit 1; }
mkdir -p "$MODEL_DIR"
MODEL_DIR="$(cd "$MODEL_DIR" && pwd)"

say() { printf '\n== %s\n' "$1"; }

free_gb() { df -BG --output=avail "$1" | tail -1 | tr -dc '0-9'; }

say "machine"
echo "repository: $REPO"
echo "models:     $MODEL_DIR"
uname -srm
if command -v nvidia-smi >/dev/null 2>&1; then
    GPU=1
    nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader || GPU=0
else
    GPU=0
    echo "no NVIDIA GPU found (nvidia-smi is not there): the models will run on the CPU"
fi
echo "cores: $(nproc), free disk at the models: $(free_gb "$MODEL_DIR") GB"
NEEDED=9; [ "$WITH_DEDICATED" = 1 ] && NEEDED=15
[ "$(free_gb "$MODEL_DIR")" -lt "$NEEDED" ] && echo "WARNING: less than $NEEDED GB free; the download may not fit"

if [ "$RUN_APT" = 1 ]; then
    say "system packages"
    sudo apt-get update
    sudo apt-get install -y --no-install-recommends python3-venv python3-pip ffmpeg curl ca-certificates
fi
for tool in python3 ffmpeg curl; do
    command -v "$tool" >/dev/null || { echo "$tool is missing; run without --no-apt or install it" >&2; exit 1; }
done

say "virtual environment"
[ -d .venv ] || python3 -m venv .venv
PY=".venv/bin/python"
"$PY" -m pip install --quiet --upgrade pip
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
    || { echo "Python 3.10 or newer is needed; this is $("$PY" -V)" >&2; exit 1; }

say "Python packages"
if [ "$GPU" = 0 ] && ! "$PY" -c 'import torch' 2>/dev/null; then
    # the default wheels carry the CUDA libraries (about 3 GB); without a GPU take the CPU ones
    "$PY" -m pip install --index-url https://download.pytorch.org/whl/cpu torch torchaudio
fi
"$PY" -m pip install -e ".[generate,dev]"
[ "$WITH_SEPARATE" = 1 ] && "$PY" -m pip install -e ".[separate]"
"$PY" - <<'PYTEXT'
import torch
print("torch", torch.__version__, "CUDA available:", torch.cuda.is_available())
PYTEXT

# ---------------------------------------------------------------- models
# Each entry: folder, Hugging Face repository, revision, files. The revisions are
# pinned, so a later change in the repository cannot change the timing you measure.
hf_get() {  # folder repo revision file...
    local dir="$MODEL_DIR/$1" repo="$2" rev="$3"; shift 3
    mkdir -p "$dir"
    for file in "$@"; do
        if [ -s "$dir/$file" ]; then
            echo "have $1/$file"
            continue
        fi
        echo "fetching $1/$file"
        curl -fL --retry 3 --retry-delay 2 -o "$dir/$file.part" \
            "https://huggingface.co/$repo/resolve/$rev/$file"
        mv "$dir/$file.part" "$dir/$file"
    done
}

check_size() {  # path expected-bytes
    local got
    got=$(stat -c %s "$1")
    [ "$got" = "$2" ] || { echo "$1 is $got bytes, expected $2; delete it and run again" >&2; exit 1; }
}

say "Japanese model (w2v2-hiragana, 1.3 GB)"
hf_get w2v2-hiragana vumichien/wav2vec2-large-xlsr-japanese-hiragana main \
    config.json model.safetensors preprocessor_config.json special_tokens_map.json \
    tokenizer_config.json vocab.json

say "letter model for Korean, English and Spanish (mms_fa, 1.3 GB)"
# CC-BY-NC: check its licence before any commercial use.
mkdir -p "$MODEL_DIR/mms_fa"
if [ -s "$MODEL_DIR/mms_fa/model.pt" ]; then
    echo "have mms_fa/model.pt"
else
    curl -fL --retry 3 --retry-delay 2 -o "$MODEL_DIR/mms_fa/model.pt.part" \
        https://dl.fbaipublicfiles.com/mms/torchaudio/ctc_alignment_mling_uroman/model.pt
    mv "$MODEL_DIR/mms_fa/model.pt.part" "$MODEL_DIR/mms_fa/model.pt"
fi
check_size "$MODEL_DIR/mms_fa/model.pt" 1262047414

convert_bin() {  # folder expected-bin-bytes
    local dir="$MODEL_DIR/$1"
    [ -s "$dir/model.safetensors" ] && { echo "have $1/model.safetensors"; return; }
    check_size "$dir/pytorch_model.bin" "$2"
    echo "converting $1/pytorch_model.bin to model.safetensors"
    "$PY" -I - "$dir" <<'PYTEXT'
import sys
from pathlib import Path

import torch
from safetensors.torch import save_file

d = Path(sys.argv[1])
# weights_only=True reads the tensors without running any code from the file
state = torch.load(d / "pytorch_model.bin", map_location="cpu", weights_only=True)
save_file({k: v.contiguous() for k, v in state.items()}, d / "model.safetensors", metadata={"format": "pt"})
print(len(state), "tensors")
PYTEXT
    rm -f "$dir/pytorch_model.bin"
}

if [ "$WITH_DEDICATED" = 1 ]; then
    say "English model (w2v2-en, 1.3 GB)"
    hf_get w2v2-en facebook/wav2vec2-large-960h-lv60-self 54074b1c16f4de6a5ad59affb4caa8f2ea03a119 \
        config.json vocab.json preprocessor_config.json pytorch_model.bin
    convert_bin w2v2-en 1262055246

    say "Spanish model (w2v2-es, 1.3 GB)"
    hf_get w2v2-es jonatasgrosman/wav2vec2-large-xlsr-53-spanish 96d7e9b4e4a78af515a3c6d3cee7c0826045d276 \
        config.json vocab.json preprocessor_config.json pytorch_model.bin
    convert_bin w2v2-es 1262101911

    say "Korean model (w2v2-ko, 1.3 GB; off by default, kept for comparison)"
    hf_get w2v2-ko kresnik/wav2vec2-large-xlsr-korean 629c9a3501c10ba128bf3fa1eebb12af3be03f61 \
        config.json vocab.json preprocessor_config.json model.safetensors
    check_size "$MODEL_DIR/w2v2-ko/model.safetensors" 1266747952
fi

say "checking the models load"
"$PY" -I - "$MODEL_DIR" <<'PYTEXT'
import sys
from pathlib import Path

from transformers import Wav2Vec2ForCTC

from kizami.generate import KANA_MODEL, MMS_MODEL
from kizami.generate.dedicated import MODEL_DIRS, model_vocab

root = Path(sys.argv[1])
expected = {"w2v2-en": (32, 0), "w2v2-es": (41, 0), "w2v2-ko": (1205, 1204)}
assert (root / MMS_MODEL).is_file(), f"missing {root / MMS_MODEL}"
print(f"{MMS_MODEL}: {(root / MMS_MODEL).stat().st_size} bytes")
for name in [KANA_MODEL, *MODEL_DIRS.values()]:
    folder = root / name
    if not folder.is_dir():
        print(f"{name}: not installed")
        continue
    vocab, blank = model_vocab(folder)
    info = Wav2Vec2ForCTC.from_pretrained(str(folder), use_safetensors=True, output_loading_info=True)[1]
    unexpected = {k: sorted(v) for k, v in info.items() if v and k != "error_msgs"}
    want = expected.get(name)
    size = (len(vocab), blank)
    note = "" if want is None or want == size else f" (expected {want})"
    print(f"{name}: {len(vocab)} tokens, blank {blank}{note}, loading {unexpected or 'clean'}")
PYTEXT

if [ "$RUN_TESTS" = 1 ]; then
    say "tests"
    "$PY" -m pytest -q
fi

say "done"
cat <<TEXT
Use the models with:

    $REPO/.venv/bin/kizami generate SONG_FOLDER --model-dir $MODEL_DIR

or put them in the environment, so --model-dir is not needed every time:

    export KIZAMI_MODEL_DIR=$MODEL_DIR
TEXT
