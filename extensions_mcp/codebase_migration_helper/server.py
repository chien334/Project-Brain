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
        
    model = os.getenv("LLM_MODEL", "gemini-1.5-flash")
    if "models/" not in model:
        model = f"models/{model}"
        
    url = f"https://generativelanguage.googleapis.com/v1beta/{model}:generateContent?key={api_key}"
    
    contents = []
    if system_instruction:
        contents.append({"role": "user", "parts": [{"text": f"System Instruction: {system_instruction}"}]})
    contents.append({"role": "user", "parts": [{"text": prompt}]})
    
    req_body = {"contents": contents}
    
    async with httpx.AsyncClient(timeout=120.0) as client:
        res = await client.post(url, json=req_body)
        if res.status_code != 200:
            raise RuntimeError(f"Gemini API Error {res.status_code}: {res.text}")
        data = res.json()
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError):
            raise RuntimeError(f"Unexpected response format from Gemini API: {data}")

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
        return {"error": f"Failed to translate: {e}"}

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
        return {"error": f"Failed to generate recommendations: {e}"}
