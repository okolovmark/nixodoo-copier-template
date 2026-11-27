#!/usr/bin/env python3
"""Corpus for the import groups ruff's isort keeps: the order the style skill gives.

Run from the repo root: uv run --no-project --with jinja2 --with ruff==<rev> python tests/test_isort_groups.py
(<rev> = the ruff the template's pre-commit pins, which the first case checks).

The env repo's pyproject.toml took `odoo` as first-party and `odoo.addons` as local-folder, the
section a module's own relative imports go to, so ruff put `from .common import X` before
`from odoo.addons...`, against the style skill. Each case renders a config from the answers, lets
ruff sort a module that has every group out of order, and reads the order back.
"""

import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib

import jinja2

PYPROJECT_SRC = pathlib.Path("template/pyproject.toml.jinja")
STYLE_SRC = pathlib.Path("template/{% if use_agent_layer %}.claude{% endif %}/skills/style/SKILL.md.jinja")
PRE_COMMIT_SRC = pathlib.Path("template/.pre-commit-config.yaml")

ANSWERS = dict(project_name="p", odoo_version="20.0", odoo_major="20", python_version="3.14",
               module_prefix="acme", custom_repo_name="acme-addons")

SHUFFLED = """\
from .common import Helper
from odoo.addons.acme_sale.models import sale_order
from odoo import fields
import requests
from odoo.addons.base.tests.common import BaseCommon
import json
from odoo.tests import tagged
from ..tools import helper
"""

# the style skill's groups: stdlib, third-party, odoo, odoo.addons, this project's modules, relative
SORTED = """\
import json

import requests

from odoo import fields
from odoo.tests import tagged

from odoo.addons.base.tests.common import BaseCommon

from odoo.addons.acme_sale.models import sale_order

from ..tools import helper
from .common import Helper
"""


def render(src, **overrides):
    env = jinja2.Environment(trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True)
    return env.from_string(src.read_text()).render(**{**ANSWERS, **overrides})


def ruff_binary():
    return shutil.which("ruff") or sys.exit("ruff is not on PATH: uv run --with ruff==<the pin>, as CI does")


def sort_imports(config_name, config_text):
    """Return SHUFFLED as ruff's isort leaves it under the config `config_text`, saved as `config_name`."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / config_name).write_text(config_text)
        module = root / "acme_stock" / "tests" / "test_x.py"
        module.parent.mkdir(parents=True)
        module.write_text(SHUFFLED)
        run = subprocess.run([ruff_binary(), "check", "--config", str(root / config_name), "--select", "I", "--fix",
                              "--quiet", "--no-cache", str(module)], check=False, capture_output=True, text=True)
        # 1 is ruff's "issues left", which a sorted module has none of: the assertion on the text says so
        assert run.returncode in (0, 1), f"ruff exit {run.returncode}: {run.stderr}"
        return module.read_text()


CASES = []


def case(fn):
    CASES.append(fn)
    return fn


@case
def the_ruff_under_test_is_the_one_the_projects_pre_commit_pins():
    pinned = re.search(r"astral-sh/ruff-pre-commit\n\s+rev: v(\S+)", PRE_COMMIT_SRC.read_text()).group(1)
    version = subprocess.run([ruff_binary(), "--version"], check=True, capture_output=True, text=True).stdout.split()[1]
    assert version == pinned, f"ruff {version} under test, the template's pre-commit pins {pinned}: pin CI to it"


@case
def the_env_repos_pyproject_sorts_the_style_skills_groups():
    result = sort_imports("pyproject.toml", render(PYPROJECT_SRC))
    assert result == SORTED, result


@case
def the_ruff_toml_the_style_skill_gives_for_the_addons_repo_sorts_the_same():
    blocks = re.findall(r"```toml\n(.*?)```", render(STYLE_SRC), re.S)
    assert len(blocks) == 1, blocks
    snippet = "\n".join(line.removeprefix("  ") for line in blocks[0].splitlines())
    result = sort_imports("ruff.toml", snippet)
    assert result == SORTED, result


@case
def without_a_module_prefix_the_pyproject_has_no_project_group():
    sections = tomllib.loads(render(PYPROJECT_SRC, module_prefix=""))["tool"]["ruff"]["lint"]["isort"]
    assert sections.get("sections") == {"odoo": ["odoo"], "odoo-addons": ["odoo.addons"]}, sections
    assert sections.get("section-order") == ["future", "standard-library", "third-party", "odoo", "odoo-addons",
                                         "first-party", "local-folder"], sections


if __name__ == "__main__":
    failed = 0
    for fn in CASES:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL {fn.__name__}: {exc}")
    print("PASS" if not failed else f"{failed} FAILED")
    sys.exit(1 if failed else 0)
