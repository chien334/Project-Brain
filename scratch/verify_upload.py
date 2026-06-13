import httpx
import asyncio

async def test_upload():
    # 1. Create a dummy txt file
    file_content = b"This is a test spec file for Project XYZ.\nIt defines how elements relate to each other."
    files = {'file': ('test_spec.txt', file_content, 'text/plain')}
    data = {'project_id': 'test-proj:v1', 'tags': 'spec,test'}
    
    async with httpx.AsyncClient() as client:
        resp = await client.post("http://localhost:8080/sources/upload", files=files, data=data)
        print("UPLOAD RESPONSE:")
        print(resp.status_code)
        print(resp.json())

if __name__ == "__main__":
    asyncio.run(test_upload())
