# Environment setup for Mirage with Qwen3.5-0.8B
# Source this file: source env_setup.sh (bash/zsh) or source env_setup.sh (fish)

# Update this line to point to the submodule
export MIRAGE_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")/third_party/mirage" && pwd)"
export PYTHONPATH="$MIRAGE_HOME/python:$PYTHONPATH"


# Optional: Set CUDA-related environment variables
# export CUDA_VISIBLE_DEVICES=0

echo "Mirage environment configured:"
echo "  MIRAGE_HOME=$MIRAGE_HOME"
echo "  PYTHONPATH includes: $MIRAGE_HOME/python"
