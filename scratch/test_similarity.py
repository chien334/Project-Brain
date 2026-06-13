import sys
import asyncio
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path("/Users/macbbook/SourceCodes/OpenMemory/src")))
from projectbrain.ai.synthetic import SyntheticAdapter

async def main():
    adapter = SyntheticAdapter(1536) # using 1536 dimension as per config default
    
    q_vec = await adapter.embed("codebase")
    d_vec1 = await adapter.embed("This is ecommerce codebase on main branch. We are using React and Mantine UI.")
    d_vec2 = await adapter.embed("This is ecommerce codebase on feature-x branch. We added PostgreSQL database support here.")
    d_vec3 = await adapter.embed("This is a completely different project codebase.")
    
    qv = np.array(q_vec)
    dv1 = np.array(d_vec1)
    dv2 = np.array(d_vec2)
    dv3 = np.array(d_vec3)
    
    sim1 = np.dot(qv, dv1) / (np.linalg.norm(qv) * np.linalg.norm(dv1))
    sim2 = np.dot(qv, dv2) / (np.linalg.norm(qv) * np.linalg.norm(dv2))
    sim3 = np.dot(qv, dv3) / (np.linalg.norm(qv) * np.linalg.norm(dv3))
    
    print(f"Similarity 1: {sim1:.4f}")
    print(f"Similarity 2: {sim2:.4f}")
    print(f"Similarity 3: {sim3:.4f}")

if __name__ == "__main__":
    asyncio.run(main())
