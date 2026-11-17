# System Architecture & Subsystem Topology

> **Deterministic Zero-Hallucination Architecture Specification**  
> Outlines subsystem boundaries, cross-module call flows, and high-cohesion community clusters.

## 1. High-Level Subsystem Flowchart

```mermaid
flowchart TD
    subgraph StorageLayer ["1. Hybrid Graphify Storage Layer"]
        Store["`PostgresSchemaGraphStore` / `GraphStore`"]
        TreeSitter["`GraphifyExtractor` AST & Leiden"]
        Resolver["`HybridResolver` Multi-Schema Overlay"]
    end

    subgraph MemoryLayer ["2. Cognitive Memory & HSG Subsystem"]
        HSGMem["`add_hsg_memory` / `memories` Table"]
        Waypoints["`waypoints` Associative Knowledge Graph"]
        Vectors["`vectors` pgvector / HNSW Embeddings"]
    end

    subgraph ServerLayer ["3. Unified Server & Tooling Layer"]
        REST["FastAPI Server (`/graphify`, `/memory`, `/pm`)"]
        MCP["FastMCP Server (`projectbrain_query`, `sync`)"]
        CLI["CLI Commands (`graphify-sync`, `build-kb`)"]
    end

    subgraph ConnectorsLayer ["4. Document & External Pipelines"]
        Ingest["`DocumentPipeline` (DOCX, XLSX, PPTX, PDF)"]
        OCR["Local PaddleOCR / Surya Engine"]
    end

    REST --> Resolver
    REST --> HSGMem
    MCP --> Resolver
    MCP --> HSGMem
    CLI --> REST
    TreeSitter --> Store
    Resolver --> Store
    HSGMem --> Waypoints
    HSGMem --> Vectors
    Ingest --> HSGMem
    Ingest --> OCR
```

## 2. Core Architectural Subsystems

### 2.1 Hybrid Graphify AST & GraphStore Engine
- **`PostgresSchemaGraphStore`** (`src/projectbrain/graphify_engine/store.py`): Multi-schema PostgreSQL engine managing `pb_root` canonical baseline and isolated `pb_br_{hash}` branch overlays.
- **`HybridResolver`** (`src/projectbrain/graphify_engine/store.py`): In-memory resolution layer blending SQLite local delta caches with PostgreSQL schemas.
- **`GraphifyExtractor`** (`src/projectbrain/graphify_engine/extractor.py`): Zero-token Tree-sitter AST extraction across 22 programming languages, computing SHA256 hashes, Leiden community clusters, and architectural God nodes.
- **`ProjectDocSynthesizer`** (`src/projectbrain/graphify_engine/doc_synthesizer.py`): Zero-hallucination deterministic documentation generator emitting Markdown Knowledge Wiki, Microsoft Word specifications, Excel traceability matrices, and PowerPoint presentations.

### 2.2 Cognitive Memory & HSG Associative Knowledge
- **`add_hsg_memory`** (`src/projectbrain/memory/hsg.py`): Primary memory ingestion function routing content into `memories`, generating vector embeddings, and updating entity graphs.
- **`hsg_query`** (`src/projectbrain/memory/hsg.py`): Hybrid vector similarity and graph traversal search across cognitive memory sectors (`semantic`, `episodic`, `procedural`).
- **`waypoints`**: Directed associative graph connecting contextual memories and document sections with code entities.

### 2.3 Server REST API & FastMCP Ecosystem
- **FastAPI Application** (`src/projectbrain/server/api.py`): Asynchronous HTTP server hosting `/graphify/sync`, `/graphify/subgraph`, `/memory/add`, `/memory/search`, and `/pm/search`.
- **FastMCP Server** (`src/projectbrain/ai/mcp.py`): Anthropic Model Context Protocol server exposing `projectbrain_query`, `projectbrain_store`, `projectbrain_sync_codegraph`, and `projectbrain_diff_project_versions`.
- **CLI Suite** (`src/projectbrain/main.py`): Developer terminal commands for synchronization, diffing, and document ingestion.

## 3. Cross-Module Call Flows & Community Interactions

| Source Module | Relation | Target Module | Confidence | Architectural Purpose |
| :--- | :--- | :--- | :--- | :--- |
| `src/projectbrain/server/routes/graphify.py` | calls | `src/projectbrain/graphify_engine/store.py` | EXTRACTED | REST endpoints delegate graph mutations to `GraphStore` |
| `src/projectbrain/server/routes/memory.py` | calls | `src/projectbrain/memory/hsg.py` | EXTRACTED | Memory endpoints invoke `add_hsg_memory` and `hsg_query` |
| `src/projectbrain/ai/mcp.py` | calls | `src/projectbrain/graphify_engine/store.py` | EXTRACTED | MCP tools inspect codebase graph topology |
| `src/projectbrain/graphify_engine/doc_synthesizer.py` | introspects | `src/projectbrain/server/routes/` | EXTRACTED | Synthesizer extracts verified endpoints directly from source |
