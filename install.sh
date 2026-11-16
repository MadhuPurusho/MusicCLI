#!/usr/bin/env bash
set -e

echo "=== Installing Pulse CLI for macOS ==="

INSTALL_DIR="$HOME/.pulse-cli"
BIN_DIR="$HOME/.local/bin"

mkdir -p "$INSTALL_DIR"
mkdir -p "$BIN_DIR"

echo "Creating isolated virtual environment in $INSTALL_DIR..."
python3 -m venv "$INSTALL_DIR/venv"

echo "Installing dependencies..."
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install .

LAUNCHER="$BIN_DIR/pulse"
cat << EOF > "$LAUNCHER"
#!/usr/bin/env bash
exec "$INSTALL_DIR/venv/bin/python3" -m pulse "\$@"
EOF

chmod +x "$LAUNCHER"

echo ""
echo "=== Pulse CLI Installation Complete! ==="
echo "Binary linked at: $LAUNCHER"
echo ""
echo "Make sure $BIN_DIR is in your PATH by adding this to ~/.zshrc if not already present:"
echo '  export PATH="$HOME/.local/bin:$PATH"'
echo ""
echo "Run 'pulse' to launch the player!"
