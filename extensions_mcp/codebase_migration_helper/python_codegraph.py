import os
import sys
import re
import ast
import sqlite3
import hashlib
import time

def setup_db(db_path):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except Exception:
            pass
            
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS schema_versions (
        version INTEGER PRIMARY KEY,
        applied_at INTEGER NOT NULL,
        description TEXT
    );
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS files (
        path TEXT PRIMARY KEY,
        content_hash TEXT NOT NULL,
        language TEXT NOT NULL,
        size INTEGER NOT NULL,
        modified_at INTEGER NOT NULL,
        indexed_at INTEGER NOT NULL,
        node_count INTEGER DEFAULT 0,
        errors TEXT
    );
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS nodes (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        name TEXT NOT NULL,
        qualified_name TEXT NOT NULL,
        file_path TEXT NOT NULL,
        language TEXT NOT NULL,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        start_column INTEGER NOT NULL,
        end_column INTEGER NOT NULL,
        docstring TEXT,
        signature TEXT,
        visibility TEXT,
        is_exported INTEGER DEFAULT 0,
        is_async INTEGER DEFAULT 0,
        is_static INTEGER DEFAULT 0,
        is_abstract INTEGER DEFAULT 0,
        decorators TEXT,
        type_parameters TEXT,
        updated_at INTEGER NOT NULL
    );
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS edges (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL,
        target TEXT NOT NULL,
        kind TEXT NOT NULL,
        metadata TEXT,
        line INTEGER,
        col INTEGER,
        provenance TEXT DEFAULT NULL,
        FOREIGN KEY (source) REFERENCES nodes(id) ON DELETE CASCADE,
        FOREIGN KEY (target) REFERENCES nodes(id) ON DELETE CASCADE
    );
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS project_metadata (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at INTEGER NOT NULL
    );
    """)
    
    # Insert initial schema version
    cursor.execute(
        "INSERT INTO schema_versions (version, applied_at, description) VALUES (1, ?, 'Initial Pure-Python Schema');",
        (int(time.time()),)
    )
    
    conn.commit()
    conn.close()

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

def parse_regex(relative_path, code, lang):
    nodes = []
    edges = []
    defined_symbols = {}
    
    file_node_id = f"{relative_path}::File"
    lines = code.split("\n")
    
    if lang == "cobol":
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
                
    elif lang == "vb6":
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
                
    elif lang == "pascal":
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
                
    elif lang == "fortran":
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
                
    else:
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
                    "language": lang,
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
                    "language": lang,
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

def detect_lang(file_path):
    ext = os.path.splitext(file_path)[1].lower()
    mapping = {
        ".py": "python",
        ".java": "java",
        ".cs": "csharp",
        ".js": "javascript",
        ".ts": "typescript",
        ".go": "go",
        ".cpp": "cpp",
        ".h": "cpp",
        ".c": "c",
        ".rb": "ruby",
        ".php": "php",
        ".cbl": "cobol",
        ".cob": "cobol",
        ".bas": "vb6",
        ".cls": "vb6",
        ".frm": "vb6",
        ".vbs": "vb6",
        ".pas": "pascal",
        ".dpr": "pascal",
        ".f": "fortran",
        ".for": "fortran",
        ".f90": "fortran"
    }
    return mapping.get(ext, "text")

def parse_project(root_path):
    exclude_dirs = {".git", "node_modules", "venv", ".venv", "__pycache__", "bin", "obj", ".vs", ".codegraph", "dist", "build"}
    exclude_exts = {".db", ".sqlite", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg", ".pdf", ".zip", ".tar", ".gz", ".rar", ".exe", ".bin", ".dll", ".so", ".dylib"}
    
    all_nodes = []
    all_edges = []
    global_defined_symbols = {}
    
    indexed_files = []
    
    print(f"Recursively parsing codebase in: {root_path}")
    
    for dirpath, dirnames, filenames in os.walk(root_path):
        dirnames[:] = [d for d in dirnames if d not in exclude_dirs]
        
        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            if ext in exclude_exts:
                continue
                
            abs_path = os.path.join(dirpath, fname)
            rel_path = os.path.relpath(abs_path, root_path)
            lang = detect_lang(fname)
            
            try:
                with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
                    code = f.read()
                    
                size = len(code)
                mtime = int(os.path.getmtime(abs_path))
                chash = hashlib.sha256(code.encode("utf-8", errors="ignore")).hexdigest()
                
                # File node
                file_node_id = f"{rel_path}::File"
                all_nodes.append({
                    "id": file_node_id,
                    "kind": "file",
                    "name": fname,
                    "qualified_name": rel_path,
                    "file_path": rel_path,
                    "language": lang,
                    "start_line": 1,
                    "end_line": len(code.split("\n")),
                    "start_column": 0,
                    "end_column": 0,
                    "docstring": "",
                    "signature": f"file {rel_path}",
                    "visibility": "public"
                })
                
                if lang == "python":
                    parser = PythonASTParser(rel_path, code)
                    parser.parse()
                    nodes = parser.nodes
                    edges = parser.edges
                    defs = parser.defined_symbols
                else:
                    nodes, edges, defs = parse_regex(rel_path, code, lang)
                    
                all_nodes.extend(nodes)
                all_edges.extend(edges)
                
                # Register definitions
                for name, node_id in defs.items():
                    global_defined_symbols[name] = node_id
                    
                indexed_files.append({
                    "path": rel_path,
                    "content_hash": chash,
                    "language": lang,
                    "size": size,
                    "modified_at": mtime,
                    "indexed_at": int(time.time()),
                    "node_count": len(nodes) + 1
                })
                
            except Exception as e:
                print(f"Failed to parse {rel_path}: {e}")
                
    # Resolve reference edges
    resolved_edges = []
    for edge in all_edges:
        if "target" in edge:
            resolved_edges.append(edge)
        elif "target_name" in edge:
            t_name = edge["target_name"]
            if t_name in global_defined_symbols:
                resolved_edges.append({
                    "source": edge["source"],
                    "target": global_defined_symbols[t_name],
                    "kind": edge["kind"],
                    "line": edge["line"],
                    "col": edge["col"]
                })
                
    return indexed_files, all_nodes, resolved_edges

def main(project_dir=None):
    if not project_dir:
        project_dir = os.getcwd()
        
    db_path = os.path.join(project_dir, ".codegraph", "codegraph.db")
    setup_db(db_path)
    
    files, nodes, edges = parse_project(project_dir)
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Insert files
    for f in files:
        cursor.execute(
            "INSERT OR IGNORE INTO files (path, content_hash, language, size, modified_at, indexed_at, node_count) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (f["path"], f["content_hash"], f["language"], f["size"], f["modified_at"], f["indexed_at"], f["node_count"])
        )
        
    # Insert nodes
    ts = int(time.time())
    for n in nodes:
        cursor.execute(
            """
            INSERT OR IGNORE INTO nodes 
            (id, kind, name, qualified_name, file_path, language, start_line, end_line, start_column, end_column, docstring, signature, visibility, updated_at) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (n["id"], n["kind"], n["name"], n["qualified_name"], n["file_path"], n["language"], n["start_line"], n["end_line"], n["start_column"], n["end_column"], n["docstring"], n["signature"], n["visibility"], ts)
        )
        
    # Insert edges
    for e in edges:
        cursor.execute(
            "INSERT OR IGNORE INTO edges (source, target, kind, line, col) VALUES (?, ?, ?, ?, ?)",
            (e["source"], e["target"], e["kind"], e["line"], e["col"])
        )
        
    # Insert project metadata
    cursor.execute(
        "INSERT INTO project_metadata (key, value, updated_at) VALUES (?, ?, ?)",
        ("project_name", os.path.basename(project_dir), ts)
    )
    
    conn.commit()
    conn.close()
    
    print(f"Successfully generated codegraph database with {len(nodes)} nodes and {len(edges)} edges at: {db_path}")

if __name__ == "__main__":
    p_dir = sys.argv[1] if len(sys.argv) > 1 else None
    main(p_dir)
