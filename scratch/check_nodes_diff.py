import sqlite3

def main():
    db_path = "/Users/macbbook/SourceCodes/OpenMemory/projectbrain.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Fetch all for main
    cursor.execute("SELECT kind, qualified_name, file_path, signature, docstring, start_line, end_line FROM project_nodes WHERE project_id = ?;", ("e-commert-jp-vn:main",))
    main_nodes = [dict(r) for r in cursor.fetchall()]
    
    # Fetch all for feature/promotion
    cursor.execute("SELECT kind, qualified_name, file_path, signature, docstring, start_line, end_line FROM project_nodes WHERE project_id = ?;", ("e-commert-jp-vn:feature/promotion",))
    promo_nodes = [dict(r) for r in cursor.fetchall()]
    
    print(f"Main nodes: {len(main_nodes)}")
    print(f"Promotion nodes: {len(promo_nodes)}")
    
    main_map = {(n["kind"], n["qualified_name"], n["file_path"]): n for n in main_nodes}
    promo_map = {(n["kind"], n["qualified_name"], n["file_path"]): n for n in promo_nodes}
    
    added = [k for k in promo_map if k not in main_map]
    deleted = [k for k in main_map if k not in promo_map]
    
    modified = []
    for k, pn in promo_map.items():
        if k in main_map:
            mn = main_map[k]
            diffs = []
            if mn["signature"] != pn["signature"]: diffs.append("signature")
            if mn["docstring"] != pn["docstring"]: diffs.append("docstring")
            if mn["start_line"] != pn["start_line"] or mn["end_line"] != pn["end_line"]: diffs.append("lines")
            if diffs:
                modified.append((k, diffs, mn, pn))
                
    print(f"Added: {len(added)}")
    print(f"Deleted: {len(deleted)}")
    print(f"Modified: {len(modified)}")
    
    if modified:
        print("\nFirst 3 modified nodes sample:")
        for k, diffs, mn, pn in modified[:3]:
            print(f"Key: {k}")
            print(f"  Diffs: {diffs}")
            print(f"  Main: signature={repr(mn['signature'])} lines={mn['start_line']}-{mn['end_line']}")
            print(f"  Promo: signature={repr(pn['signature'])} lines={pn['start_line']}-{pn['end_line']}")
            
    conn.close()

if __name__ == "__main__":
    main()
