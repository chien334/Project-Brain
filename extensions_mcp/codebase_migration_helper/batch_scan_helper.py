import os
import sys
import json
import sqlite3
import httpx
import asyncio

# Setup path to import projectbrain modules if needed
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

def get_project_functions(db_path, project_id):
    """Retrieves all function and method nodes from the codegraph database for a project."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Query function and method nodes
    cursor.execute("""
        SELECT id, kind, name, qualified_name, file_path, start_line, end_line, docstring, signature 
        FROM nodes 
        WHERE kind IN ('function', 'method')
        ORDER BY file_path ASC, start_line ASC;
    """)
    rows = cursor.fetchall()
    
    functions = []
    for r in rows:
        functions.append({
            "id": r[0],
            "kind": r[1],
            "name": r[2],
            "qualified_name": r[3],
            "file_path": r[4],
            "start_line": r[5],
            "end_line": r[6],
            "docstring": r[7],
            "signature": r[8]
        })
        
    conn.close()
    return functions

def extract_function_code(project_root, file_path, start_line, end_line):
    """Reads the source code lines corresponding to a specific function node."""
    abs_path = os.path.join(project_root, file_path)
    if not os.path.exists(abs_path):
        return None
        
    try:
        with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        # lines are 0-indexed, start/end_line are 1-indexed
        target_lines = lines[max(0, start_line - 1):min(len(lines), end_line)]
        return "".join(target_lines)
    except Exception as e:
        return f"Error reading file: {str(e)}"

async def analyze_business_logic(api_key, model, code_snippet, file_path, function_name, language):
    """Calls Gemini API to analyze the business logic of a single function snippet."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    
    prompt = (
        f"Analyze the legacy business logic for the following function in '{file_path}':\n\n"
        f"Language: {language}\n"
        f"Function Name: {function_name}\n\n"
        f"```\n{code_snippet}\n```\n\n"
        f"Please write a structured business logic report following this template exactly:\n"
        f"1. **Tên nghiệp vụ (Business Name)**: [Clear Vietnamese business name for this function]\n"
        f"2. **Mô tả chức năng (Functional Description)**: [Explain what this function does in plain language]\n"
        f"3. **Luồng xử lý (Processing Flow)**: [Step-by-step logic breakdown]\n"
        f"4. **Quy tắc nghiệp vụ (Business Rules & Constraints)**: [Any validation, conditions, calculations, or constraints]\n"
        f"5. **Tham số & Kết quả (Inputs/Outputs)**: [Describe parameters and return values]"
    )
    
    system_instruction = (
        "You are an expert systems modernization architect and business analyst. "
        "Your task is to extract clear, domain-specific, and developer-friendly business rules "
        "and logical constraints from legacy source code snippets. Write your output in Vietnamese."
    )
    
    req_body = {
        "contents": [
            {"role": "user", "parts": [{"text": f"System Instruction: {system_instruction}"}]},
            {"role": "user", "parts": [{"text": prompt}]}
        ]
    }
    
    async with httpx.AsyncClient(timeout=120.0) as client:
        res = await client.post(url, json=req_body)
        if res.status_code != 200:
            return f"Error analyzing logic: {res.text}"
        data = res.json()
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError):
            return f"Error parsing API response: {json.dumps(data)}"

async def main():
    if len(sys.argv) < 3:
        print("Usage: python3 batch_scan_helper.py <project_root_directory> <project_id>")
        print("Example: python3 batch_scan_helper.py /path/to/legacy-project my-legacy-project")
        sys.exit(1)
        
    project_root = os.path.abspath(sys.argv[1])
    project_id = sys.argv[2]
    
    db_path = os.path.join(project_root, ".codegraph", "codegraph.db")
    if not os.path.exists(db_path):
        print(f"Error: Codegraph database not found at {db_path}.")
        print("Please run 'python3 -m projectbrain.main codegraph-sync' or equivalent to build it first.")
        sys.exit(1)
        
    functions = get_project_functions(db_path, project_id)
    if not functions:
        print("No function or method nodes found in the database. Scan completed.")
        sys.exit(0)
        
    print(f"Found {len(functions)} function/method nodes. Preparing draft templates...")
    
    api_key = os.getenv("LLM_API_KEY") or os.getenv("GEMINI_API_KEY")
    model = os.getenv("LLM_MODEL", "gemini-1.5-flash")
    
    drafts = []
    
    for idx, fn in enumerate(functions):
        code = extract_function_code(project_root, fn["file_path"], fn["start_line"], fn["end_line"])
        if not code:
            continue
            
        print(f"[{idx+1}/{len(functions)}] Preparing draft for {fn['file_path']} -> {fn['name']}...")
        
        draft_item = {
            "node_id": fn["id"],
            "function_name": fn["name"],
            "file_path": fn["file_path"],
            "signature": fn["signature"],
            "code_snippet": code,
            "business_logic_draft": "",
            "status": "pending_review"
        }
        
        # If API key is available, we can optionally pre-populate the draft
        if api_key:
            ext = os.path.splitext(fn["file_path"])[1]
            try:
                logic_md = await analyze_business_logic(
                    api_key, model, code, fn["file_path"], fn["name"], ext
                )
                draft_item["business_logic_draft"] = logic_md
            except Exception as e:
                draft_item["business_logic_draft"] = f"Failed to generate draft: {str(e)}"
        else:
            draft_item["business_logic_draft"] = "API key not set. Please review and fill business logic manually."
            
        drafts.append(draft_item)
        
    output_path = os.path.join(project_root, ".planning", "business_logic_drafts.json")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(drafts, f, indent=2, ensure_ascii=False)
        
    print(f"\nSuccessfully generated {len(drafts)} drafts at: {output_path}")
    print("Kỹ sư có thể chỉnh sửa tệp JSON này trực tiếp hoặc qua Dashboard trước khi gửi 'projectbrain_store' để lưu vào bộ nhớ chung.")

if __name__ == "__main__":
    asyncio.run(main())
