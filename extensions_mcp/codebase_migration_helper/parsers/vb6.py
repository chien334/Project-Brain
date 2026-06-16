import re
from .base import BaseParser, register_parser

@register_parser(["vb6"])
class VB6Parser(BaseParser):
    def parse(self, relative_path, code):
        nodes = []
        edges = []
        defined_symbols = {}
        file_node_id = f"{relative_path}::File"
        lines = code.split("\n")
        
        sub_pat = re.compile(r'^\s*(?:Public|Private|Friend)?\s*(?:Static\s+)?(?:Sub|Function|Property\s+(?:Get|Let|Set))\s+([a-zA-Z0-9_]+)', re.IGNORECASE)
        for idx, line in enumerate(lines):
            line_num = idx + 1
            cleaned = line.strip()
            m_sub = sub_pat.match(cleaned)
            if m_sub:
                sub_name = m_sub.group(1)
                sub_id = f"{relative_path}::Function::{sub_name}"
                nodes.append({
                    "id": sub_id,
                    "kind": "function",
                    "name": sub_name,
                    "qualified_name": sub_name,
                    "file_path": relative_path,
                    "language": "vb6",
                    "start_line": line_num,
                    "end_line": line_num,
                    "start_column": line.find(sub_name),
                    "end_column": line.find(sub_name) + len(sub_name),
                    "docstring": "",
                    "signature": cleaned,
                    "visibility": "public"
                })
                edges.append({
                    "source": file_node_id,
                    "target": sub_id,
                    "kind": "contains",
                    "line": line_num,
                    "col": line.find(sub_name)
                })
                defined_symbols[sub_name] = sub_id
        return nodes, edges, defined_symbols
