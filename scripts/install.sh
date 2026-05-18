#!/usr/bin/env bash
# windows-llm-bridge · install script
#
# Install the project into a user-local, isolated Python environment.
# Does NOT require root. Does NOT touch the system Python (/usr/bin/python3).
# Safe to run on a shared server — zero impact on other users.
#
# See scripts/uninstall.sh to undo everything.

set -euo pipefail

# Lock down PATH so the script always finds the same tools regardless of
# whatever the caller had in their environment. Important on shared servers
# where user PATHs may include pyenv / conda / etc.
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${HOME}/.local/bin:${HOME}/.cargo/bin"
export PYTHONNOUSERSITE=1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ─── Colors ──────────────────────────────────────────────────────
if [[ -t 1 ]]; then
    RED='\033[31m'; GREEN='\033[32m'; YELLOW='\033[33m'; BLUE='\033[34m'; DIM='\033[2m'; NC='\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; BLUE=''; DIM=''; NC=''
fi

log_info() { printf "${BLUE}[INFO]${NC} %s\n" "$*"; }
log_ok()   { printf "${GREEN}[ OK ]${NC} %s\n" "$*"; }
log_warn() { printf "${YELLOW}[WARN]${NC} %s\n" "$*"; }
log_err()  { printf "${RED}[FAIL]${NC} %s\n" "$*" >&2; }
indent()   { sed 's/^/       /'; }

# ─── Args ────────────────────────────────────────────────────────
MODIFY_PATH=0
SKIP_SMOKE=0
PYTHON_VERSION="3.11"

usage() {
    cat <<USAGE
Usage: $(basename "$0") [options]

Install windows-llm-bridge into a user-local Python 3.11+ environment.
Requires no root.

Options:
  --with-uv-in-path    Append ~/.local/bin to your ~/.bashrc (or ~/.zshrc).
                       Only affects YOUR user; other users are untouched.
  --skip-smoke-test    Skip post-install smoke tests.
  --python VERSION     Python version uv should install (default: 3.11).
  -h, --help           Show this help.

What this script does (nothing outside your home directory):
  1. Refuse to run as root.
  2. Install uv into ~/.local/bin (if not already present).
  3. Ask uv to fetch Python ${PYTHON_VERSION} into ~/.local/share/uv/python/.
  4. Run 'uv sync' in the project repo to create .venv/ with all deps.
  5. Run a pytest smoke test (tests/test_smoke.py).
  6. Print next-step hints.

Isolation guarantees:
  - System /usr/bin/python3 is NOT modified.
  - No sudo is ever invoked.
  - uv-managed Python lands in ~/.local/share/uv/python/ (user-only).
  - Project venv lands in ${REPO_ROOT}/.venv/ (project-only).
  - Other users on this machine are unaffected.
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --with-uv-in-path) MODIFY_PATH=1; shift ;;
        --skip-smoke-test) SKIP_SMOKE=1; shift ;;
        --python) PYTHON_VERSION="${2:?missing value for --python}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) log_err "unknown option: $1"; echo; usage; exit 1 ;;
    esac
done

# ─── Safety checks ───────────────────────────────────────────────
if [[ ${EUID} -eq 0 ]]; then
    log_err "Refusing to run as root. This installer is user-local by design."
    exit 1
fi

if [[ ! -f "${REPO_ROOT}/pyproject.toml" ]]; then
    log_err "pyproject.toml not found at ${REPO_ROOT}."
    log_err "Is ${SCRIPT_DIR} really a scripts/ dir inside the windows-llm-bridge repo?"
    exit 1
fi

log_info "Installing into: ${REPO_ROOT}"
log_info "User: $(id -un) (uid=${EUID})"
log_info "Target Python: ${PYTHON_VERSION}"
echo

# ─── uv ──────────────────────────────────────────────────────────
UV_BIN=""
if [[ -x "${HOME}/.local/bin/uv" ]]; then
    UV_BIN="${HOME}/.local/bin/uv"
elif command -v uv >/dev/null 2>&1; then
    UV_BIN="$(command -v uv)"
fi

if [[ -n "${UV_BIN}" ]]; then
    log_ok "uv already installed: ${UV_BIN} ($(${UV_BIN} --version))"
else
    log_info "Installing uv to ~/.local/bin (no shell rc changes)..."
    if command -v curl >/dev/null 2>&1; then
        curl -LsSf https://astral.sh/uv/install.sh | env INSTALLER_NO_MODIFY_PATH=1 sh 2>&1 | indent
    elif command -v wget >/dev/null 2>&1; then
        wget -qO- https://astral.sh/uv/install.sh | env INSTALLER_NO_MODIFY_PATH=1 sh 2>&1 | indent
    else
        log_err "Neither curl nor wget is available."
        log_err "Install one manually or grab uv from: https://github.com/astral-sh/uv/releases"
        exit 1
    fi
    UV_BIN="${HOME}/.local/bin/uv"
    [[ -x "${UV_BIN}" ]] || { log_err "uv installer did not produce ${UV_BIN}."; exit 1; }
    log_ok "uv installed: $(${UV_BIN} --version)"
fi

# ─── Optional PATH persistence ───────────────────────────────────
if [[ ${MODIFY_PATH} -eq 1 ]]; then
    RC_FILE="${HOME}/.bashrc"
    if [[ -n "${ZSH_VERSION:-}" || "${SHELL:-}" == */zsh ]]; then
        RC_FILE="${HOME}/.zshrc"
    fi
    LINE='export PATH="$HOME/.local/bin:$PATH"   # added by windows-llm-bridge installer'
    if grep -qF 'windows-llm-bridge installer' "${RC_FILE}" 2>/dev/null; then
        log_ok "PATH entry already present in ${RC_FILE}"
    else
        touch "${RC_FILE}"
        printf '\n%s\n' "${LINE}" >> "${RC_FILE}"
        log_ok "Appended PATH entry to ${RC_FILE}"
        log_info "Run 'source ${RC_FILE}' or re-open your shell to pick it up."
    fi
fi

# ─── Python ──────────────────────────────────────────────────────
log_info "Asking uv to ensure Python ${PYTHON_VERSION} is available..."
"${UV_BIN}" python install "${PYTHON_VERSION}" 2>&1 | indent || {
    log_err "uv python install failed. Network restricted?"
    exit 1
}
log_ok "Python ${PYTHON_VERSION} ready (managed by uv, under ~/.local/share/uv/python/)"

# ─── uv sync ─────────────────────────────────────────────────────
cd "${REPO_ROOT}"
log_info "Running 'uv sync' (creates .venv/ and installs all project dependencies)..."
"${UV_BIN}" sync 2>&1 | indent
[[ -d "${REPO_ROOT}/.venv" ]] || { log_err "uv sync finished but .venv/ was not created."; exit 1; }
log_ok ".venv/ ready at ${REPO_ROOT}/.venv"

# ─── Smoke test ──────────────────────────────────────────────────
if [[ ${SKIP_SMOKE} -eq 0 ]]; then
    log_info "Running smoke tests..."
    if "${UV_BIN}" run pytest -q tests/test_smoke.py 2>&1 | indent; then
        log_ok "Smoke tests passed"
    else
        log_warn "Smoke tests failed. Install completed but please review the output."
    fi
else
    log_info "Skipping smoke tests (--skip-smoke-test)"
fi

# ─── Summary ─────────────────────────────────────────────────────
echo
log_ok "windows-llm-bridge installed successfully."
echo
cat <<NEXT
${GREEN}Next steps:${NC}

  ${DIM}# Make sure ~/.local/bin is on your PATH (just for your shell):${NC}
  export PATH="\$HOME/.local/bin:\$PATH"

  ${DIM}# Configure the Windows target:${NC}
  cp ${REPO_ROOT}/.env.example ${REPO_ROOT}/.env
  \$EDITOR ${REPO_ROOT}/.env       # fill WLB_SSH_HOST / WLB_SSH_USER / WLB_SSH_KEY

  ${DIM}# Try it out:${NC}
  cd ${REPO_ROOT}
  uv run wlb describe              # list transports + capabilities
  uv run wlb status                # transport health

  ${DIM}# Use from Claude Code — add to ~/.claude/mcp-settings.json:${NC}
  {
    "mcpServers": {
      "wlb": {
        "command": "uv",
        "args": ["run", "--project", "${REPO_ROOT}", "wlb-mcp"]
      }
    }
  }

  ${DIM}# Windows-side setup (run on the Windows host as administrator):${NC}
  ${REPO_ROOT}/scripts/windows-setup/enable-openssh.ps1

${DIM}Docs:      ${REPO_ROOT}/docs/
Uninstall: ${REPO_ROOT}/scripts/uninstall.sh${NC}
NEXT
