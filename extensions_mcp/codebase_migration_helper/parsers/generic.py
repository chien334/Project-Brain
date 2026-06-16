import re
from .base import BaseParser, register_parser

class GenericParser(BaseParser):
    def __init__(self, lang):
        self.lang = lang
        
    def parse(self, relative_path, code):
        nodes = []
        edges = []
        defined_symbols = {}
        file_node_id = f"{relative_path}::File"
        lines = code.split("\n")
        
        class_pattern = re.compile(r'(?:public|protected|private|static|\s|^)class\s+([a-zA-Z0-9_]+)')
        method_pattern = re.compile(r'(?:public|protected|private|static|\s)+[a-zA-Z0-9_<>\[\]]+ +([a-zA-Z0-9_]+)\s*\([^)]*\)\s*\{')
        
        current_class_id = None
        for idx, line in enumerate(lines):
            line_num = idx + 1
            
            m_class = class_pattern.search(line)
            if m_class:
                class_name = m_class.group(1)
                class_id = f"{relative_path}::Class::{class_name}"
                nodes.append({
                    "id": class_id,
                    "kind": "class",
                    "name": class_name,
                    "qualified_name": class_name,
                    "file_path": relative_path,
                    "language": self.lang,
                    "start_line": line_num,
                    "end_line": line_num,
                    "start_column": line.find(class_name),
                    "end_column": line.find(class_name) + len(class_name),
                    "docstring": "",
                    "signature": f"class {class_name}",
                    "visibility": "public"
                })
                edges.append({
                    "source": file_node_id,
                    "target": class_id,
                    "kind": "contains",
                    "line": line_num,
                    "col": line.find(class_name)
                })
                defined_symbols[class_name] = class_id
                current_class_id = class_id
                continue
                
            m_method = method_pattern.search(line)
            if m_method:
                method_name = m_method.group(1)
                if method_name in ("if", "for", "while", "switch", "catch", "return"):
                    continue
                class_name = current_class_id.split("::")[-1] if current_class_id else None
                qual_name = f"{class_name}.{method_name}" if class_name else method_name
                method_id = f"{relative_path}::Function::{qual_name}"
                
                nodes.append({
                    "id": method_id,
                    "kind": "method" if current_class_id else "function",
                    "name": method_name,
                    "qualified_name": qual_name,
                    "file_path": relative_path,
                    "language": self.lang,
                    "start_line": line_num,
                    "end_line": line_num,
                    "start_column": line.find(method_name),
                    "end_column": line.find(method_name) + len(method_name),
                    "docstring": "",
                    "signature": line.strip().rstrip("{").strip(),
                    "visibility": "public"
                })
                
                parent_id = current_class_id if current_class_id else file_node_id
                edges.append({
                    "source": parent_id,
                    "target": method_id,
                    "kind": "contains",
                    "line": line_num,
                    "col": line.find(method_name)
                })
                defined_symbols[qual_name] = method_id
                defined_symbols[method_name] = method_id
                
        return nodes, edges, defined_symbols
