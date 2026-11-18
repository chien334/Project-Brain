from typing import List, Any, Optional
try:
    from langchain_core.chat_history import BaseChatMessageHistory
    from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
    from langchain_core.retrievers import BaseRetriever
    from langchain_core.documents import Document
    from langchain_core.callbacks import CallbackManagerForRetrieverRun
    _HAS_LANGCHAIN = True
except ImportError:
    _HAS_LANGCHAIN = False
    BaseChatMessageHistory = object
    BaseRetriever = object
    BaseMessage = Any
    CallbackManagerForRetrieverRun = Any

    class _FallbackMsg:
        def __init__(self, content: str = "", **kwargs: Any):
            self.content = content

    HumanMessage = _FallbackMsg
    AIMessage = _FallbackMsg

    class Document:  # type: ignore[no-redef]
        def __init__(self, page_content: str = "", metadata: Optional[dict] = None, **kwargs: Any):
            self.page_content = page_content
            self.metadata = metadata or {}

from ..main import Memory

class ProjectBrainChatMessageHistory(BaseChatMessageHistory):
    def __init__(self, memory: Memory, user_id: str, session_id: str = "default"):
        self.mem = memory
        self.user_id = user_id
        self.session_id = session_id

    @property
    def messages(self) -> List[BaseMessage]:
        """synchronous property required by langchain"""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # in async context, return empty and let aget_messages handle it
                return []
            else:
                # not in async context, run sync
                return loop.run_until_complete(self.aget_messages())
        except RuntimeError:
            # no event loop, create one
            return asyncio.run(self.aget_messages())

    async def aget_messages(self) -> List[BaseMessage]:
        # history() is synchronous, not async
        history = self.mem.history(user_id=self.user_id, limit=20)
        msgs = []
        for h in history:
            c = h.get("content", "")
            if c.startswith("User:"):
                msgs.append(HumanMessage(content=c[5:].strip()))
            elif c.startswith("Assistant:"):
                msgs.append(AIMessage(content=c[10:].strip()))
            else:
                msgs.append(HumanMessage(content=c))
        return msgs

    def add_message(self, message: BaseMessage) -> None:
        """sync method that properly handles async storage"""
        role = "User" if isinstance(message, HumanMessage) else "Assistant"
        content = f"{role}: {message.content}"
        
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # schedule as task
                loop.create_task(self.mem.add(content, user_id=self.user_id))
            else:
                # run synchronously
                loop.run_until_complete(self.mem.add(content, user_id=self.user_id))
        except RuntimeError:
            # no loop, create one
            asyncio.run(self.mem.add(content, user_id=self.user_id))
    
    async def aadd_messages(self, messages: List[BaseMessage]) -> None:
        """async batch add for langchain"""
        for msg in messages:
            role = "User" if isinstance(msg, HumanMessage) else "Assistant"
            await self.mem.add(f"{role}: {msg.content}", user_id=self.user_id)

    def clear(self) -> None:
        pass

class ProjectBrainRetriever(BaseRetriever):
    memory: Memory
    user_id: str
    k: int = 5

    if not _HAS_LANGCHAIN:
        def __init__(self, memory: Memory = None, user_id: str = "default", k: int = 5, **kwargs: Any):
            self.memory = memory
            self.user_id = user_id
            self.k = k

    def _get_relevant_documents(self, query: str, *, run_manager: Any = None) -> List[Document]:
        from .agents import _run_async
        results = _run_async(self.memory.search(query, user_id=self.user_id)) or []
        docs = []
        for r in results[:self.k]:
            docs.append(Document(page_content=r["content"], metadata=r))
        return docs

    async def _aget_relevant_documents(self, query: str, *, run_manager: Any = None) -> List[Document]:
        results = await self.memory.search(query, user_id=self.user_id) or []
        docs = []
        for r in results[:self.k]:
            docs.append(Document(page_content=r["content"], metadata=r))
        return docs
