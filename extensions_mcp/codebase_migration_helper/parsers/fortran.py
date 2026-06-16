import re
from .base import BaseParser, register_parser

@register_parser(["fortran"])
class FortranParser(BaseParser):
    def parse(self, relative_path, code):
        nodes = []
        edges = []
        defined_symbols = {}
        file_node_id = f"{relative_path}::File"
        lines = code.split("\n")
        
        fort_pat = re.compile(r'^\s*(?:PROGRAM|SUBROUTINE|FUNCTION)\s+([a-zA-Z0-9_]+)', re.IGNORECASE)
        for idx, line in enumerate(lines):
            line_num = idx + 1
            cleaned = line.strip()
            m_fort = fort_pat.match(cleaned)
            if m_fort:
                name = m_fort.group(1)
                node_id = f"{relative_path}::Function::{name}"
                nodes.append({
                    "id": node_id,
                    "kind": "function",
                    "name": name,
                    "qualified_name": name,
                    "file_path": relative_path,
                    "language": "fortran",
                    "start_line": line_num,
                    "end_line": line_num,
                    "start_column": line.find(name),
                    "end_column": line.find(name) + len(name),
                    "docstring": "",
                    "signature": cleaned,
                    "visibility": "public"
                })
                edges.append({
                    "source": file_node_id,
                    "target": node_id,
                    "kind": "contains",
                    "line": line_num,
                    "col": line.find(name)
                })
                defined_symbols[name] = node_id
        return nodes, edges, defined_symbols
