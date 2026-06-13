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
    primary_model = model or os.getenv("LLM_MODEL", "gemma-4-26b-a4b-it")
    model_chain = [primary_model, "gemini-1.5-flash", "gemini-2.5-flash", "gemma-4-26b-a4b-it"]
    seen = set()
    models_to_try = []
    for m in model_chain:
        if m not in seen:
            seen.add(m)
            models_to_try.append(m)

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
    
    last_err = None
    for model_name in models_to_try:
        if "models/" not in model_name:
            model_path = f"models/{model_name}"
        else:
            model_path = model_name
            
        url = f"https://generativelanguage.googleapis.com/v1beta/{model_path}:generateContent?key={api_key}"
        
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                res = await client.post(url, json=req_body)
                if res.status_code == 200:
                    data = res.json()
                    try:
                        return data["candidates"][0]["content"]["parts"][0]["text"]
                    except (KeyError, IndexError):
                        last_err = f"Parsing candidates failed for {model_name}: {json.dumps(data)}"
                else:
                    last_err = f"API Error {res.status_code} for {model_name}: {res.text}"
        except Exception as e:
            last_err = f"HTTP Error for {model_name}: {str(e)}"
            
    raise RuntimeError(f"All models in fallback chain failed. Last error: {last_err}")

async def run_batch_scan_logic(project_root, project_id):
    project_root = os.path.abspath(project_root)
    db_path = os.path.join(project_root, ".codegraph", "codegraph.db")
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Codegraph database not found at {db_path}.")
        
    functions = get_project_functions(db_path, project_id)
    if not functions:
        return {"status": "success", "processed": 0, "message": "No functions found to scan."}
        
    api_key = os.getenv("LLM_API_KEY") or os.getenv("GEMINI_API_KEY")
    model = os.getenv("LLM_MODEL", "gemma-4-26b-a4b-it")
    
    drafts = []
    for fn in functions:
        code = extract_function_code(project_root, fn["file_path"], fn["start_line"], fn["end_line"])
        if not code:
            continue
            
        draft_item = {
            "node_id": fn["id"],
            "function_name": fn["name"],
            "file_path": fn["file_path"],
            "signature": fn["signature"],
            "code_snippet": code,
            "business_logic_draft": "",
            "status": "pending_review"
        }
        
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
        
    failed_items = [
        d for d in drafts 
        if d["business_logic_draft"].startswith("Failed to generate draft:") 
        or d["business_logic_draft"].startswith("API key not set")
    ]
    
    if failed_items:
        return {
            "status": "partial_success_fallback_needed",
            "processed": len(drafts),
            "output_file": output_path,
            "failed_items": failed_items,
            "message": (
                f"Processed {len(drafts)} functions. Some items failed to generate business logic due to LLM errors. "
                "Please use your own model capabilities ('model hiện tại') to analyze the code snippets for the failed items, "
                "and update the draft file directly."
            )
        }
        
    return {
        "status": "success",
        "processed": len(drafts),
        "output_file": output_path
    }

async def run_phase_planning(project_root: str, max_phases: int = 5) -> dict:
    project_root = os.path.abspath(project_root)
    drafts_path = os.path.join(project_root, ".planning", "business_logic_drafts.json")
    if not os.path.exists(drafts_path):
        raise FileNotFoundError(f"Business logic drafts not found at {drafts_path}. Please run batch scan first.")
        
    with open(drafts_path, "r", encoding="utf-8") as f:
        drafts = json.load(f)
        
    if not drafts:
        return {"status": "success", "phases_count": 0, "message": "No drafts found to plan."}
        
    functions_summary = []
    for item in drafts:
        functions_summary.append({
            "node_id": item.get("node_id"),
            "function_name": item.get("function_name"),
            "file_path": item.get("file_path"),
            "signature": item.get("signature"),
            "business_logic_summary": (item.get("business_logic_draft") or "")[:200]
        })
        
    prompt = (
        f"Below is a list of functions from the legacy project with their business logic drafts. "
        f"Please group these functions into sequential migration/refactoring phases (maximum {max_phases} phases). "
        f"Ensure that dependencies are respected: utility and helper functions should be in earlier phases, "
        f"core business logic in middle phases, and API entrypoints or user interfaces in the final phases.\n\n"
        f"Functions List:\n{json.dumps(functions_summary, indent=2, ensure_ascii=False)}\n\n"
        f"Please output a structured JSON response matching this schema exactly:\n"
        f"{{\n"
        f"  \"phases\": [\n"
        f"    {{\n"
        f"      \"phase_number\": 1,\n"
        f"      \"name\": \"Phase Name\",\n"
        f"      \"description\": \"Description of this phase\",\n"
        f"      \"complexity\": \"low|medium|high\",\n"
        f"      \"functions\": [\n"
        f"        {{\n"
        f"          \"node_id\": 1,\n"
        f"          \"name\": \"function_name\",\n"
        f"          \"file_path\": \"file_path\"\n"
        f"        }}\n"
        f"      ]\n"
        f"    }}\n"
        f"  ]\n"
        f"}}\n\n"
        f"Return ONLY valid JSON. Do not include markdown code block wraps."
    )
    
    system_instruction = (
        "You are a principal software migration architect. "
        "Your task is to partition a list of legacy codebase functions into logical, "
        "sequential refactoring phases based on complexity and dependency ordering."
    )
    
    api_key = os.getenv("LLM_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("API key not set. Cannot run phase planning.")
        
    primary_model = os.getenv("LLM_MODEL", "gemma-4-26b-a4b-it")
    model_chain = [primary_model, "gemini-1.5-flash", "gemini-2.5-flash", "gemma-4-26b-a4b-it"]
    seen = set()
    models_to_try = []
    for m in model_chain:
        if m not in seen:
            seen.add(m)
            models_to_try.append(m)
            
    req_body = {
        "contents": [
            {"role": "user", "parts": [{"text": f"System Instruction: {system_instruction}"}]},
            {"role": "user", "parts": [{"text": prompt}]}
        ]
    }
    
    raw_result = None
    last_err = None
    for model_name in models_to_try:
        if "models/" not in model_name:
            model_path = f"models/{model_name}"
        else:
            model_path = model_name
            
        url = f"https://generativelanguage.googleapis.com/v1beta/{model_path}:generateContent?key={api_key}"
        
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                res = await client.post(url, json=req_body)
                if res.status_code == 200:
                    data = res.json()
                    try:
                        raw_result = data["candidates"][0]["content"]["parts"][0]["text"]
                        break
                    except (KeyError, IndexError):
                        last_err = f"Parsing candidates failed for {model_name}: {json.dumps(data)}"
                else:
                    last_err = f"API Error {res.status_code} for {model_name}: {res.text}"
        except Exception as e:
            last_err = f"HTTP Error for {model_name}: {str(e)}"
            
    if not raw_result:
        raise RuntimeError(f"All models in fallback chain failed. Last error: {last_err}")
        
    clean_result = raw_result.strip()
    if clean_result.startswith("```"):
        lines = clean_result.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines[-1].startswith("```"):
            lines = lines[:-1]
        clean_result = "\n".join(lines).strip()
        
    phases_data = json.loads(clean_result)
    
    # Save structured JSON
    output_json_path = os.path.join(project_root, ".planning", "migration_phases.json")
    os.makedirs(os.path.dirname(output_json_path), exist_ok=True)
    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(phases_data, f, indent=2, ensure_ascii=False)
        
    # Generate and save human-readable Markdown report
    md_lines = [
        "# Kế hoạch các Giai đoạn Di trú (Migration Phase Plan)",
        "Kế hoạch phân rã và chia phase thực thi cho dự án di trú mã nguồn kế thừa.",
        ""
    ]
    for phase in phases_data.get("phases", []):
        p_num = phase.get("phase_number", "?")
        p_name = phase.get("name", "Unnamed Phase")
        p_desc = phase.get("description", "")
        p_complexity = phase.get("complexity", "medium")
        
        md_lines.append(f"## Phase {p_num}: {p_name}")
        md_lines.append(f"- **Độ phức tạp / Rủi ro**: {p_complexity.upper()}")
        md_lines.append(f"- **Mô tả**: {p_desc}")
        md_lines.append("- **Danh sách các hàm di trú:**")
        for fn in phase.get("functions", []):
            md_lines.append(f"  - `{fn.get('name')}` trong `{fn.get('file_path')}` (Node ID: {fn.get('node_id')})")
        md_lines.append("")
        
    output_md_path = os.path.join(project_root, ".planning", "migration_phases.md")
    with open(output_md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))
        
    return {
        "status": "success",
        "output_json": output_json_path,
        "output_md": output_md_path,
        "phases_count": len(phases_data.get("phases", []))
    }

async def main():
    if len(sys.argv) < 3:
        print("Usage: python3 batch_scan_helper.py <project_root_directory> <project_id>")
        print("Example: python3 batch_scan_helper.py /path/to/legacy-project my-legacy-project")
        sys.exit(1)
        
    try:
        res = await run_batch_scan_logic(sys.argv[1], sys.argv[2])
        print(f"Batch scan finished. Processed {res['processed']} functions. Output: {res.get('output_file')}")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
