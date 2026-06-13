import sys
from pathlib import Path

# Add src to sys.path
sys.path.insert(0, str(Path("/Users/macbbook/SourceCodes/OpenMemory/src")))

from projectbrain.core.db import db, q

def main():
    db.connect()
    
    user_id = 'e-commert-jp-vn:%'
    print(f"Querying for: {user_id}")
    rows = q.all_mem_by_user(user_id, limit=30)
    print(f"Returned: {len(rows)} memories")
    for r in rows[:3]:
        print(dict(r))

    print("\nQuerying stats for: " + user_id)
    total_res = db.fetchone("SELECT count(*) as c FROM memories WHERE user_id LIKE ?", (user_id,))
    print(f"Total memories: {dict(total_res)}")
    
if __name__ == "__main__":
    main()
