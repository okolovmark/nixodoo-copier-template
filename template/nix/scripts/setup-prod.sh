# shellcheck shell=bash
# Full production setup: env, repos, odoo/nginx configs, systemd services.
# The called scripts are on PATH (injected by flake.nix).
set -e
echo "=== Running create-env ==="
create-env
echo
echo "=== Running update-repos ==="
update-repos
echo
echo "=== Running create-odoo-config ==="
create-odoo-config
echo
echo "=== Running create-nginx-config ==="
create-nginx-config
echo
echo "=== Running create-systemd-service ==="
create-systemd-service
echo
echo "=== Prod setup complete! ==="
