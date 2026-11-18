import asyncio
import inspect
from concurrent.futures import ThreadPoolExecutor
from typing import Any, List, Dict
from ..main import Memory


def _run_async(coro: Any) -> Any:
    if not inspect.isawaitable(coro):
        return coro
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


class CrewAIMemory:
    """
    Adapter for CrewAI's memory system.
    Usage:
    crew = Crew(..., memory=True, memory_config={"provider": CrewAIMemory(mem_instance)})
    """
    def __init__(self, memory: Memory, user_id: str = "crew_agent"):
        self.mem = memory
        self.user_id = user_id

    def save(self, value: Any, metadata: Dict[str, Any] = None) -> None:
        if isinstance(value, str):
            _run_async(self.mem.add(value, user_id=self.user_id, meta=metadata))

    def search(self, query: str, limit: int = 3) -> List[Any]:
        results = _run_async(self.mem.search(query, user_id=self.user_id)) or []
        return [r["content"] for r in results[:limit]]


def memory_node(state: Dict, memory: Memory, user_key: str = "user_id", input_key: str = "messages"):
    """
    LangGraph node to automatically persist state to memory.
    """
    messages = state.get(input_key, [])
    user_id = state.get(user_key, "anonymous")

    if messages:
        last_msg = messages[-1]
        content = getattr(last_msg, "content", str(last_msg))
        _run_async(memory.add(content, user_id=user_id))
    return state
