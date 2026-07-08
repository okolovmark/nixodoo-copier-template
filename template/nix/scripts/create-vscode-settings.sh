# shellcheck shell=bash
# Generate .vscode/settings.json (no-op if it already exists).
set -e
VSCODE_DIR="$PWD/.vscode"
SETTINGS_FILE="$VSCODE_DIR/settings.json"
PYTHON_PATH="$HOME/.nix-profile/bin/python"
mkdir -p "$VSCODE_DIR"
if [ -f "$SETTINGS_FILE" ]; then
    echo "You already have existing VS Code settings, skipping..."
else
    echo "Creating new VS Code settings file..."
    cat > "$SETTINGS_FILE" << EOF
{
    "python.defaultInterpreterPath": "$PYTHON_PATH",
    "ty.importStrategy": "useBundled",
    "files.exclude": {
        "**/*.egg-info": true,
        ".ruff_cache": true,
        ".playwright-mcp": true,
        "result": true
    },
}

EOF
    echo "VS Code settings configured!"
fi
