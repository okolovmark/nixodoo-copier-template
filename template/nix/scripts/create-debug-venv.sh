# shellcheck shell=bash
# Create ./.venv - a plain venv over the BASE interpreter, for editors that
# insist on building their own debug-adapter environment.
#
# Zed's debugpy support runs `<toolchain python> -m venv` and then installs
# debugpy with that venv's pip. It cannot do that with the project env: the nix
# env is itself a venv, and on CPython 3.10 a venv over a venv resolves its
# prefix back to the parent (the interpreter is a symlink, so python finds the
# parent's pyvenv.cfg first), which sends ensurepip into the read-only nix store
# and fails. A venv over the base interpreter has none of that.
#
# The venv carries no packages of its own; devenv.pth points it at the project
# env's site-packages, so anything selecting it as a toolchain still sees every
# dependency. Re-run this after rebuilding the env - the store path changes.
# Requires DEV_PYTHON in the environment (injected by flake.nix).
set -e
VENV="$PWD/.venv"
BASE_PYTHON=$("$DEV_PYTHON" -c 'import sys; print(sys._base_executable)')
SITE_PACKAGES=$("$DEV_PYTHON" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')
PTH=$(echo "$VENV"/lib/python*/site-packages/devenv.pth)

if [ -f "$PTH" ] && [ "$(cat "$PTH")" = "$SITE_PACKAGES" ]; then
    echo "$VENV is up to date."
    exit 0
fi

echo "Creating $VENV from $BASE_PYTHON..."
"$BASE_PYTHON" -m venv --clear "$VENV"
echo "$SITE_PACKAGES" >"$(echo "$VENV"/lib/python*/site-packages)/devenv.pth"
echo "Done. Point your editor's Python toolchain at $VENV/bin/python."
