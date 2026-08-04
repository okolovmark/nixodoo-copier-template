# shellcheck shell=bash
# Create ./.venv - a plain venv over the BASE interpreter, for editors that
# insist on building their own debug-adapter environment (Zed does).
#
# Zed's debugpy support runs `<toolchain python> -m venv` and then installs
# debugpy with pip. It cannot do that with the project env: the nix env is
# itself a venv, and on CPython a venv over a venv resolves its prefix back to
# the parent (the interpreter is a symlink, so python finds the parent's
# pyvenv.cfg first), which sends ensurepip into the read-only nix store and
# fails with "Failed to create base virtual environment". A venv over the base
# interpreter has none of that. Select ./.venv as the editor's Python toolchain.
#
# The venv stays empty (pip only): Zed also puts the selected toolchain first
# in PATH when launching the debuggee, so the launch config pins the debugged
# process to ./.venv/bin/dev-python instead - a symlink to the nix profile's
# python, which is the full project env and stays current across env rebuilds
# (the profile path is stable, store paths are not). The debuggee needs no
# debugpy: the adapter injects its own into the process it launches.
# Requires PROJECT_PYTHON in the environment (injected by flake.nix).
set -e
VENV="$PWD/.venv"
BASE_PYTHON=$("$PROJECT_PYTHON" -c 'import sys; print(sys.base_prefix + "/bin/python3")')
PROFILE_PYTHON="$HOME/.nix-profile/bin/python"

if ! "$VENV/bin/pip" --version >/dev/null 2>&1; then
    echo "Creating $VENV from $BASE_PYTHON..."
    "$BASE_PYTHON" -m venv --clear "$VENV"
fi
ln -sfn "$PROFILE_PYTHON" "$VENV/bin/dev-python"
echo "$VENV ready. Select it as the editor's Python toolchain;"
echo "the debug config launches the server via $VENV/bin/dev-python."
