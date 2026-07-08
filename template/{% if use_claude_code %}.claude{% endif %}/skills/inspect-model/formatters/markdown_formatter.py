"""
Markdown formatter for model inspection results.
"""

from typing import Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from parsers.dependency_resolver import InheritanceNode
    from parsers.model_parser import ModelInfo


class MarkdownFormatter:
    """Formats inspection results as Markdown."""

    @staticmethod
    def format_inheritance_chain(
        model_name: str,
        chain: List,
        context_module: Optional[str] = None,
        base_definition = None,
        docs_paths: Optional[List[str]] = None
    ) -> str:
        """
        Format inheritance chain as Markdown.

        Args:
            model_name: Name of the model
            chain: List of InheritanceNode objects
            context_module: Optional context module name
            base_definition: Optional base model definition
            docs_paths: Optional list of documentation paths

        Returns:
            Markdown string
        """
        lines = []

        # Header
        total_fields = sum(len(node.model_info.fields) for node in chain)
        lines.append(f"# Model: {model_name}")
        lines.append(f"**Total Fields:** {total_fields}")
        if context_module:
            lines.append(f"**Context Module:** {context_module}")
        lines.append("")

        # Base definition
        if base_definition:
            lines.append("## Base Definition")
            lines.append(f"- **Module:** {base_definition.module}")
            lines.append(f"- **File:** `{base_definition.file_path}:{base_definition.line}`")
            lines.append("")

        # Inheritance chain visualization
        lines.append("## Inheritance Chain")
        lines.append("")
        lines.append("```")
        MarkdownFormatter._build_chain_tree(chain, lines, context_module)
        lines.append("```")
        lines.append("")

        # Detailed module information
        lines.append("## Module Details")
        lines.append("")

        for node in chain:
            module = node.model_info.module
            is_current = context_module and module == context_module

            # Module header
            header = f"### {node.order}. {module}"
            if node.model_info.is_base:
                header += " (base definition)"
            elif is_current:
                header += " (current context)"

            lines.append(header)
            lines.append(f"**File:** `{node.model_info.file_path}:{node.model_info.line}`")

            if node.depends_on:
                lines.append(f"**Depends:** {', '.join(node.depends_on)}")

            lines.append("")

            # Fields
            if node.model_info.fields:
                action = "Defined" if node.model_info.is_base else "Added"
                lines.append(f"**Fields {action} ({len(node.model_info.fields)}):**")
                lines.append("")

                for field_name, field_info in sorted(node.model_info.fields.items()):
                    # Handle both dict and string format for backward compatibility
                    if isinstance(field_info, dict):
                        field_type = field_info['type']
                        required_marker = " [required]" if field_info.get('required', False) else ""
                        lines.append(f"- `{field_name}`: {field_type}{required_marker}")
                    else:
                        # Old format (string)
                        lines.append(f"- `{field_name}`: {field_info}")

                lines.append("")
            else:
                lines.append("*No fields added in this module*")
                lines.append("")

            # Methods
            if node.model_info.methods:
                action = "Defined" if node.model_info.is_base else "Added"
                lines.append(f"**Methods {action} ({len(node.model_info.methods)}):**")
                lines.append("")

                # Find which methods override parent methods
                parent_methods = MarkdownFormatter._get_parent_methods(chain, node.order)

                for method_name, has_super in sorted(node.model_info.methods.items()):
                    if has_super and method_name in parent_methods:
                        parent_module = parent_methods[method_name]
                        lines.append(f"- `{method_name}` [super from {parent_module}]")
                    else:
                        lines.append(f"- `{method_name}`")

                lines.append("")

            lines.append("---")
            lines.append("")

        # Summary
        lines.append("## Summary")
        lines.append("")
        lines.append(f"- **Total Fields:** {total_fields}")
        lines.append(f"- **Modules Involved:** {len(chain)}")
        lines.append("")

        # Documentation
        if docs_paths:
            lines.append("## Related Documentation")
            lines.append("")
            for doc_path in docs_paths:
                lines.append(f"- {doc_path}")
            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def _build_chain_tree(chain: List, lines: List[str], context_module: Optional[str]):
        """
        Build ASCII tree representation of inheritance chain.

        Args:
            chain: List of InheritanceNode objects
            lines: List to append lines to
            context_module: Optional context module to mark
        """
        if not chain:
            return

        for i, node in enumerate(chain):
            module = node.model_info.module
            fields_count = len(node.model_info.fields)

            # Build prefix
            if i == 0:
                # Base module
                prefix = ""
                suffix = f" (BASE) - {fields_count} fields"
            else:
                # Calculate indentation
                indent = "  " * (i - 1)
                if i == len(chain) - 1 and context_module == module:
                    suffix = f" - +{fields_count} fields [CURRENT]"
                else:
                    suffix = f" - +{fields_count} fields"
                prefix = f"{indent}└─> "

            line = f"{prefix}{module}{suffix}"
            lines.append(line)

    @staticmethod
    def _get_parent_methods(chain: List, current_order: int) -> Dict[str, str]:
        """
        Get methods from parent modules in the chain.

        Args:
            chain: List of InheritanceNode objects
            current_order: Order of current module

        Returns:
            Dict of method_name -> module_name where method is defined
        """
        parent_methods = {}

        # Look at all modules before current one
        for node in chain[:current_order-1]:
            for method_name in node.model_info.methods:
                # Keep first occurrence (closest parent)
                if method_name not in parent_methods:
                    parent_methods[method_name] = node.model_info.module

        return parent_methods
