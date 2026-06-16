import re
from .base import BaseParser, register_parser

@register_parser(["cobol"])
class CobolParser(BaseParser):
    def parse(self, relative_path, code):
        nodes = []
        edges = []
        defined_symbols = {}
        file_node_id = f"{relative_path}::File"
        lines = code.split("\n")
        
        div_pat = re.compile(r'^\s*(IDENTIFICATION|ENVIRONMENT|DATA|PROCEDURE)\s+DIVISION\.', re.IGNORECASE)
        sec_pat = re.compile(r'^\s*([A-Z0-9\-]+)\s+SECTION\.', re.IGNORECASE)
        para_pat = re.compile(r'^\s*([A-Z0-9\-]+)\.\s*$', re.IGNORECASE)
        
        current_division = None
        current_section = None
        
        for idx, line in enumerate(lines):
            line_num = idx + 1
            cleaned = line.strip()
            
            m_div = div_pat.match(cleaned)
            if m_div:
                div_name = f"{m_div.group(1).upper()} DIVISION"
                div_id = f"{relative_path}::Division::{div_name.replace(' ', '_')}"
                nodes.append({
                    "id": div_id,
                    "kind": "class",
                    "name": div_name,
                    "qualified_name": div_name,
                    "file_path": relative_path,
                    "language": "cobol",
                    "start_line": line_num,
                    "end_line": line_num,
                    "start_column": 0,
                    "end_column": len(div_name),
                    "docstring": "",
                    "signature": div_name,
                    "visibility": "public"
                })
                edges.append({
                    "source": file_node_id,
                    "target": div_id,
                    "kind": "contains",
                    "line": line_num,
                    "col": 0
                })
                current_division = div_id
                current_section = None
                continue
                
            m_sec = sec_pat.match(cleaned)
            if m_sec:
                sec_name = m_sec.group(1).upper()
                sec_id = f"{relative_path}::Section::{sec_name}"
                parent = current_division if current_division else file_node_id
                nodes.append({
                    "id": sec_id,
                    "kind": "class",
                    "name": f"{sec_name} SECTION",
                    "qualified_name": f"{sec_name} SECTION",
                    "file_path": relative_path,
                    "language": "cobol",
                    "start_line": line_num,
                    "end_line": line_num,
                    "start_column": 0,
                    "end_column": len(sec_name) + 8,
                    "docstring": "",
                    "signature": f"{sec_name} SECTION",
                    "visibility": "public"
                })
                edges.append({
                    "source": parent,
                    "target": sec_id,
                    "kind": "contains",
                    "line": line_num,
                    "col": 0
                })
                current_section = sec_id
                continue
                
            m_para = para_pat.match(cleaned)
            if m_para:
                para_name = m_para.group(1).upper()
                if para_name in ("IDENTIFICATION", "ENVIRONMENT", "DATA", "PROCEDURE", "SECTION", "DIVISION"):
                    continue
                para_id = f"{relative_path}::Function::{para_name}"
                parent = current_section if current_section else (current_division if current_division else file_node_id)
                nodes.append({
                    "id": para_id,
                    "kind": "function",
                    "name": para_name,
                    "qualified_name": para_name,
                    "file_path": relative_path,
                    "language": "cobol",
                    "start_line": line_num,
                    "end_line": line_num,
                    "start_column": 0,
                    "end_column": len(para_name),
                    "docstring": "",
                    "signature": f"{para_name}.",
                    "visibility": "public"
                })
                edges.append({
                    "source": parent,
                    "target": para_id,
                    "kind": "contains",
                    "line": line_num,
                    "col": 0
                })
                defined_symbols[para_name] = para_id
                
        return nodes, edges, defined_symbols
