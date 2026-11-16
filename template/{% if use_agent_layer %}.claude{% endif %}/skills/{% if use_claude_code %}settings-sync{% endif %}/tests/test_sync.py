"""sync.py against throwaway home, checkout and layers: python3 -m unittest discover -s <this folder>."""

import builtins
import importlib.util
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location('sync', Path(__file__).resolve().parents[1] / 'sync.py')
sync = importlib.util.module_from_spec(SPEC)
# dataclasses look their module up while the class is made
sys.modules['sync'] = sync
SPEC.loader.exec_module(sync)

ID = 'f00dcafe-0000-4000-8000-000000000001'
SHOP = {'SHOP': ['read', 'status']}


def slug_of(path):
    # as Claude Code names a project's folder under ~/.claude/projects: every other character a dash
    return re.sub(r'[^A-Za-z0-9]', '-', str(path))


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.template = self.tmp / 'template-layer'
        self.template.mkdir()
        self.place = sync.Place(
            home=self.tmp / 'home' / 'ann', project=self.tmp / 'home' / 'ann' / 'work' / 'erp', template_layer=self.template
        )
        self.place.project.mkdir(parents=True)
        self.place.project_layer.mkdir(parents=True)

    def store(self, layer, name, data):
        (layer / name).write_text(json.dumps(data))

    def mine(self, name, data):
        target = self.place.user_file if name == 'user' else self.place.local_file
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data))

    def run_apply(self, connectors=None, dry_run=False):
        found = connectors if connectors is not None else {'SHOP': [ID]}
        return sync.apply(self.place, found, dry_run, 'stamp')

    def main(self, *argv):
        """sync.main on the throwaway place; what it printed."""
        printed = []
        self.addCleanup(setattr, sync, 'HERE', sync.HERE)
        sync.HERE = self.tmp / 'skill'
        (sync.HERE).mkdir(exist_ok=True)
        if not (sync.HERE / 'baseline').exists():
            (sync.HERE / 'baseline').symlink_to(self.template)
        original = builtins.print
        builtins.print = lambda *args, **kwargs: printed.append(' '.join(map(str, args)))
        try:
            code = sync.main([*argv, '--home', str(self.place.home), '--project', str(self.place.project)])
        finally:
            builtins.print = original
        return code, printed

    def test_a_fresh_machine_gets_both_layers_with_its_own_paths(self):
        self.store(self.template, 'user.json', {
            'theme': 'dark',
            'hooks': {'Notification': [{'hooks': [{'type': 'command', 'command': 'bash @HOME@/.claude/hooks/n.sh'}]}]},
        })
        self.store(self.place.project_layer, 'user.json', {'permissions': {'allow': ['mcp__@SHOP@__read', 'Read(/@HOME@/x/**)']}})
        self.store(self.place.project_layer, 'connectors.json', SHOP)
        self.store(self.template, 'local.json', {'permissions': {'additionalDirectories': ['@HOME@/.claude/projects/@PROJECT_SLUG@/memory']}})
        (self.template / 'files' / '.claude' / 'hooks').mkdir(parents=True)
        (self.template / 'files' / '.claude' / 'hooks' / 'n.sh').write_text('echo hi\n')

        self.run_apply()

        home = str(self.place.home)
        user = json.loads(self.place.user_file.read_text())
        self.assertEqual(user['theme'], 'dark')
        self.assertEqual(user['permissions']['allow'], [f'mcp__{ID}__read', f'Read(/{home}/x/**)'])
        self.assertEqual(user['hooks']['Notification'][0]['hooks'][0]['command'], f'bash {home}/.claude/hooks/n.sh')
        local = json.loads(self.place.local_file.read_text())
        self.assertEqual(local['permissions']['additionalDirectories'], [f'{home}/.claude/projects/{slug_of(self.place.project)}/memory'])
        script = self.place.home / '.claude' / 'hooks' / 'n.sh'
        self.assertEqual(script.read_text(), 'echo hi\n')
        self.assertTrue(os.access(script, os.X_OK))

    def test_the_project_layer_overrides_the_template_layer(self):
        self.store(self.template, 'user.json', {'model': 'opus', 'permissions': {'allow': ['A']}})
        self.store(self.place.project_layer, 'user.json', {'model': 'sonnet', 'permissions': {'allow': ['B']}})

        self.assertEqual(sync.layered('user.json', self.place), {'model': 'sonnet', 'permissions': {'allow': ['B', 'A']}})

    def test_what_you_have_stays_and_only_the_missing_is_added(self):
        self.store(self.template, 'user.json', {'theme': 'dark', 'model': 'opus', 'permissions': {'allow': ['A', 'B']}})
        self.mine('user', {'theme': 'light', 'permissions': {'allow': ['B', 'C']}})

        lines = self.run_apply()

        user = json.loads(self.place.user_file.read_text())
        self.assertEqual(user, {'theme': 'light', 'permissions': {'allow': ['B', 'C', 'A']}, 'model': 'opus'})
        self.assertIn('  = theme: yours "light", baseline "dark"', lines)
        self.assertIn('  + model = "opus"', lines)
        self.assertIn('  ! permissions.allow += "A"', lines)
        backups = list(self.place.user_file.parent.glob('settings.json.bak-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text())['theme'], 'light')

    def test_nothing_missing_writes_nothing(self):
        self.store(self.template, 'user.json', {'theme': 'dark'})
        self.mine('user', {'theme': 'dark'})

        self.run_apply()

        self.assertEqual(list(self.place.user_file.parent.glob('*.bak-*')), [])

    def test_a_connector_not_in_the_session_is_skipped(self):
        self.store(self.place.project_layer, 'user.json', {'permissions': {'allow': ['mcp__@SHOP@__read', 'WebSearch']}})
        self.store(self.place.project_layer, 'connectors.json', SHOP)

        lines = self.run_apply(connectors={'SHOP': []})

        self.assertEqual(json.loads(self.place.user_file.read_text())['permissions']['allow'], ['WebSearch'])
        self.assertTrue(any('its connector is not in this session' in line for line in lines))

    def test_files_land_under_hooks_only(self):
        for relative in ('.claude/hooks/ok.sh', '.bashrc'):
            (self.template / 'files' / relative).parent.mkdir(parents=True, exist_ok=True)
            (self.template / 'files' / relative).write_text('echo x\n')

        lines = self.run_apply()

        self.assertTrue(any(line.startswith('  ! restored ~/.claude/hooks/ok.sh (1 lines, sha256 ') for line in lines))
        self.assertIn('refused ~/.bashrc: a layer restores files under ~/.claude/hooks/ only', lines)
        self.assertFalse((self.place.home / '.bashrc').exists())

    def test_check_writes_nothing_and_points_to_apply(self):
        self.store(self.template, 'user.json', {'theme': 'dark'})

        code, printed = self.main('check')

        self.assertEqual(code, 0)
        self.assertFalse(self.place.user_file.exists())
        self.assertIn('team settings (check: nothing written)', printed)
        self.assertTrue(any('`/settings-sync apply`' in line for line in printed))

    def test_connectors_lists_what_the_layers_declare(self):
        self.store(self.template, 'connectors.json', {'MAIL': ['send']})
        self.store(self.place.project_layer, 'connectors.json', SHOP)

        code, printed = self.main('connectors')

        self.assertEqual(code, 0)
        self.assertEqual(json.loads(printed[0]), {'MAIL': ['send'], 'SHOP': ['read', 'status']})

    def test_save_writes_your_settings_less_the_template_layer(self):
        home = self.place.home
        self.store(self.template, 'user.json', {'theme': 'dark', 'permissions': {'allow': ['WebSearch']}})
        self.store(self.place.project_layer, 'connectors.json', SHOP)
        self.mine('user', {
            'theme': 'dark',
            'model': 'sonnet',
            'permissions': {'allow': ['WebSearch', f'mcp__{ID}__read', f'Read(/{home}/x/**)', 'Bash(curl -H "Authorization: token ghp_' + 'a' * 30 + '")']},
            'env': {'API_TOKEN': 'x', 'DIRS': '~/a'},
        })

        lines = sync.save(self.place, {'SHOP': [ID]})

        layer = json.loads((self.place.project_layer / 'user.json').read_text())
        self.assertEqual(layer, {
            'model': 'sonnet',
            'permissions': {'allow': ['mcp__@SHOP@__read', 'Read(/@HOME@/x/**)']},
            'env': {'DIRS': '~/a'},
        })
        self.assertIn('left out env.API_TOKEN: named like a secret', lines)
        self.assertIn('left out an item of permissions.allow: it looks like a secret', lines)

    def test_save_carries_hook_scripts_the_template_does_not_give(self):
        home = self.place.home
        (home / '.claude' / 'hooks').mkdir(parents=True)
        for name, text in (('given.sh', 'echo same\n'), ('own.sh', 'echo own\n'), ('leaky.sh', 'curl -H "Authorization: Bearer ' + 'c' * 30 + '"\n')):
            (home / '.claude' / 'hooks' / name).write_text(text)
        (self.template / 'files' / '.claude' / 'hooks').mkdir(parents=True)
        (self.template / 'files' / '.claude' / 'hooks' / 'given.sh').write_text('echo same\n')
        (home / '.ssh').mkdir()
        (home / '.ssh' / 'id').write_text('key\n')
        commands = [f'bash {home}/.claude/hooks/{name}' for name in ('given.sh', 'own.sh', 'leaky.sh')] + [f'cat {home}/.claude/../.ssh/id']
        self.mine('user', {'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': c} for c in commands]}]}})

        lines = sync.save(self.place, {})

        files = self.place.project_layer / 'files'
        self.assertEqual(sorted(str(p.relative_to(files)) for p in files.rglob('*') if p.is_file()), ['.claude/hooks/own.sh'])
        self.assertIn('not saved ~/.claude/hooks/leaky.sh: it holds something that looks like a secret', lines)
        self.assertIn('not saved ~/.claude/../.ssh/id: a layer carries files under ~/.claude/hooks/ only', lines)

    def test_only_the_maintainer_saves_and_the_first_save_names_one(self):
        self.mine('user', {'theme': 'light'})
        self.addCleanup(setattr, sync, 'git_email', sync.git_email)

        sync.git_email = lambda project: 'maintainer@example.com'
        code, printed = self.main('save')
        self.assertEqual(code, 0)
        self.assertEqual((self.place.project_layer / 'maintainer').read_text(), 'maintainer@example.com\n')

        sync.git_email = lambda project: 'ann@example.com'
        self.mine('user', {'theme': 'pink'})
        code, printed = self.main('save')
        self.assertEqual(code, 2)
        self.assertIn('maintainer@example.com, not ann@example.com', printed[0])
        self.assertEqual(json.loads((self.place.project_layer / 'user.json').read_text()), {'theme': 'light'})

    def test_save_then_apply_elsewhere_gives_the_same_settings(self):
        home = str(self.place.home)
        self.store(self.template, 'user.json', {'theme': 'dark'})
        self.store(self.place.project_layer, 'connectors.json', SHOP)
        mine = {
            'theme': 'dark',
            'permissions': {'allow': [f'mcp__{ID}__read', f'Read(/{home}/x/**)'], 'defaultMode': 'auto'},
            'autoMode': {'environment': [f'**Trusted repo**: {self.place.project} (working directory)']},
        }
        self.mine('user', mine)
        self.mine('local', {'permissions': {'additionalDirectories': [f'{home}/.claude/projects/{slug_of(self.place.project)}/memory']}})
        sync.save(self.place, {'SHOP': [ID]})
        stored = (self.place.project_layer / 'user.json').read_text() + (self.place.project_layer / 'local.json').read_text()
        self.assertNotIn(home, stored)
        self.assertNotIn(ID, stored)

        laptop = sync.Place(home=self.tmp / 'Users' / 'ann', project=self.tmp / 'Users' / 'ann' / 'code' / 'erp', template_layer=self.template)
        laptop.project.mkdir(parents=True)
        laptop.project_layer.parent.mkdir(parents=True)
        os.symlink(self.place.project_layer, laptop.project_layer)
        sync.apply(laptop, {'SHOP': ['other-id']}, False, 'stamp')
        moved = json.loads(laptop.user_file.read_text())
        self.assertEqual(moved['theme'], 'dark')
        self.assertEqual(moved['permissions']['allow'], ['mcp__other-id__read', f'Read(/{laptop.home}/x/**)'])
        self.assertEqual(moved['autoMode']['environment'], [f'**Trusted repo**: {laptop.project} (working directory)'])
        self.assertEqual(
            json.loads(laptop.local_file.read_text())['permissions']['additionalDirectories'],
            [f'{laptop.home}/.claude/projects/{slug_of(laptop.project)}/memory'],
        )


if __name__ == '__main__':
    unittest.main()
