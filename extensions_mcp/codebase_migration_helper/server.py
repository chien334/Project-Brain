import os
import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from typing import Annotated

mcp = FastMCP("codebase-migration-helper")

# Helper to call Gemini API directly without SDK version mismatch issues
async def call_gemini(prompt: str, system_instruction: str = None) -> str:
    api_key = os.getenv("LLM_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("Missing LLM_API_KEY or GEMINI_API_KEY in environment.")
        
    primary_model = os.getenv("LLM_MODEL", "gemma-4-26b-a4b-it")
    model_chain = [primary_model, "gemini-1.5-flash", "gemini-2.5-flash", "gemma-4-26b-a4b-it"]
    seen = set()
    models_to_try = []
    for m in model_chain:
        if m not in seen:
            seen.add(m)
            models_to_try.append(m)
            
    last_err = None
    for model in models_to_try:
        if "models/" not in model:
            model_path = f"models/{model}"
        else:
            model_path = model
            
        url = f"https://generativelanguage.googleapis.com/v1beta/{model_path}:generateContent?key={api_key}"
        
        contents = []
        if system_instruction:
            contents.append({"role": "user", "parts": [{"text": f"System Instruction: {system_instruction}"}]})
        contents.append({"role": "user", "parts": [{"text": prompt}]})
        
        req_body = {"contents": contents}
        
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                res = await client.post(url, json=req_body)
                if res.status_code == 200:
                    data = res.json()
                    return data["candidates"][0]["content"]["parts"][0]["text"]
                else:
                    last_err = f"API Error {res.status_code} for {model}: {res.text}"
        except Exception as e:
            last_err = f"HTTP Error for {model}: {str(e)}"
            
    raise RuntimeError(f"All models in fallback chain failed. Last error: {last_err}")

@mcp.tool(name="migration_translate_code_comments")
async def translate_code_comments(
    file_path: Annotated[str, Field(description="Path to the source file to translate comments")],
    direction: Annotated[str, Field(description="Translation direction: 'ja2en' or 'en2ja'")] = "ja2en"
) -> dict:
    """Scans a code file, extracts and translates comments and docstrings, and returns the translated code.
    
    Perfect for maintaining legacy Japanese codebases by translating all comments to English.
    """
    path = os.path.abspath(os.path.expanduser(file_path))
    if not os.path.isfile(path):
        return {"error": f"File not found: {file_path}"}
        
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            code_content = f.read()
            
        system_prompt = (
            "You are an expert software engineer specialized in Japanese legacy systems migration. "
            "Your task is to take the provided source code, identify all comments, docstrings, and "
            "embedded documentation in Japanese, and translate them to clear developer-friendly English. "
            "Keep the program logic, variables, syntax, and functionality EXACTLY the same. "
            "Return ONLY the updated source code. Do not wrap in markdown quotes, and do not explain."
        )
        if direction == "en2ja":
            system_prompt = system_prompt.replace("Japanese", "English").replace("English", "Japanese")
            
        translated_code = await call_gemini(code_content, system_prompt)
        
        # Clean markdown code block wraps if LLM added them
        if translated_code.startswith("```"):
            lines = translated_code.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines[-1].startswith("```"):
                lines = lines[:-1]
            translated_code = "\n".join(lines)
            
        return {
            "file": file_path,
            "direction": direction,
            "original_size": len(code_content),
            "translated_size": len(translated_code),
            "translated_code": translated_code
        }
    except Exception as e:
        return {
            "error": f"Failed to translate: {e}",
            "fallback_to_client": True,
            "system_prompt": system_prompt if 'system_prompt' in locals() else None,
            "prompt": code_content if 'code_content' in locals() else None,
            "message": (
                "The server-side LLM call failed. Please perform this translation yourself "
                "using your own model capabilities ('current model'). Translate all Japanese comments "
                "to English (or vice versa if specified) in the provided code, maintaining logic exactly, "
                "and output the complete translated code."
            )
        }

@mcp.tool(name="migration_recommend_refactor")
async def recommend_refactor(
    file_path: Annotated[str, Field(description="Path to the legacy file to refactor")],
    tech_stack: Annotated[str, Field(description="Target modern tech stack (e.g. 'Spring Boot', 'React', 'FastAPI')")]
) -> dict:
    """Analyzes a legacy source file and recommends modern tech-stack refactoring based on industry migration standards.
    
    Helpful for migrating legacy tech (like ASP.NET WebForms, Struts, VB6) to modern API-driven architectures.
    """
    path = os.path.abspath(os.path.expanduser(file_path))
    if not os.path.isfile(path):
        return {"error": f"File not found: {file_path}"}
        
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            code_content = f.read()
            
        prompt = (
            f"Here is a legacy source file from {file_path}:\n\n"
            f"```\n{code_content}\n```\n\n"
            f"Please write a migration recommendations report to convert this module to the modern stack: '{tech_stack}'.\n"
            f"Include:\n"
            f"1. A structural analysis of the legacy code (dependencies, patterns used).\n"
            f"2. Recommendations for mapping legacy constructs (e.g. struts actions, inline SQL) to modern constructs (e.g. Spring Controllers, JPA repositories).\n"
            f"3. Concrete code snippet examples showing the migrated structure in '{tech_stack}'.\n"
            f"4. A test strategy to prevent regressions."
        )
        
        system_prompt = (
            "You are a principal software migration architect. "
            "Write a structured, technical, and detailed markdown migration recommendation report."
        )
        
        report = await call_gemini(prompt, system_prompt)
        
        return {
            "file": file_path,
            "target_stack": tech_stack,
            "migration_report": report
        }
    except Exception as e:
        return {
            "error": f"Failed to generate recommendations: {e}",
            "fallback_to_client": True,
            "system_prompt": system_prompt if 'system_prompt' in locals() else None,
            "prompt": prompt if 'prompt' in locals() else None,
            "message": (
                "The server-side LLM call failed. Please perform this refactoring analysis yourself "
                "using your own model capabilities ('current model'). Generate a structured, technical, "
                "and detailed markdown migration recommendation report for the provided code."
            )
        }

@mcp.tool(name="migration_batch_scan_logic")
async def batch_scan_logic(
    project_path: Annotated[str, Field(description="Root path to the legacy project workspace")],
    project_id: Annotated[str, Field(description="Project ID to scope the scan inside RAG memory")]
) -> dict:
    """Recursively batch-scans a codebase, analyzes each function's business logic using Gemini, and saves a JSON draft.
    
    This is extremely useful to extract implicit business rules from COBOL, VB6, or modern files.
    """
    try:
        from .batch_scan_helper import run_batch_scan_logic
        res = await run_batch_scan_logic(project_path, project_id)
        return res
    except Exception as e:
        return {"error": f"Failed to run batch scan logic: {str(e)}"}

@mcp.tool(name="migration_plan_execution_phases")
async def plan_execution_phases(
    project_path: Annotated[str, Field(description="Root path to the legacy project workspace")],
    max_phases: Annotated[int, Field(description="Maximum number of phases to split into")] = 5
) -> dict:
    """Analyzes drafts and plans sequential refactoring/migration phases.
    
    Perfect to partition the codebase into small execution waves.
    """
    try:
        from .batch_scan_helper import run_phase_planning
        res = await run_phase_planning(project_path, max_phases)
        return res
    except Exception as e:
        # If the API fails completely, support the fallback mechanism
        # Find drafts path to construct fallback prompts if possible
        import json
        drafts_data = []
        try:
            path = os.path.abspath(os.path.expanduser(project_path))
            drafts_path = os.path.join(path, ".planning", "business_logic_drafts.json")
            if os.path.exists(drafts_path):
                with open(drafts_path, "r", encoding="utf-8") as f:
                    drafts_data = json.load(f)
        except Exception:
            pass
            
        functions_summary = []
        for item in drafts_data:
            functions_summary.append({
                "node_id": item.get("node_id"),
                "function_name": item.get("function_name"),
                "file_path": item.get("file_path"),
                "signature": item.get("signature"),
                "business_logic_summary": (item.get("business_logic_draft") or "")[:200]
            })
            
        system_prompt = (
            "You are a principal software migration architect. "
            "Your task is to partition a list of legacy codebase functions into logical, "
            "sequential refactoring phases based on complexity and dependency ordering."
        )
        
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
        
        return {
            "error": f"Failed to plan phases: {e}",
            "fallback_to_client": True,
            "system_prompt": system_prompt,
            "prompt": prompt,
            "message": (
                "The server-side LLM call failed. Please partition these functions into logical refactoring phases yourself "
                "using your own model capabilities ('current model'). Create both `.planning/migration_phases.json` "
                "and `.planning/migration_phases.md` in the project directory based on the output schema."
            )
        }
