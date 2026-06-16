import re
from .base import BaseParser, register_parser

@register_parser(["pascal"])
class PascalParser(BaseParser):
    def parse(self, relative_path, code):
        nodes = []
        edges = []
        defined_symbols = {}
        file_node_id = f"{relative_path}::File"
        lines = code.split("\n")
        
        unit_pat = re.compile(r'^\s*(?:unit|program|library)\s+([a-zA-Z0-9_]+)\s*;', re.IGNORECASE)
        proc_pat = re.compile(r'^\s*(?:procedure|function)\s+([a-zA-Z0-9_]+)', re.IGNORECASE)
        current_unit_id = None
        
        for idx, line in enumerate(lines):
            line_num = idx + 1
            cleaned = line.strip()
            
            m_unit = unit_pat.match(cleaned)
            if m_unit:
                unit_name = m_unit.group(1)
                unit_id = f"{relative_path}::Class::{unit_name}"
                nodes.append({
                    "id": unit_id,
                    "kind": "class",
                    "name": unit_name,
                    "qualified_name": unit_name,
                    "file_path": relative_path,
                    "language": "pascal",
                    "start_line": line_num,
                    "end_line": line_num,
                    "start_column": line.find(unit_name),
                    "end_column": line.find(unit_name) + len(unit_name),
                    "docstring": "",
                    "signature": cleaned,
                    "visibility": "public"
                })
                edges.append({
                    "source": file_node_id,
                    "target": unit_id,
                    "kind": "contains",
                    "line": line_num,
                    "col": line.find(unit_name)
                })
                current_unit_id = unit_id
                continue
                
            m_proc = proc_pat.match(cleaned)
            if m_proc:
                proc_name = m_proc.group(1)
                proc_id = f"{relative_path}::Function::{proc_name}"
                parent = current_unit_id if current_unit_id else file_node_id
                nodes.append({
                    "id": proc_id,
                    "kind": "function",
                    "name": proc_name,
                    "qualified_name": proc_name,
                    "file_path": relative_path,
                    "language": "pascal",
                    "start_line": line_num,
                    "end_line": line_num,
                    "start_column": line.find(proc_name),
                    "end_column": line.find(proc_name) + len(proc_name),
                    "docstring": "",
                    "signature": cleaned,
                    "visibility": "public"
                })
                edges.append({
                    "source": parent,
                    "target": proc_id,
                    "kind": "contains",
                    "line": line_num,
                    "col": line.find(proc_name)
                })
                defined_symbols[proc_name] = proc_id
        return nodes, edges, defined_symbols
