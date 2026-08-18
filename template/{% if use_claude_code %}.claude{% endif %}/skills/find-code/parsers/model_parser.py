"""
Parser for Odoo model files.
Extracts model definitions, inheritance, and fields using AST.
"""

import ast
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class ModelInfo:
    """Information about a model definition."""

    def __init__(self, module: str, file_path: Path, line: int):
        self.module = module
        self.file_path = file_path
        self.line = line
        self.model_name: Optional[str] = None
        self.inherits: List[str] = []
        self.is_base = False
        self.fields: Dict[str, Dict[str, any]] = {}  # field_name -> {type, required}
        self.methods: Dict[str, bool] = {}  # method_name -> has_super

    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            'module': self.module,
            'file': str(self.file_path),
            'line': self.line,
            'model_name': self.model_name,
            'inherits': self.inherits,
            'is_base': self.is_base,
            'fields': self.fields,
            'fields_count': len(self.fields),
            'methods': self.methods,
            'methods_count': len(self.methods)
        }


class ModelParser:
    """Parses Python files to extract Odoo model definitions."""

    # Known Odoo field types
    FIELD_TYPES = {
        'Char', 'Text', 'Html', 'Integer', 'Float', 'Monetary',
        'Boolean', 'Date', 'Datetime', 'Binary', 'Selection',
        'Many2one', 'One2many', 'Many2many', 'Reference',
        'Json', 'Properties'
    }

    def __init__(self):
        self._cache: Dict[str, List[ModelInfo]] = {}

    def parse_file(self, file_path: Path, module_name: str) -> List[ModelInfo]:
        """
        Parse Python file and extract model definitions.

        Args:
            file_path: Path to Python file
            module_name: Name of the module

        Returns:
            List of ModelInfo objects
        """
        cache_key = str(file_path)
        if cache_key in self._cache:
            return self._cache[cache_key]

        models = []

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()

            tree = ast.parse(content)

            # Find class definitions
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    model_info = self._parse_class(node, module_name, file_path)
                    if model_info:
                        models.append(model_info)

        except Exception as e:
            print(f"Error parsing {file_path}: {e}")

        self._cache[cache_key] = models
        return models

    def _parse_class(self, class_node: ast.ClassDef, module_name: str, file_path: Path) -> Optional[ModelInfo]:
        """
        Parse class definition to extract model info.

        Args:
            class_node: AST ClassDef node
            module_name: Name of the module
            file_path: Path to file

        Returns:
            ModelInfo object or None
        """
        model_info = ModelInfo(
            module=module_name,
            file_path=file_path,
            line=class_node.lineno
        )

        # Check if inherits from models.Model or models.TransientModel
        is_model_class = False
        for base in class_node.bases:
            base_name = self._get_attribute_name(base)
            if base_name in ['models.Model', 'models.TransientModel', 'models.AbstractModel']:
                is_model_class = True
                break

        if not is_model_class:
            return None

        # Parse class body
        for node in class_node.body:
            if isinstance(node, ast.Assign):
                self._parse_assignment(node, model_info)
            elif isinstance(node, ast.FunctionDef):
                self._parse_method(node, model_info)

        # Only return if we found _name or _inherit
        if model_info.model_name or model_info.inherits:
            return model_info

        return None

    def _parse_assignment(self, assign_node: ast.Assign, model_info: ModelInfo):
        """
        Parse assignment statement.

        Args:
            assign_node: AST Assign node
            model_info: ModelInfo to populate
        """
        for target in assign_node.targets:
            if not isinstance(target, ast.Name):
                continue

            var_name = target.id

            # Check for _name, _inherit, _inherits
            if var_name == '_name':
                model_info.model_name = self._extract_string_value(assign_node.value)
                model_info.is_base = True

            elif var_name == '_inherit':
                inherits = self._extract_inherit_value(assign_node.value)
                model_info.inherits = inherits
                # If _inherit without _name, use first inherit as model_name
                if not model_info.model_name and inherits:
                    model_info.model_name = inherits[0]

            # Check for fields
            elif self._is_field_assignment(assign_node.value):
                field_info = self._extract_field_info(assign_node.value)
                if field_info:
                    model_info.fields[var_name] = field_info

    def _parse_method(self, func_node: ast.FunctionDef, model_info: ModelInfo):
        """
        Parse method definition.

        Args:
            func_node: AST FunctionDef node
            model_info: ModelInfo to populate
        """
        method_name = func_node.name

        # Skip private methods (but keep Odoo magic methods like _compute_*)
        if method_name.startswith('__'):
            return

        # Check if method contains super() call
        has_super = self._has_super_call(func_node)

        model_info.methods[method_name] = has_super

    def _has_super_call(self, func_node: ast.FunctionDef) -> bool:
        """
        Check if function contains super() call.

        Args:
            func_node: AST FunctionDef node

        Returns:
            True if contains super() call
        """
        for node in ast.walk(func_node):
            if isinstance(node, ast.Call):
                func_name = self._get_attribute_name(node.func)
                if func_name == 'super':
                    return True
        return False

    def _is_field_assignment(self, value_node) -> bool:
        """
        Check if assignment is a field definition.

        Args:
            value_node: AST value node

        Returns:
            True if it's a field assignment
        """
        if isinstance(value_node, ast.Call):
            func_name = self._get_attribute_name(value_node.func)
            # Check for fields.* or direct field type
            if func_name and ('fields.' in func_name or any(ft in func_name for ft in self.FIELD_TYPES)):
                return True
        return False

    def _extract_field_info(self, value_node) -> Optional[Dict[str, any]]:
        """
        Extract field information from assignment.

        Args:
            value_node: AST value node

        Returns:
            Dict with 'type' and 'required' or None
        """
        if not isinstance(value_node, ast.Call):
            return None

        func_name = self._get_attribute_name(value_node.func)
        if not func_name:
            return None

        # Extract type from fields.Char -> Char
        field_type = None
        for ft in self.FIELD_TYPES:
            if ft in func_name:
                field_type = ft
                break

        if not field_type:
            return None

        # Extract required parameter
        required = self._extract_required_param(value_node)

        return {
            'type': field_type,
            'required': required
        }

    def _extract_required_param(self, call_node: ast.Call) -> bool:
        """
        Extract 'required' parameter from field call.

        Args:
            call_node: AST Call node

        Returns:
            True if required=True, False otherwise
        """
        # Check keyword arguments
        for keyword in call_node.keywords:
            if keyword.arg == 'required':
                # Get the value
                if isinstance(keyword.value, ast.Constant):
                    return bool(keyword.value.value)
                elif isinstance(keyword.value, ast.NameConstant):  # Python < 3.8
                    return bool(keyword.value.value)

        return False

    def _extract_string_value(self, node) -> Optional[str]:
        """
        Extract string value from AST node.

        Args:
            node: AST node

        Returns:
            String value or None
        """
        if isinstance(node, ast.Constant):
            return node.value if isinstance(node.value, str) else None
        elif isinstance(node, ast.Str):  # Python < 3.8
            return node.s
        return None

    def _extract_inherit_value(self, node) -> List[str]:
        """
        Extract _inherit value (can be string or list of strings).

        Args:
            node: AST node

        Returns:
            List of inherited model names
        """
        # Single string
        if isinstance(node, (ast.Constant, ast.Str)):
            value = self._extract_string_value(node)
            return [value] if value else []

        # List of strings
        if isinstance(node, ast.List):
            result = []
            for item in node.elts:
                value = self._extract_string_value(item)
                if value:
                    result.append(value)
            return result

        return []

    def _get_attribute_name(self, node) -> Optional[str]:
        """
        Get full attribute name from AST node.

        Args:
            node: AST node (Name or Attribute)

        Returns:
            Full attribute name (e.g., 'fields.Char')
        """
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            value_name = self._get_attribute_name(node.value)
            if value_name:
                return f"{value_name}.{node.attr}"
            return node.attr
        return None

    def find_models_in_module(self, module_path: Path, module_name: str, model_name: Optional[str] = None) -> List[ModelInfo]:
        """
        Find all models in a module.

        Args:
            module_path: Path to module directory
            module_name: Name of the module
            model_name: Optional - filter by specific model name

        Returns:
            List of ModelInfo objects
        """
        models = []
        models_dir = module_path / 'models'

        if not models_dir.exists():
            return models

        # Parse all Python files in models directory
        for py_file in models_dir.rglob('*.py'):
            if py_file.name.startswith('__'):
                continue

            file_models = self.parse_file(py_file, module_name)

            # Filter by model_name if specified
            if model_name:
                file_models = [m for m in file_models if m.model_name == model_name or model_name in m.inherits]

            models.extend(file_models)

        return models
