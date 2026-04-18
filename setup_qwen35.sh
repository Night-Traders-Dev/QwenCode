#!/bin/bash
# Setup script for Mirage with Qwen3.5-0.8B support
# Compatible with bash, fish, and zsh

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Color codes for output (works in bash, fish, zsh)
if command -v tput >/dev/null 2>&1; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    BLUE='\033[0;34m'
    NC='\033[0m' # No Color
else
    RED=''
    GREEN=''
    YELLOW=''
    BLUE=''
    NC=''
fi

# Print functions that work across shells
print_info() {
    printf "${BLUE}[INFO]${NC} %s\n" "$1"
}

print_success() {
    printf "${GREEN}[SUCCESS]${NC} %s\n" "$1"
}

print_warning() {
    printf "${YELLOW}[WARNING]${NC} %s\n" "$1"
}

print_error() {
    printf "${RED}[ERROR]${NC} %s\n" "$1"
}

# Check if running in a shell that supports source
if [ -n "$FISH_VERSION" ]; then
    SHELL_TYPE="fish"
elif [ -n "$ZSH_VERSION" ]; then
    SHELL_TYPE="zsh"
else
    SHELL_TYPE="bash"
fi

print_info "Detected shell: $SHELL_TYPE"
print_info "Script directory: $SCRIPT_DIR"

# Step 1: Initialize git submodules
print_info "Step 1: Initializing git submodules..."
cd "$SCRIPT_DIR"
if [ -f ".gitmodules" ]; then
    git submodule update --init --recursive
    if [ $? -eq 0 ]; then
        print_success "Git submodules initialized successfully"
    else
        print_warning "Git submodule initialization had issues (may already be initialized)"
    fi
else
    print_warning "No .gitmodules file found, skipping submodule initialization"
fi

# Step 2: Create deps directory if needed and clone dependencies
print_info "Step 2: Setting up dependencies..."
if [ ! -d "deps" ]; then
    mkdir -p deps
fi

# Clone CUTLASS if not present
if [ ! -d "deps/cutlass" ]; then
    print_info "Cloning CUTLASS..."
    git clone --depth 1 https://github.com/NVIDIA/cutlass.git deps/cutlass
fi

# Clone Z3 if not present
if [ ! -d "deps/z3" ]; then
    print_info "Cloning Z3..."
    git clone --depth 1 --branch z3-4.16.0 https://github.com/Z3Prover/z3.git deps/z3
fi

# Clone nlohmann/json if not present
if [ ! -d "deps/json" ]; then
    print_info "Cloning nlohmann/json..."
    git clone --depth 1 https://github.com/nlohmann/json.git deps/json
fi

print_success "Dependencies setup complete"

# Step 3: Install Python dependencies
print_info "Step 3: Installing Python dependencies..."
pip install --upgrade pip
pip install cmake cython z3-solver==4.16 torch numpy graphviz tqdm protobuf

# Install transformers with specific version for Qwen3.5 compatibility
pip install "transformers>=4.57.1" accelerate==1.8.0

# Install tg4perfetto
pip install "tg4perfetto @ git+https://github.com/flashinfer-ai/tg4perfetto.git"

# Install cuda-python if available
pip install cuda-python 2>/dev/null || print_warning "cuda-python installation failed (optional)"

print_success "Python dependencies installed"

# Step 4: Build and install Mirage
print_info "Step 4: Building and installing Mirage..."
cd "$SCRIPT_DIR/third_party/mirage"
pip install -e . -v

if [ $? -eq 0 ]; then
    print_success "Mirage installed successfully"
else
    print_error "Mirage installation failed"
    exit 1
fi

# Step 5: Verify installation
print_info "Step 5: Verifying installation..."
python3 -c "import mirage; print('Mirage version:', mirage.__version__ if hasattr(mirage, '__version__') else 'unknown')"
if [ $? -eq 0 ]; then
    print_success "Mirage import successful"
else
    print_error "Mirage import failed"
    exit 1
fi

# Step 6: Verify Qwen3.5-0.8B model registration
print_info "Step 6: Verifying Qwen3.5-0.8B model support..."
python3 -c "
from mirage.mpk.model_registry import get_builder
try:
    builder = get_builder('Qwen/Qwen3.5-0.8B')
    print('Qwen3.5-0.8B builder found:', builder.__name__)
except ValueError as e:
    print('Error:', e)
    exit(1)
"
if [ $? -eq 0 ]; then
    print_success "Qwen3.5-0.8B model support verified"
else
    print_error "Qwen3.5-0.8B model support verification failed"
    exit 1
fi

print_info "Step 7: Creating environment configuration..."
# Step 7: Create environment setup instructions
cat > "$SCRIPT_DIR/env_setup.sh" << 'ENVEOF'
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
ENVEOF

print_success "Environment setup file created: $SCRIPT_DIR/env_setup.sh"

# Final summary
echo ""
print_success "=========================================="
print_success "Setup complete!"
print_success "=========================================="
echo ""
print_info "To use Mirage with Qwen3.5-0.8B:"
echo ""
echo "1. Source the environment setup:"
if [ "$SHELL_TYPE" = "fish" ]; then
    echo "   source $SCRIPT_DIR/env_setup.sh"
else
    echo "   source $SCRIPT_DIR/env_setup.sh"
fi
echo ""
echo "2. Run the demo with Qwen3.5-0.8B:"
echo "   cd $SCRIPT_DIR"
echo "   python3 third_party/mirage/demo/qwen3/demo.py --model Qwen/Qwen3.5-0.8B --use-mirage"
echo ""
print_info "Note: Qwen3.5-0.8B uses the same architecture as Qwen3, so it works with the existing Qwen3 builder."
echo ""
