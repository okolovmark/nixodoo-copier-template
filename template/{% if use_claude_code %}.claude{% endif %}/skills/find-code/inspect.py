#!/usr/bin/env python3
"""
Odoo Model Inspector
Analyzes model inheritance chains and fields across modules.
"""

import argparse
import sys
from pathlib import Path

# Add parsers and formatters to path
sys.path.insert(0, str(Path(__file__).parent))

from parsers.dependency_resolver import DependencyResolver
from formatters.json_formatter import JsonFormatter
from formatters.markdown_formatter import MarkdownFormatter
from config import PROJECT_ROOT, ADDON_DIRECTORIES


def get_addon_paths() -> list:
    """
    Get addon paths from config.

    Returns:
        List of Path objects to addon directories
    """
    paths = []
    for dir_name in ADDON_DIRECTORIES:
        path = PROJECT_ROOT / dir_name
        if path.exists():
            paths.append(path)

    return paths


def main():
    parser = argparse.ArgumentParser(
        description='Analyze Odoo model inheritance chain'
    )
    parser.add_argument(
        '--model',
        required=True,
        help='Model name (e.g., sale.order, product.product)'
    )
    parser.add_argument(
        '--context-module',
        help='Analyze from this module perspective'
    )
    parser.add_argument(
        '--output-markdown',
        help='Output Markdown file path (optional)'
    )

    args = parser.parse_args()

    try:
        # Get addon paths
        addon_paths = get_addon_paths()

        if not addon_paths:
            print(JsonFormatter.format_error("No addon directories found"))
            sys.exit(1)

        # Initialize resolver
        resolver = DependencyResolver(addon_paths)

        # Build inheritance chain
        chain = resolver.build_inheritance_chain(
            model_name=args.model,
            context_module=args.context_module
        )

        if not chain:
            print(JsonFormatter.format_error(f"Model '{args.model}' not found"))
            sys.exit(1)

        # Find base definition
        base_def = resolver.find_base_definition(args.model)

        # Output JSON to stdout
        json_output = JsonFormatter.format_inheritance_chain(
            model_name=args.model,
            chain=chain,
            context_module=args.context_module,
            base_definition=base_def,
            docs_paths=None
        )
        print(json_output)

        # Optionally output Markdown
        if args.output_markdown:
            markdown_output = MarkdownFormatter.format_inheritance_chain(
                model_name=args.model,
                chain=chain,
                context_module=args.context_module,
                base_definition=base_def,
                docs_paths=None
            )

            output_path = Path(args.output_markdown)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(markdown_output, encoding='utf-8')

            # Print info to stderr so it doesn't mix with JSON
            print(f"Markdown saved to: {output_path}", file=sys.stderr)

    except Exception as e:
        print(JsonFormatter.format_error(str(e)))
        sys.exit(1)


if __name__ == '__main__':
    main()
