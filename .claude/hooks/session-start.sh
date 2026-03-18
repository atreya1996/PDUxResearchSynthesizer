#!/bin/bash
set -euo pipefail

# Only run in remote Claude Code on the web sessions
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

echo "=== PDUxResearchSynthesizer: Session Start ==="

# Install Python dependencies
if [ -f "$CLAUDE_PROJECT_DIR/requirements.txt" ]; then
  echo "Installing Python dependencies..."
  pip install -r "$CLAUDE_PROJECT_DIR/requirements.txt" --quiet
  echo "Dependencies installed."
else
  echo "WARNING: requirements.txt not found."
fi

# Install linter and test runner if not already present
pip install flake8 pytest --quiet

# Install GitHub CLI (gh) so Claude can create PRs automatically
if ! command -v gh &>/dev/null; then
  echo "Installing GitHub CLI (gh) via binary release..."
  GH_VERSION="2.63.2"
  ARCH=$(dpkg --print-architecture 2>/dev/null || echo "amd64")
  GH_TAR="gh_${GH_VERSION}_linux_${ARCH}.tar.gz"
  curl -fsSL "https://github.com/cli/cli/releases/download/v${GH_VERSION}/${GH_TAR}" \
    -o /tmp/gh.tar.gz
  tar -xzf /tmp/gh.tar.gz -C /tmp
  install -m 755 "/tmp/gh_${GH_VERSION}_linux_${ARCH}/bin/gh" /usr/local/bin/gh
  rm -rf /tmp/gh.tar.gz "/tmp/gh_${GH_VERSION}_linux_${ARCH}"
  echo "GitHub CLI installed: $(gh --version | head -1)"
else
  echo "GitHub CLI already installed: $(gh --version | head -1)"
fi

# Warn if .env is missing
if [ ! -f "$CLAUDE_PROJECT_DIR/.env" ]; then
  echo "WARNING: .env file not found. Copy .env.example to .env and fill in your credentials."
fi

# Validate GOOGLE_SERVICE_ACCOUNT_JSON path if .env exists
if [ -f "$CLAUDE_PROJECT_DIR/.env" ]; then
  SA_PATH=$(grep -E '^GOOGLE_SERVICE_ACCOUNT_JSON=' "$CLAUDE_PROJECT_DIR/.env" | cut -d'=' -f2- | tr -d '"' | tr -d "'")
  if [ -n "$SA_PATH" ] && [ ! -f "$SA_PATH" ]; then
    echo "WARNING: GOOGLE_SERVICE_ACCOUNT_JSON path '$SA_PATH' does not exist."
  fi
fi

# Set PYTHONPATH so imports resolve from project root
echo "export PYTHONPATH=\"$CLAUDE_PROJECT_DIR\"" >> "$CLAUDE_ENV_FILE"

echo "=== Session start complete ==="
