import asyncio
import httpx

async def main():
    api_key = "sk-yKYtlrDAXEGvxl0g_mcNRA"
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://openmemory.org",
        "X-Title": "OpenMemory Control Panel"
    }
    
    async with httpx.AsyncClient() as client:
        res = await client.post(url, json={
            "model": "google/gemini-3.1-pro-preview",
            "messages": [{"role": "user", "content": "hi"}],
        }, headers=headers)
        
        print("Status Code:", res.status_code)
        print("Headers:", res.headers)
        print("Body:", res.text)

if __name__ == "__main__":
    asyncio.run(main())
