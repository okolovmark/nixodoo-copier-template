# shellcheck shell=bash
# Create ./.venv - a plain, empty venv over the BASE interpreter, for editors
# that insist on building their own debug-adapter environment.
#
# Zed's debugpy support runs `<toolchain python> -m venv` and then installs
# debugpy with that venv's pip. It cannot do that with the project env: the nix
# env is itself a venv, and on CPython 3.10 a venv over a venv resolves its
# prefix back to the parent (the interpreter is a symlink, so python finds the
# parent's pyvenv.cfg first), which sends ensurepip into the read-only nix store
# and fails. A venv over the base interpreter has none of that.
#
# It deliberately holds nothing but pip: the debugged process runs on the
# project env (the launch config resolves `python` through PATH), and pinning
# the project env's site-packages in here would go stale on every rebuild.
# Requires DEV_PYTHON in the environment (injected by flake.nix).
set -e
VENV="$PWD/.venv"
BASE_PYTHON=$("$DEV_PYTHON" -c 'import sys; print(sys.base_prefix + "/bin/python3")')

if "$VENV/bin/pip" --version >/dev/null 2>&1; then
    echo "$VENV already usable ($("$VENV/bin/python" --version))."
    exit 0
fi

echo "Creating $VENV from $BASE_PYTHON..."
"$BASE_PYTHON" -m venv --clear "$VENV"
echo "Done. Point your editor's Python toolchain at $VENV/bin/python."
