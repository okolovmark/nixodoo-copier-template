-- Dev fixups for a freshly restored prod copy. NOT neutralization — that is
-- `odoo neutralize` (runs every installed module's data/neutralize.sql), which
-- the tooling executes first. This adds what neutralization must not:
-- local admin/admin credentials and requeueing stuck queue jobs so they run
-- against this copy.
-- Shared by `nix run .#download-backup` and worktree-env's wt-restore.sh.
-- Run WITHOUT ON_ERROR_STOP: a statement whose table is missing from the
-- dump errors and is skipped, the rest still apply.
UPDATE res_users SET login='admin', password='$pbkdf2-sha512$25000$zRkjhPD.P0fo3VsrRag1hg$Ybluv8VT4rorEdlO3H88tQ/Yz9s.kZYEhWnIFybVNRTq4VlD6ZrTcn2TXI7R7bdT26SLC4QtIu5njrS9PL96BA', active=true WHERE id=2;
UPDATE queue_job SET state='pending' WHERE state IN ('started','enqueued');
