"""
Dependency resolver for Odoo modules.
Builds inheritance chain for models.
"""

import sys
from pathlib import Path
from typing import Dict, List, Optional

from .manifest_parser import ManifestParser
from .model_parser import ModelParser, ModelInfo


class InheritanceNode:
    """Node in the inheritance chain."""

    def __init__(self, model_info: ModelInfo, order: int):
        self.model_info = model_info
        self.order = order
        self.depends_on: List[str] = []

    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        data = self.model_info.to_dict()
        data['order'] = self.order
        data['depends_on'] = self.depends_on
        return data


class DependencyResolver:
    """Resolves module and model dependencies."""

    def __init__(self, addon_paths: List[Path]):
        """
        Initialize resolver.

        Args:
            addon_paths: List of paths to addon directories
        """
        self.addon_paths = addon_paths
        self.manifest_parser = ManifestParser(addon_paths)
        self.model_parser = ModelParser()

    def build_inheritance_chain(self, model_name: str, context_module: Optional[str] = None) -> List[InheritanceNode]:
        """
        Build complete inheritance chain for a model.

        Args:
            model_name: Name of the model (e.g., 'sale.order')
            context_module: Optional - analyze from this module's perspective

        Returns:
            List of InheritanceNode objects in dependency order
        """
        # Step 1: Find all modules that define or extend this model
        modules_with_model = self._find_modules_with_model(model_name, context_module)

        if not modules_with_model:
            return []

        # Step 2: Find base module (the one with is_base=True)
        base_module = None
        for module_name, model_info in modules_with_model.items():
            if model_info.is_base:
                base_module = module_name
                break

        # Step 3: Build dependency graph
        dependency_graph = self._build_dependency_graph(modules_with_model)

        # Step 4: Topological sort to get correct order
        sorted_modules = self._topological_sort(dependency_graph)

        # Step 5: Ensure base module is first
        if base_module:
            # Remove base from sorted list if present
            if base_module in sorted_modules:
                sorted_modules.remove(base_module)
            # Add base as first
            sorted_modules.insert(0, base_module)

        # Step 6: Add any missing modules (from circular dependencies)
        for module_name in modules_with_model:
            if module_name not in sorted_modules:
                sorted_modules.append(module_name)

        # Step 7: Build inheritance chain
        chain = []
        for order, module_name in enumerate(sorted_modules, start=1):
            model_info = modules_with_model[module_name]
            node = InheritanceNode(model_info, order)

            # Add dependencies from manifest
            deps = self.manifest_parser.get_dependencies(module_name)
            # Filter to only deps that are in our chain
            node.depends_on = [d for d in deps if d in modules_with_model]

            chain.append(node)

        return chain

    def _find_modules_with_model(self, model_name: str, context_module: Optional[str]) -> Dict[str, ModelInfo]:
        """
        Find all modules that define or extend a model.

        Args:
            model_name: Name of the model
            context_module: Optional - starting module

        Returns:
            Dictionary mapping module_name -> ModelInfo
        """
        result = {}

        # Determine which modules to search
        if context_module:
            # Get all dependencies of context module
            all_deps = self.manifest_parser.get_all_dependencies_recursive(context_module)
            modules_to_search = all_deps + [context_module]
        else:
            # Search all available modules
            modules_to_search = self._get_all_modules()

        # Search each module for the model
        for module_name in modules_to_search:
            module_path = self.manifest_parser.find_module_path(module_name)
            if not module_path:
                continue

            models = self.model_parser.find_models_in_module(module_path, module_name, model_name)

            # Take the first matching model from this module
            for model_info in models:
                if model_info.model_name == model_name:
                    result[module_name] = model_info
                    break

        return result

    def _get_all_modules(self) -> List[str]:
        """
        Get list of all available modules.

        Returns:
            List of module names
        """
        modules = []
        for addon_path in self.addon_paths:
            if not addon_path.exists():
                continue

            for item in addon_path.iterdir():
                if not item.is_dir():
                    continue

                # Check if it has __manifest__.py
                manifest = item / '__manifest__.py'
                if manifest.exists():
                    modules.append(item.name)

        return modules

    def _build_dependency_graph(self, modules_with_model: Dict[str, ModelInfo]) -> Dict[str, List[str]]:
        """
        Build dependency graph from module dependencies.

        Args:
            modules_with_model: Dictionary of module_name -> ModelInfo

        Returns:
            Graph as dict: module_name -> [dependent_modules]
        """
        graph = {}

        for module_name in modules_with_model:
            deps = self.manifest_parser.get_dependencies(module_name)
            # Only include dependencies that are also in our model chain
            relevant_deps = [d for d in deps if d in modules_with_model]
            graph[module_name] = relevant_deps

        return graph

    def _topological_sort(self, graph: Dict[str, List[str]]) -> List[str]:
        """
        Topological sort of dependency graph.

        Args:
            graph: Dependency graph

        Returns:
            Sorted list of module names
        """
        # Calculate in-degree for each node
        in_degree = {node: 0 for node in graph}
        for node in graph:
            for dep in graph[node]:
                if dep in in_degree:
                    in_degree[dep] += 1

        # Find nodes with no dependencies (base modules)
        queue = [node for node, degree in in_degree.items() if degree == 0]
        result = []

        while queue:
            # Sort to ensure deterministic order
            queue.sort()
            node = queue.pop(0)
            result.append(node)

            # Reduce in-degree for dependent nodes
            for other_node in graph:
                if node in graph[other_node]:
                    in_degree[other_node] -= 1
                    if in_degree[other_node] == 0:
                        queue.append(other_node)

        # Check for circular dependencies
        if len(result) != len(graph):
            # Return what we have, but warn
            print(f"Warning: Circular dependency detected in modules: {set(graph.keys()) - set(result)}", file=sys.stderr)

        # Reverse to get correct order (base first, extensions last)
        return list(reversed(result))

    def find_base_definition(self, model_name: str) -> Optional[ModelInfo]:
        """
        Find the base definition of a model (_name = ...).

        Args:
            model_name: Name of the model

        Returns:
            ModelInfo of base definition or None
        """
        all_modules = self._get_all_modules()

        for module_name in all_modules:
            module_path = self.manifest_parser.find_module_path(module_name)
            if not module_path:
                continue

            models = self.model_parser.find_models_in_module(module_path, module_name, model_name)

            for model_info in models:
                if model_info.is_base and model_info.model_name == model_name:
                    return model_info

        return None
