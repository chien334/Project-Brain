# Community 28: Langchain

> **Leiden Community Cluster 28**  
> Contains 23 member symbols with high intra-cluster cohesive density.

## 1. Community Member Symbols

| Symbol Name | Type | Source File | Location | Community ID |
| :--- | :--- | :--- | :--- | :--- |
| `agents.py` | code | `src/projectbrain/integrations/agents.py` | `L1` | Community 28 |
| `CrewAIMemory` | code | `src/projectbrain/integrations/agents.py` | `L3` | Community 28 |
| `.__init__()` | code | `src/projectbrain/integrations/agents.py` | `L9` | Community 28 |
| `.save()` | code | `src/projectbrain/integrations/agents.py` | `L13` | Community 28 |
| `.search()` | code | `src/projectbrain/integrations/agents.py` | `L17` | Community 28 |
| `memory_node()` | code | `src/projectbrain/integrations/agents.py` | `L20` | Community 28 |
| `Adapter for CrewAI's memory system.     Usage:     crew = Crew(..., memory=True,` | code | `src/projectbrain/integrations/agents.py` | `L4` | Community 28 |
| `LangGraph node to automatically persist state to memory.` | code | `src/projectbrain/integrations/agents.py` | `L21` | Community 28 |
| `langchain.py` | code | `src/projectbrain/integrations/langchain.py` | `L1` | Community 28 |
| `ProjectBrainChatMessageHistory` | code | `src/projectbrain/integrations/langchain.py` | `L14` | Community 28 |
| `BaseChatMessageHistory` | code | `src/projectbrain/integrations/langchain.py` | `src/projectbrain/integrations/langchain.py:1` | Community 28 |
| `.__init__()` | code | `src/projectbrain/integrations/langchain.py` | `L15` | Community 28 |
| `messages()` | code | `src/projectbrain/integrations/langchain.py` | `L21` | Community 28 |
| `.aget_messages()` | code | `src/projectbrain/integrations/langchain.py` | `L36` | Community 28 |
| `.add_message()` | code | `src/projectbrain/integrations/langchain.py` | `L50` | Community 28 |
| `.aadd_messages()` | code | `src/projectbrain/integrations/langchain.py` | `L68` | Community 28 |
| `.clear()` | code | `src/projectbrain/integrations/langchain.py` | `L74` | Community 28 |
| `ProjectBrainRetriever` | code | `src/projectbrain/integrations/langchain.py` | `L77` | Community 28 |
| `BaseRetriever` | code | `src/projectbrain/integrations/langchain.py` | `src/projectbrain/integrations/langchain.py:1` | Community 28 |
| `._get_relevant_documents()` | code | `src/projectbrain/integrations/langchain.py` | `L82` | Community 28 |
| `synchronous property required by langchain` | code | `src/projectbrain/integrations/langchain.py` | `L22` | Community 28 |
| `sync method that properly handles async storage` | code | `src/projectbrain/integrations/langchain.py` | `L51` | Community 28 |
| `async batch add for langchain` | code | `src/projectbrain/integrations/langchain.py` | `L69` | Community 28 |

## 2. Internal Call & Import Relations

| Source Symbol | Relation | Target Symbol | Confidence | Score |
| :--- | :--- | :--- | :--- | :--- |
| `agents` | `contains` | `agents_crewaimemory` | EXTRACTED | 1.0 |
| `agents` | `contains` | `agents_memory_node` | EXTRACTED | 1.0 |
| `agents` | `contains` | `agents_crewaimemory` | EXTRACTED | 1.0 |
| `agents` | `contains` | `agents_memory_node` | EXTRACTED | 1.0 |
| `agents_crewaimemory` | `method` | `agents_crewaimemory_init` | EXTRACTED | 1.0 |
| `agents_crewaimemory` | `method` | `agents_crewaimemory_save` | EXTRACTED | 1.0 |
| `agents_crewaimemory` | `method` | `agents_crewaimemory_search` | EXTRACTED | 1.0 |
| `agents_crewaimemory` | `method` | `agents_crewaimemory_init` | EXTRACTED | 1.0 |
| `agents_crewaimemory` | `method` | `agents_crewaimemory_save` | EXTRACTED | 1.0 |
| `agents_crewaimemory` | `method` | `agents_crewaimemory_search` | EXTRACTED | 1.0 |
| `agents_rationale_4` | `rationale_for` | `agents_crewaimemory` | EXTRACTED | 1.0 |
| `agents_rationale_4` | `rationale_for` | `agents_crewaimemory` | EXTRACTED | 1.0 |
| `agents_rationale_21` | `rationale_for` | `agents_memory_node` | EXTRACTED | 1.0 |
| `agents_rationale_21` | `rationale_for` | `agents_memory_node` | EXTRACTED | 1.0 |
| `langchain` | `contains` | `langchain_projectbrainchatmessagehistory` | EXTRACTED | 1.0 |
| `langchain` | `contains` | `langchain_messages` | EXTRACTED | 1.0 |
| `langchain` | `contains` | `langchain_projectbrainretriever` | EXTRACTED | 1.0 |
| `langchain` | `contains` | `langchain_projectbrainchatmessagehistory` | EXTRACTED | 1.0 |
| `langchain` | `contains` | `langchain_messages` | EXTRACTED | 1.0 |
| `langchain` | `contains` | `langchain_projectbrainretriever` | EXTRACTED | 1.0 |
| `langchain_projectbrainchatmessagehistory` | `inherits` | `basechatmessagehistory` | EXTRACTED | 1.0 |
| `langchain_projectbrainchatmessagehistory` | `method` | `langchain_projectbrainchatmessagehistory_init` | EXTRACTED | 1.0 |
| `langchain_projectbrainchatmessagehistory` | `method` | `langchain_projectbrainchatmessagehistory_aget_messages` | EXTRACTED | 1.0 |
| `langchain_projectbrainchatmessagehistory` | `method` | `langchain_projectbrainchatmessagehistory_add_message` | EXTRACTED | 1.0 |
| `langchain_projectbrainchatmessagehistory` | `method` | `langchain_projectbrainchatmessagehistory_clear` | EXTRACTED | 1.0 |

## 3. Cross-Community Bridge Connections

| Source Symbol | Target External Symbol | Relation | Confidence |
| :--- | :--- | :--- | :--- |
| `agents` | `typing` | `imports_from` | EXTRACTED |
| `agents` | `main` | `imports_from` | EXTRACTED |
| `agents` | `typing` | `imports_from` | EXTRACTED |
| `agents` | `main` | `imports_from` | EXTRACTED |
| `langchain` | `typing` | `imports_from` | EXTRACTED |
| `langchain` | `langchain_core_chat_history` | `imports_from` | EXTRACTED |
| `langchain` | `langchain_core_messages` | `imports_from` | EXTRACTED |
| `langchain` | `langchain_core_retrievers` | `imports_from` | EXTRACTED |
| `langchain` | `langchain_core_documents` | `imports_from` | EXTRACTED |
| `langchain` | `langchain_core_callbacks` | `imports_from` | EXTRACTED |
| `langchain` | `langchain_core_callbacks` | `imports_from` | EXTRACTED |
| `langchain` | `main` | `imports_from` | EXTRACTED |
| `langchain` | `typing` | `imports_from` | EXTRACTED |
| `langchain` | `langchain_core_chat_history` | `imports_from` | EXTRACTED |
| `langchain` | `langchain_core_messages` | `imports_from` | EXTRACTED |
| `langchain` | `langchain_core_retrievers` | `imports_from` | EXTRACTED |
| `langchain` | `langchain_core_documents` | `imports_from` | EXTRACTED |
| `langchain` | `langchain_core_callbacks` | `imports_from` | EXTRACTED |
| `langchain` | `main` | `imports_from` | EXTRACTED |
| `langchain_rationale_22` | `langchain_projectbrainchatmessagehistory_messages` | `rationale_for` | EXTRACTED |
