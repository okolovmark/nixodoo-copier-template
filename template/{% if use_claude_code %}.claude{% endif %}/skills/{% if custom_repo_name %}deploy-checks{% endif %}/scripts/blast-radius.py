#!/usr/bin/env python3
"""Blast-radius classifier for Odoo addons changes.

Tags a change set by how much damage it can do on a live database:
  high   -- code that creates/mutates business documents (journal entries,
            stock moves, payslips), raw SQL writes, stored-field definitions
            (schema change + recompute on update), migration scripts
  medium -- other server-side .py, security rules, data records, manifests
  low    -- views, reports, static assets, docs, tests, translations

Usage (content-aware, preferred -- scans the changed lines):
  blast-radius.py --repo <addons-repo> --range <base>..<head>
Usage (path-only, e.g. from a PR file list, one path per line):
  blast-radius.py --files-from - < changed_files.txt

Extra high-risk models: env BLAST_HIGH_MODELS=my.model,other.model

Output: one "MODULE <name>: <tier> -- <reasons>" line per touched module,
then "BLAST_RADIUS=<high|medium|low>" (max across modules) for scripting.
Always exits 0; parse the last line.
"""
import argparse
import os
import re
import subprocess
import sys
from collections import defaultdict

TIERS = {"low": 0, "medium": 1, "high": 2}

HIGH_MODELS = {
    "account.move", "account.move.line", "account.payment",
    "account.partial.reconcile", "account.bank.statement",
    "stock.move", "stock.move.line", "stock.quant", "stock.valuation.layer",
    "hr.payslip", "hr.payslip.line", "hr.payslip.run",
}
HIGH_MODELS |= {
    m.strip() for m in os.environ.get("BLAST_HIGH_MODELS", "").split(",") if m.strip()
}

MODEL_RE = re.compile(r"['\"](%s)['\"]" % "|".join(re.escape(m) for m in sorted(HIGH_MODELS)))
SQL_EXEC_RE = re.compile(r"\bcr\.execute\s*\(|\.env\.cr\.execute\s*\(")
SQL_WRITE_KW_RE = re.compile(r"\b(INSERT|UPDATE|DELETE|ALTER|TRUNCATE)\b", re.I)
STORED_FIELD_RE = re.compile(r"\bstore\s*=\s*True\b")

LOW_DIR_RE = re.compile(r"^(views|report|reports|static|readme|demo|doc|docs|tests|i18n)/")
LOW_FILE_RE = re.compile(r"(^README\.(rst|md)$|\.pot?$|\.md$|\.rst$|\.html$|\.scss$|\.css$|\.js$)")


def classify_path(relpath):
    """Tier from the path alone. Returns (tier, reason) or None for ignorable."""
    if "/migrations/" in "/" + relpath or relpath.startswith("migrations/"):
        return "high", "migration script"
    if LOW_DIR_RE.match(relpath) or LOW_FILE_RE.search(os.path.basename(relpath)):
        return "low", None
    if relpath.startswith("security/"):
        return "medium", "security change"
    if relpath.startswith("data/"):
        return "medium", "data records"
    if os.path.basename(relpath) == "__manifest__.py":
        return "medium", "manifest change"
    if relpath.endswith(".py"):
        return "medium", "server-side code"
    if relpath.endswith(".xml"):
        return "medium", "xml outside views/ (may be data)"
    return "low", None


def scan_changed_lines(lines):
    """Content markers that raise a .py file to high. Returns list of reasons."""
    reasons = set()
    for line in lines:
        if MODEL_RE.search(line):
            reasons.add("touches %s" % ", ".join(sorted(set(MODEL_RE.findall(line)))))
        if STORED_FIELD_RE.search(line):
            reasons.add("stored field definition changed (schema/recompute on update)")
        if SQL_EXEC_RE.search(line) and SQL_WRITE_KW_RE.search(line):
            reasons.add("raw SQL write")
    return sorted(reasons)


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", repo] + list(args),
        check=True, capture_output=True, text=True,
    ).stdout


def changed_lines(repo, range_, path):
    """Added/removed lines of one file in the range (diff hunk content)."""
    out = git(repo, "diff", "-U0", range_, "--", path)
    lines = []
    for ln in out.splitlines():
        if ln.startswith(("+++", "---")):
            continue
        if ln.startswith(("+", "-")):
            lines.append(ln[1:])
    return lines


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", help="addons git repo (enables content scan)")
    ap.add_argument("--range", dest="range_", help="git range, e.g. origin/16.0..HEAD")
    ap.add_argument("--files-from", help="file with changed paths, '-' for stdin")
    args = ap.parse_args()

    if args.repo and args.range_:
        files = [f for f in git(args.repo, "diff", "--name-only", args.range_).splitlines() if f]
        content_mode = True
    elif args.files_from:
        src = sys.stdin if args.files_from == "-" else open(args.files_from)
        files = [ln.strip() for ln in src if ln.strip()]
        content_mode = False
    else:
        ap.error("need --repo + --range, or --files-from")

    modules = defaultdict(lambda: {"tier": "low", "reasons": set()})
    for path in files:
        parts = path.split("/", 1)
        if len(parts) < 2:
            continue  # repo-root file (CI config, README) -- not a module
        module, relpath = parts
        if content_mode and not os.path.exists(os.path.join(args.repo, module, "__manifest__.py")):
            continue  # not an addon dir
        tier, reason = classify_path(relpath)
        entry = modules[module]
        if reason:
            entry["reasons"].add(reason)
        if TIERS[tier] > TIERS[entry["tier"]]:
            entry["tier"] = tier
        if content_mode and relpath.endswith(".py") and "tests/" not in relpath:
            for r in scan_changed_lines(changed_lines(args.repo, args.range_, path)):
                entry["reasons"].add(r)
                entry["tier"] = "high"

    if not modules:
        print("BLAST_RADIUS=low")
        return
    overall = "low"
    for module in sorted(modules):
        entry = modules[module]
        reasons = "; ".join(sorted(entry["reasons"])) or "cosmetic surfaces only"
        if not content_mode:
            reasons += " (path-only scan)"
        print("MODULE %s: %s -- %s" % (module, entry["tier"], reasons))
        if TIERS[entry["tier"]] > TIERS[overall]:
            overall = entry["tier"]
    print("BLAST_RADIUS=%s" % overall)


if __name__ == "__main__":
    main()
