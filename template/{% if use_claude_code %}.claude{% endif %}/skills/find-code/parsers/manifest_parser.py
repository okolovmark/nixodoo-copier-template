"""
Parser for Odoo __manifest__.py files.
Extracts module dependencies and metadata.
"""

import ast
from pathlib import Path
from typing import Dict, List, Optional


class ManifestParser:
    """Parses __manifest__.py files to extract module metadata."""

    def __init__(self, addon_paths: List[Path]):
        """
        Initialize parser with addon directories.

        Args:
            addon_paths: List of paths to addon directories
        """
        self.addon_paths = [Path(p) for p in addon_paths]
        self._cache: Dict[str, Dict] = {}

    def find_module_path(self, module_name: str) -> Optional[Path]:
        """
        Find module directory by name.

        Args:
            module_name: Name of the module (e.g., 'sale', )

        Returns:
            Path to module directory or None if not found
        """
        for addon_path in self.addon_paths:
            module_path = addon_path / module_name
            if module_path.exists() and module_path.is_dir():
                manifest_path = module_path / '__manifest__.py'
                if manifest_path.exists():
                    return module_path
        return None

    def parse_manifest(self, module_path: Path) -> Dict:
        """
        Parse __manifest__.py file.

        Args:
            module_path: Path to module directory

        Returns:
            Dictionary with manifest data
        """
        manifest_path = module_path / '__manifest__.py'

        if not manifest_path.exists():
            return {}

        # Check cache
        cache_key = str(manifest_path)
        if cache_key in self._cache:
            return self._cache[cache_key]

        try:
            # Read and parse as Python dict
            with open(manifest_path, 'r', encoding='utf-8') as f:
                content = f.read()

            # Parse AST and extract dict
            tree = ast.parse(content)

            # Find dictionary assignment
            manifest_data = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.Dict):
                    manifest_data = self._extract_dict(node)
                    break

            # Cache result
            self._cache[cache_key] = manifest_data
            return manifest_data

        except Exception as e:
            print(f"Error parsing manifest {manifest_path}: {e}")
            return {}

    def _extract_dict(self, node: ast.Dict) -> Dict:
        """
        Extract dictionary from AST node.

        Args:
            node: AST Dict node

        Returns:
            Dictionary with extracted values
        """
        result = {}

        for key, value in zip(node.keys, node.values):
            if key is None:
                continue

            # Get key name
            if isinstance(key, ast.Constant):
                key_name = key.value
            elif isinstance(key, ast.Str):  # Python < 3.8
                key_name = key.s
            else:
                continue

            # Extract value
            result[key_name] = self._extract_value(value)

        return result

    def _extract_value(self, node):
        """
        Extract value from AST node.

        Args:
            node: AST node

        Returns:
            Extracted Python value
        """
        if isinstance(node, ast.Constant):
            return node.value
        elif isinstance(node, ast.Str):  # Python < 3.8
            return node.s
        elif isinstance(node, ast.Num):  # Python < 3.8
            return node.n
        elif isinstance(node, ast.List):
            return [self._extract_value(item) for item in node.elts]
        elif isinstance(node, ast.Dict):
            return self._extract_dict(node)
        elif isinstance(node, ast.NameConstant):  # Python < 3.8
            return node.value
        else:
            return None

    def get_dependencies(self, module_name: str) -> List[str]:
        """
        Get module dependencies from manifest.

        Args:
            module_name: Name of the module

        Returns:
            List of dependency module names
        """
        module_path = self.find_module_path(module_name)
        if not module_path:
            return []

        manifest = self.parse_manifest(module_path)
        depends = manifest.get('depends', [])

        # Filter out None values and ensure strings
        return [dep for dep in depends if dep]

    def get_all_dependencies_recursive(self, module_name: str, visited: Optional[set] = None) -> List[str]:
        """
        Get all dependencies recursively.

        Args:
            module_name: Name of the module
            visited: Set of already visited modules (for circular dependency detection)

        Returns:
            List of all dependency module names (including transitive)
        """
        if visited is None:
            visited = set()

        if module_name in visited:
            return []

        visited.add(module_name)

        direct_deps = self.get_dependencies(module_name)
        all_deps = list(direct_deps)

        for dep in direct_deps:
            transitive_deps = self.get_all_dependencies_recursive(dep, visited)
            for td in transitive_deps:
                if td not in all_deps:
                    all_deps.append(td)

        return all_deps

    def get_module_info(self, module_name: str) -> Dict:
        """
        Get module metadata from manifest.

        Args:
            module_name: Name of the module

        Returns:
            Dictionary with module info (name, version, depends, etc.)
        """
        module_path = self.find_module_path(module_name)
        if not module_path:
            return {}

        manifest = self.parse_manifest(module_path)

        return {
            'name': manifest.get('name', module_name),
            'version': manifest.get('version', 'unknown'),
            'depends': manifest.get('depends', []),
            'path': str(module_path)
        }
