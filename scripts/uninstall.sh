#!/usr/bin/env bash
# windows-llm-bridge · uninstall script
#
# Remove the project's local Python environment and optionally the runtime
# artifacts, uv, and uv-managed Pythons. Completely user-local. Never
# touches system Python.

set -euo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${HOME}/.local/bin"
export PYTHONNOUSERSITE=1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ -t 1 ]]; then
    RED='\033[31m'; GREEN='\033[32m'; YELLOW='\033[33m'; BLUE='\033[34m'; DIM='\033[2m'; NC='\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; BLUE=''; DIM=''; NC=''
fi

log_info() { printf "${BLUE}[INFO]${NC} %s\n" "$*"; }
log_ok()   { printf "${GREEN}[ OK ]${NC} %s\n" "$*"; }
log_warn() { printf "${YELLOW}[WARN]${NC} %s\n" "$*"; }
log_err()  { printf "${RED}[FAIL]${NC} %s\n" "$*" >&2; }

FORCE=0
PURGE_WORKSPACE=0
REMOVE_UV=0
REMOVE_UV_PYTHON=0

usage() {
    cat <<USAGE
Usage: $(basename "$0") [options]

Uninstall windows-llm-bridge's local artifacts. Does NOT require root.

By default this script removes ONLY:
  - ${REPO_ROOT}/.venv/
  - Python caches (__pycache__, .mypy_cache, .ruff_cache, .pytest_cache, htmlcov)

Options:
  --force              Skip interactive confirmation.
  --purge-workspace    Also delete ${REPO_ROOT}/workspace/* (kept .gitkeep).
                       ${YELLOW}⚠ Removes captured logs, pulled files, tool runs.${NC}
  --remove-uv          Delete ~/.local/bin/uv (and uvx).
                       ${YELLOW}⚠ Only if no other project on your account uses uv.${NC}
  --remove-uv-python   Delete ~/.local/share/uv/ entirely (uv cache + all
                       uv-managed Python installations).
                       ${YELLOW}⚠ Only if you won't use uv again.${NC}
  -h, --help           Show this help.

What this script NEVER touches:
  - System Python (/usr/bin/python3)
  - ~/.bashrc / ~/.zshrc — reported but not auto-removed
  - /etc, /usr, /opt — no root, no system changes
  - Your project source code and .git history
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --force) FORCE=1; shift ;;
        --purge-workspace) PURGE_WORKSPACE=1; shift ;;
        --remove-uv) REMOVE_UV=1; shift ;;
        --remove-uv-python) REMOVE_UV_PYTHON=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) log_err "unknown option: $1"; echo; usage; exit 1 ;;
    esac
done

if [[ ${EUID} -eq 0 ]]; then
    log_err "Refusing to run as root."
    exit 1
fi
[[ -f "${REPO_ROOT}/pyproject.toml" ]] || { log_err "pyproject.toml not found at ${REPO_ROOT}"; exit 1; }

if [[ ${FORCE} -eq 0 ]]; then
    echo "About to uninstall from: ${REPO_ROOT}"
    echo
    echo "Will delete:"
    [[ -d "${REPO_ROOT}/.venv" ]] && echo "  • ${REPO_ROOT}/.venv/"
    echo "  • Python caches under ${REPO_ROOT}"
    [[ ${PURGE_WORKSPACE} -eq 1 && -d "${REPO_ROOT}/workspace" ]] && \
        echo "  • ${REPO_ROOT}/workspace/* (${RED}PURGE — captured artifacts${NC})"
    [[ ${REMOVE_UV} -eq 1 && -f "${HOME}/.local/bin/uv" ]] && \
        echo "  • ~/.local/bin/uv and ~/.local/bin/uvx"
    [[ ${REMOVE_UV_PYTHON} -eq 1 && -d "${HOME}/.local/share/uv" ]] && \
        echo "  • ~/.local/share/uv/ (${RED}ALL uv-managed Pythons${NC})"
    echo
    read -rp "Continue? [y/N] " ans
    [[ "${ans}" =~ ^[Yy]$ ]] || { log_info "Aborted."; exit 0; }
fi

if [[ -d "${REPO_ROOT}/.venv" ]]; then
    rm -rf "${REPO_ROOT}/.venv"
    log_ok "Removed ${REPO_ROOT}/.venv/"
fi

for cache in .mypy_cache .ruff_cache .pytest_cache htmlcov coverage.xml .coverage .uv_cache; do
    if [[ -e "${REPO_ROOT}/${cache}" ]]; then
        rm -rf "${REPO_ROOT}/${cache}"
        log_ok "Removed ${cache}"
    fi
done
if find "${REPO_ROOT}/src" "${REPO_ROOT}/tests" -type d -name __pycache__ 2>/dev/null | grep -q .; then
    find "${REPO_ROOT}/src" "${REPO_ROOT}/tests" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
    log_ok "Removed __pycache__/ directories"
fi

if [[ ${PURGE_WORKSPACE} -eq 1 && -d "${REPO_ROOT}/workspace" ]]; then
    find "${REPO_ROOT}/workspace" -mindepth 1 -not -name '.gitkeep' -print0 2>/dev/null \
        | xargs -0 rm -rf 2>/dev/null || true
    log_ok "Purged workspace/ (kept .gitkeep)"
fi

if [[ ${REMOVE_UV} -eq 1 ]]; then
    for bin in uv uvx; do
        if [[ -f "${HOME}/.local/bin/${bin}" ]]; then
            rm -f "${HOME}/.local/bin/${bin}"
            log_ok "Removed ~/.local/bin/${bin}"
        fi
    done
fi

if [[ ${REMOVE_UV_PYTHON} -eq 1 ]]; then
    [[ -d "${HOME}/.local/share/uv" ]] && { rm -rf "${HOME}/.local/share/uv"; log_ok "Removed ~/.local/share/uv/"; }
    [[ -d "${HOME}/.cache/uv" ]] && { rm -rf "${HOME}/.cache/uv"; log_ok "Removed ~/.cache/uv/"; }
fi

for rc in "${HOME}/.bashrc" "${HOME}/.zshrc"; do
    if [[ -f "${rc}" ]] && grep -q 'windows-llm-bridge installer' "${rc}" 2>/dev/null; then
        log_warn "${rc} still contains a PATH line added by the installer."
        log_warn "Remove it manually if you no longer need ~/.local/bin on PATH."
    fi
done

echo
log_ok "Uninstall complete."
