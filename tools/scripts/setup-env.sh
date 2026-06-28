#!/usr/bin/env bash
set -e

# Define colors
BLUE='\033[1;34m'
GREEN='\033[1;32m'
NC='\033[0m' # No Color
INFO="${BLUE}ℹ${NC}"
OK="${GREEN}✓${NC}"

# Detect if running on internal Linux (Rodete)
if [ -f "/etc/os-release" ] && grep -q "rodete" /etc/os-release; then
    echo -e "${INFO} Detected internal environment (Rodete)."

    # Configure uv.toml with public PyPI registry to avoid proxy blocks
    if [ ! -f "uv.toml" ]; then
        echo -e "${INFO} Creating uv.toml to configure public PyPI registry..."
        cat <<EOF > uv.toml
[[index]]
name = "pypi"
url = "https://pypi.org/simple"
default = true
EOF
        echo -e "${OK} uv.toml created."
    else
        if ! grep -q "url = \"https://pypi.org/simple\"" uv.toml; then
             cat <<EOF >> uv.toml

[[index]]
name = "pypi"
url = "https://pypi.org/simple"
default = true
EOF
             echo -e "${OK} Appended public index to uv.toml."
        else
             echo -e "${INFO} uv.toml already configured with PyPI index. Skipping."
        fi
    fi
else
    echo -e "${INFO} Not running on Rodete. Skipping internal environment setup."
fi
