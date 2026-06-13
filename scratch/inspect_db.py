import sqlite3

def main():
    db_path = "/Users/macbbook/SourceCodes/OpenMemory/projectbrain.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    print("--- TABLES ---")
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [row['name'] for row in cursor.fetchall()]
    for table in tables:
        try:
            cursor.execute(f"SELECT COUNT(*) as count FROM {table};")
            row = cursor.fetchone()
            print(f"Table '{table}': {row['count']} rows")
        except Exception as e:
            print(f"Table '{table}' failed: {e}")

    print("\n--- PROJECTS ---")
    try:
        cursor.execute("SELECT id, name, project_path, sync_author, sync_ip FROM projects;")
        for row in cursor.fetchall():
            print(dict(row))
    except Exception as e:
        print("Error fetching projects:", e)

    print("\n--- DISTINCT USER_IDS (MEMORIES) ---")
    try:
        cursor.execute("SELECT user_id, COUNT(*) as count FROM memories GROUP BY user_id;")
        for row in cursor.fetchall():
            print(dict(row))
    except Exception as e:
        print("Error fetching memories user_ids:", e)

    print("\n--- CODESYMBOLS / FILES COUNT BY PROJECT ---")
    try:
        cursor.execute("SELECT project_id, COUNT(*) as count FROM project_nodes GROUP BY project_id;")
        for row in cursor.fetchall():
            print(dict(row))
    except Exception as e:
        print("Error fetching nodes:", e)

    conn.close()

if __name__ == "__main__":
    main()
