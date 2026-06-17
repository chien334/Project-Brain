import re
import xml.etree.ElementTree as ET
from .base import BaseParser, register_parser

@register_parser(["structured_text", "xml"])
class StructuredTextParser(BaseParser):
    def parse(self, relative_path, code):
        is_xml = False
        stripped = code.strip()
        if stripped.startswith("<"):
            is_xml = True
            
        if is_xml:
            return self._parse_xml(relative_path, code)
        else:
            # Parse ST code first
            st_nodes, st_edges, st_defs = self._parse_st(relative_path, code)
            
            # Check for embedded XML block (*! ... *)
            xml_match = re.search(r'\(\*!(.*?)\*\)', code, re.DOTALL)
            if xml_match:
                xml_code = xml_match.group(1).strip()
                try:
                    xml_nodes, xml_edges, xml_defs = self._parse_xml(relative_path, xml_code)
                    st_nodes.extend(xml_nodes)
                    st_edges.extend(xml_edges)
                    st_defs.update(xml_defs)
                except Exception as e:
                    print(f"Error parsing embedded XML in {relative_path}: {e}")
            return st_nodes, st_edges, st_defs
            
    def _parse_xml(self, relative_path, code):
        nodes = []
        edges = []
        defined_symbols = {}
        file_node_id = f"{relative_path}::File"
        
        try:
            # XML elements might not have a single root if it's a raw class block, wrap it if needed
            if not code.strip().startswith("<Class") and not code.strip().startswith("<Network") and not code.strip().startswith("<"):
                # fallback/ignore
                pass
            root = ET.fromstring(code)
            
            def walk(elem, current_class_id=None, parent_obj_name=None):
                tag_lower = elem.tag.lower()
                
                # Check for class, component or object instance
                if tag_lower == "class" or tag_lower == "component" or tag_lower == "object":
                    name = elem.get("Name") or elem.get("ID") or elem.get("Type")
                    if name and tag_lower != "components":
                        node_id = f"{relative_path}::Class::{name}"
                        # Object refers to component instances, Class refers to class definitions
                        kind = "class"
                        docstring = elem.get("Comment") or f"XML element {elem.tag}"
                        sig = f"{elem.tag} {name}"
                        if tag_lower == "object":
                            sig = f"Object {name} : {elem.get('Class', 'unknown')}"
                            if elem.get("Class"):
                                edges.append({
                                    "source": node_id,
                                    "target_name": elem.get("Class"),
                                    "kind": "instantiates",
                                    "line": 1,
                                    "col": 0
                                })
                            
                        nodes.append({
                            "id": node_id,
                            "kind": kind,
                            "name": name,
                            "qualified_name": name,
                            "file_path": relative_path,
                            "language": "structured_text",
                            "start_line": 1,
                            "end_line": 1,
                            "start_column": 0,
                            "end_column": 0,
                            "docstring": docstring,
                            "signature": sig,
                            "visibility": "public"
                        })
                        edges.append({
                            "source": file_node_id,
                            "target": node_id,
                            "kind": "contains",
                            "line": 1,
                            "col": 0
                        })
                        defined_symbols[name] = node_id
                        
                        # Set parent object name context for child server/clients
                        if tag_lower == "class":
                            parent_obj_name = "this"
                        elif tag_lower == "object":
                            parent_obj_name = name
                            
                        current_class_id = node_id
                        
                elif tag_lower == "server" or tag_lower == "client" or tag_lower == "var" or tag_lower == "channel":
                    name = elem.get("Name") or elem.get("ID")
                    if name:
                        var_kind = "variable"
                        if tag_lower == "server":
                            var_kind = "server"
                        elif tag_lower == "client":
                            var_kind = "client"
                            
                        # Prefix channel name with component name to allow connection matching
                        full_name = f"{parent_obj_name}.{name}" if parent_obj_name else name
                        var_id = f"{relative_path}::{var_kind.capitalize()}::{full_name}"
                        
                        nodes.append({
                            "id": var_id,
                            "kind": var_kind,
                            "name": full_name,
                            "qualified_name": full_name,
                            "file_path": relative_path,
                            "language": "structured_text",
                            "start_line": 1,
                            "end_line": 1,
                            "start_column": 0,
                            "end_column": 0,
                            "docstring": elem.get("Comment") or elem.get("DataType") or "",
                            "signature": f"{elem.tag} {full_name} : {elem.get('DataType', 'ANY')}",
                            "visibility": "public"
                        })
                        parent = current_class_id if current_class_id else file_node_id
                        edges.append({
                            "source": parent,
                            "target": var_id,
                            "kind": "contains",
                            "line": 1,
                            "col": 0
                        })
                        defined_symbols[full_name] = var_id
                        defined_symbols[name] = var_id
                        
                elif tag_lower == "connection" or tag_lower == "link":
                    src = elem.get("Source") or elem.get("Client") or elem.get("From") or elem.get("ClientName")
                    dest = elem.get("Destination") or elem.get("Server") or elem.get("To") or elem.get("ServerName")
                    if src and dest:
                        edges.append({
                            "source": f"{relative_path}::Client::{src}",
                            "target_name": dest,
                            "kind": "connects",
                            "line": 1,
                            "col": 0
                        })
                
                for child in elem:
                    walk(child, current_class_id, parent_obj_name)
                    
            walk(root)
        except Exception as e:
            print(f"Error parsing xml file {relative_path}: {e}")
            
        return nodes, edges, defined_symbols
        
    def _parse_st(self, relative_path, code):
        nodes = []
        edges = []
        defined_symbols = {}
        file_node_id = f"{relative_path}::File"
        lines = code.split("\n")
        
        class_pat = re.compile(r'^\s*(?:CLASS|FUNCTION_BLOCK|PROGRAM)\s+([a-zA-Z0-9_]+)', re.IGNORECASE)
        end_class_pat = re.compile(r'^\s*END_(?:CLASS|FUNCTION_BLOCK|PROGRAM)', re.IGNORECASE)
        method_pat = re.compile(r'^\s*(?:METHOD|FUNCTION)\s+(?:[a-zA-Z0-9_]+\s*::\s*)?([a-zA-Z0-9_]+)', re.IGNORECASE)
        
        current_class_id = None
        var_block = False
        
        for idx, line in enumerate(lines):
            line_num = idx + 1
            cleaned = line.strip()
            if not cleaned:
                continue
                
            parts_temp = cleaned.split(None, 1)
            first_word = parts_temp[0].upper() if parts_temp else ""
            
            m_class = None
            if first_word in ("CLASS", "FUNCTION_BLOCK", "PROGRAM"):
                m_class = class_pat.match(cleaned)
                if m_class:
                    class_name = m_class.group(1)
                    class_id = f"{relative_path}::Class::{class_name}"
                    nodes.append({
                        "id": class_id,
                        "kind": "class",
                        "name": class_name,
                        "qualified_name": class_name,
                        "file_path": relative_path,
                        "language": "structured_text",
                        "start_line": line_num,
                        "end_line": line_num,
                        "start_column": line.find(class_name),
                        "end_column": line.find(class_name) + len(class_name),
                        "docstring": "",
                        "signature": cleaned,
                        "visibility": "public"
                    })
                    edges.append({
                        "source": file_node_id,
                        "target": class_id,
                        "kind": "contains",
                        "line": line_num,
                        "col": line.find(class_name)
                    })
                    current_class_id = class_id
                    defined_symbols[class_name] = class_id
                    continue
                
            if first_word in ("END_CLASS", "END_FUNCTION_BLOCK", "END_PROGRAM"):
                if end_class_pat.match(cleaned):
                    current_class_id = None
                    continue
                
            m_method = None
            if first_word in ("METHOD", "FUNCTION"):
                m_method = method_pat.match(cleaned)
                if m_method:
                    method_name = m_method.group(1)
                    if method_name.lower() in ("if", "for", "while", "case", "of"):
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
                        "language": "structured_text",
                        "start_line": line_num,
                        "end_line": line_num,
                        "start_column": line.find(method_name),
                        "end_column": line.find(method_name) + len(method_name),
                        "docstring": "",
                        "signature": cleaned,
                        "visibility": "public"
                    })
                    
                    parent = current_class_id if current_class_id else file_node_id
                    edges.append({
                        "source": parent,
                        "target": method_id,
                        "kind": "contains",
                        "line": line_num,
                        "col": line.find(method_name)
                    })
                    defined_symbols[qual_name] = method_id
                    defined_symbols[method_name] = method_id
                    continue
                
            if first_word == "VAR" or first_word.startswith("VAR_"):
                var_block = True
                continue
            if first_word == "END_VAR":
                var_block = False
                continue
                
            if var_block and ":" in cleaned:
                parts = cleaned.split(":")
                var_name = parts[0].strip()
                var_name = var_name.split()[-1]
                if re.match(r'^[a-zA-Z0-9_]+$', var_name):
                    var_id = f"{relative_path}::Variable::{var_name}"
                    nodes.append({
                        "id": var_id,
                        "kind": "variable",
                        "name": var_name,
                        "qualified_name": var_name,
                        "file_path": relative_path,
                        "language": "structured_text",
                        "start_line": line_num,
                        "end_line": line_num,
                        "start_column": line.find(var_name),
                        "end_column": line.find(var_name) + len(var_name),
                        "docstring": f"Type: {parts[1].strip().rstrip(';')}",
                        "signature": cleaned,
                        "visibility": "public"
                    })
                    parent = current_class_id if current_class_id else file_node_id
                    edges.append({
                        "source": parent,
                        "target": var_id,
                        "kind": "contains",
                        "line": line_num,
                        "col": line.find(var_name)
                    })
                    defined_symbols[var_name] = var_id
                    
        return nodes, edges, defined_symbols
