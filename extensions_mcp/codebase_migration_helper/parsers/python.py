import ast
from .base import BaseParser, register_parser

class PythonASTParser(ast.NodeVisitor):
    def __init__(self, relative_path, code):
        self.relative_path = relative_path
        self.code = code
        self.nodes = []
        self.edges = []
        self.current_class = None
        self.defined_symbols = {}
        
    def parse(self):
        try:
            tree = ast.parse(self.code)
            self.visit(tree)
        except Exception:
            pass
            
    def visit_ClassDef(self, node):
        class_id = f"{self.relative_path}::Class::{node.name}"
        doc = ast.get_docstring(node) or ""
        
        bases = [ast.unparse(b) for b in node.bases] if hasattr(ast, "unparse") else []
        sig = f"class {node.name}" + (f"({', '.join(bases)})" if bases else "")
        
        node_data = {
            "id": class_id,
            "kind": "class",
            "name": node.name,
            "qualified_name": node.name,
            "file_path": self.relative_path,
            "language": "python",
            "start_line": node.lineno,
            "end_line": getattr(node, "end_lineno", node.lineno),
            "start_column": node.col_offset,
            "end_column": getattr(node, "end_col_offset", node.col_offset),
            "docstring": doc,
            "signature": sig,
            "visibility": "public"
        }
        self.nodes.append(node_data)
        self.defined_symbols[node.name] = class_id
        
        file_node_id = f"{self.relative_path}::File"
        self.edges.append({
            "source": file_node_id,
            "target": class_id,
            "kind": "contains",
            "line": node.lineno,
            "col": node.col_offset
        })
        
        prev_class = self.current_class
        self.current_class = class_id
        self.generic_visit(node)
        self.current_class = prev_class
        
    def visit_FunctionDef(self, node):
        self.handle_func(node, is_async=0)
        
    def visit_AsyncFunctionDef(self, node):
        self.handle_func(node, is_async=1)
        
    def handle_func(self, node, is_async=0):
        kind = "method" if self.current_class else "function"
        class_name = self.current_class.split("::")[-1] if self.current_class else None
        qual_name = f"{class_name}.{node.name}" if class_name else node.name
        func_id = f"{self.relative_path}::Function::{qual_name}"
        
        doc = ast.get_docstring(node) or ""
        
        args = []
        for arg in node.args.args:
            args.append(arg.arg)
        sig = f"{'async ' if is_async else ''}def {node.name}({', '.join(args)})"
        
        node_data = {
            "id": func_id,
            "kind": kind,
            "name": node.name,
            "qualified_name": qual_name,
            "file_path": self.relative_path,
            "language": "python",
            "start_line": node.lineno,
            "end_line": getattr(node, "end_lineno", node.lineno),
            "start_column": node.col_offset,
            "end_column": getattr(node, "end_col_offset", node.col_offset),
            "docstring": doc,
            "signature": sig,
            "visibility": "public",
            "is_async": is_async
        }
        self.nodes.append(node_data)
        self.defined_symbols[qual_name] = func_id
        self.defined_symbols[node.name] = func_id
        
        parent_id = self.current_class if self.current_class else f"{self.relative_path}::File"
        self.edges.append({
            "source": parent_id,
            "target": func_id,
            "kind": "contains",
            "line": node.lineno,
            "col": node.col_offset
        })
        
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                call_name = None
                if isinstance(child.func, ast.Name):
                    call_name = child.func.id
                elif isinstance(child.func, ast.Attribute):
                    call_name = child.func.attr
                
                if call_name:
                    self.edges.append({
                        "source": func_id,
                        "target_name": call_name,
                        "kind": "calls",
                        "line": child.lineno,
                        "col": child.col_offset
                    })

@register_parser(["python"])
class PythonParser(BaseParser):
    def parse(self, relative_path, code):
        parser = PythonASTParser(relative_path, code)
        parser.parse()
        return parser.nodes, parser.edges, parser.defined_symbols
