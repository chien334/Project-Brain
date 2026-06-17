import os
import sys
import sqlite3
import hashlib
import time

# Import modular registry and parser classes from package
from .parsers import PARSER_REGISTRY, BaseParser, detect_lang, GenericParser

# Database setup
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
    
    cursor.execute(
        "INSERT INTO schema_versions (version, applied_at, description) VALUES (1, ?, 'Initial Pure-Python Schema');",
        (int(time.time()),)
    )
    
    conn.commit()
    conn.close()

def parse_project(root_path):
    exclude_dirs = {".git", "node_modules", "venv", ".venv", "__pycache__", "bin", "obj", ".vs", ".codegraph", "dist", "build", ".cache"}
    exclude_exts = {
        ".db", ".sqlite", ".sqlite3",
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg",
        ".pdf", ".zip", ".tar", ".gz", ".rar", ".7z",
        ".exe", ".bin", ".dll", ".pdb", ".so", ".dylib",
        ".woff", ".woff2", ".ttf", ".eot",
        ".cache", ".up2date", ".log",
        ".suo", ".user", ".map", ".lock",
        ".mp3", ".mp4", ".wav", ".avi", ".mov", ".flac", ".ogg",
        ".dll.config", ".exe.config", ".bak", ".tmp"
    }
    
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
                
                # Run modular parser
                parser_cls = PARSER_REGISTRY.get(lang)
                if parser_cls:
                    parser = parser_cls()
                    nodes, edges, defs = parser.parse(rel_path, code)
                else:
                    # Fallback to GenericParser for other textual codebases
                    parser = GenericParser(lang)
                    nodes, edges, defs = parser.parse(rel_path, code)
                    
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
