"""
JSON formatter for model inspection results.
"""

import json
from typing import Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from parsers.dependency_resolver import InheritanceNode
    from parsers.model_parser import ModelInfo


class JsonFormatter:
    """Formats inspection results as JSON."""

    @staticmethod
    def format_inheritance_chain(
        model_name: str,
        chain: List,
        context_module: Optional[str] = None,
        base_definition = None,
        docs_paths: Optional[List[str]] = None
    ) -> str:
        """
        Format inheritance chain as JSON.

        Args:
            model_name: Name of the model
            chain: List of InheritanceNode objects
            context_module: Optional context module name
            base_definition: Optional base model definition
            docs_paths: Optional list of documentation paths

        Returns:
            JSON string
        """
        # Calculate totals
        total_fields = sum(len(node.model_info.fields) for node in chain)
        modules_involved = len(chain)

        # Build chain data
        chain_data = []
        for node in chain:
            node_dict = node.to_dict()
            chain_data.append(node_dict)

        # Build base definition data
        base_def_data = None
        if base_definition:
            base_def_data = {
                'module': base_definition.module,
                'file': str(base_definition.file_path),
                'line': base_definition.line
            }

        # Build result
        result = {
            'model': model_name,
            'context_module': context_module,
            'base_definition': base_def_data,
            'inheritance_chain': chain_data,
            'total_fields': total_fields,
            'modules_involved': modules_involved,
            'docs_to_read': docs_paths or []
        }

        return json.dumps(result, indent=2)

    @staticmethod
    def format_error(error_message: str) -> str:
        """
        Format error as JSON.

        Args:
            error_message: Error message

        Returns:
            JSON string
        """
        return json.dumps({
            'error': error_message,
            'status': 'failed'
        }, indent=2)
