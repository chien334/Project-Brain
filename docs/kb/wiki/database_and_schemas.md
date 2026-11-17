# Database Architectures & Schema Reference

> **PostgreSQL Multi-Schema, SQLite Overlays, HSG Cognitive Memory, and Pydantic DTOs**  
> Deterministic schema documentation covering storage backends and typed data contracts.

## 1. PostgreSQL Multi-Schema Architecture

Project-Brain employs a multi-schema PostgreSQL layout for git-like branch isolation:
- **`pb_root`**: Contains canonical baseline nodes, edges, hyperedges, and branch registry for the default `main` branch.
- **`pb_br_{hash}`**: Isolated delta schema created on-demand per branch, storing incremental upserts, tombstones, and file cache records.

### 1.1 Canonical `pb_root` Schema

| Table Name | Description | Key Columns |
| :--- | :--- | :--- |
| `pb_root.nodes` | AST & semantic entities | `id (PK)`, `project_id`, `label`, `file_type`, `source_file`, `community`, `metadata` |
| `pb_root.edges` | Directed call/import edges | `project_id`, `source`, `target`, `relation`, `confidence`, `confidence_score`, `weight` |
| `pb_root.hyperedges` | N-ary relationship groups | `id (PK)`, `project_id`, `label`, `nodes`, `relation`, `confidence` |
| `pb_root.graph_branches` | Branch metadata catalog | `project_id`, `branch_name (PK)`, `branch_schema`, `base_branch`, `created_at` |

### 1.2 Isolated `pb_br_{hash}` Delta Schemas

| Table Name | Description | Key Columns |
| :--- | :--- | :--- |
| `{schema}.node_upserts` | Nodes added or modified on branch | `id (PK)`, `project_id`, `label`, `file_type`, `source_file`, `community`, `metadata` |
| `{schema}.node_tombstones` | Nodes deleted on branch | `id (PK)`, `deleted_at` |
| `{schema}.edge_upserts` | Edges added or modified on branch | `source`, `target`, `relation`, `confidence`, `confidence_score`, `weight` |
| `{schema}.edge_tombstones` | Edges deleted on branch | `source`, `target`, `relation`, `deleted_at` |
| `{schema}.hyperedge_upserts` | Hyperedges added or modified | `id (PK)`, `label`, `nodes`, `relation`, `confidence` |
| `{schema}.hyperedge_tombstones` | Hyperedges deleted | `id (PK)`, `deleted_at` |
| `{schema}.synced_files` | SHA256 cache of synced files | `file_path (PK)`, `sha256`, `synced_at` |

## 2. HSG Cognitive Memory & Associative Knowledge Tables

| Table Name | Description | Key Columns |
| :--- | :--- | :--- |
| `memories` | Master cognitive memory chunks | `id (PK)`, `content`, `summary`, `hash`, `tags`, `primary_sector`, `secondary_sector`, `salience` |
| `vectors` | pgvector / HNSW vector embeddings | `memory_id (FK)`, `embedding`, `meta` |
| `waypoints` | Directed associative knowledge graph | `src_id (FK)`, `dst_id (FK)`, `weight`, `relation`, `meta` |
| `temporal_facts` | Point-in-time entity facts | `id (PK)`, `entity`, `attribute`, `value`, `valid_at`, `expired_at`, `memory_id` |
| `temporal_edges` | Temporal event relationships | `src_fact_id (FK)`, `dst_fact_id (FK)`, `relation`, `valid_at`, `expired_at` |

## 3. Discovered Pydantic Data Models

| Model Name | Source Location | Field Count | Key Fields | Description |
| :--- | :--- | :--- | :--- | :--- |
| `AccountRequest` | `src/projectbrain/server/routes/auth.py:52` | 4 | `username, token, role, allowed_tools` | Pydantic DTO |
| `ActiveProjectRequest` | `src/projectbrain/server/routes/memory.py:241` | 1 | `project_id` | Pydantic DTO |
| `AddMemoryRequest` | `src/projectbrain/server/routes/memory.py:10` | 5 | `content, user_id, tags, metadata...` | Pydantic DTO |
| `AddReq` | `src/projectbrain/core/types.py:4` | 6 | `content, tags, metadata, salience...` | Pydantic DTO |
| `BaseGraphModel` | `src/projectbrain/graphify_engine/doc_synthesizer.py:51` | 0 | `None` | Pydantic DTO |
| `BaseGraphModel` | `src/projectbrain/graphify_engine/models.py:18` | 0 | `None` | Base model with cross-version Pydantic helper methods. |
| `BaseGraphModel` | `src/projectbrain/graphify_engine/extractor.py:48` | 0 | `None` | Pydantic DTO |
| `BaseGraphModel` | `src/projectbrain/graphify_engine/doc_pipeline.py:48` | 0 | `None` | Pydantic DTO |
| `BranchDiffResult` | `src/projectbrain/graphify_engine/models.py:88` | 6 | `summary, new_nodes, modified_nodes, deleted_node_ids...` | Pydantic DTO |
| `BranchDiffResult` | `src/projectbrain/graphify_engine/hybrid_resolver.py:92` | 6 | `summary, new_nodes, modified_nodes, deleted_node_ids...` | Pydantic DTO |
| `BranchDiffSummary` | `src/projectbrain/graphify_engine/models.py:80` | 5 | `new_nodes_count, modified_nodes_count, deleted_nodes_count, new_edges_count...` | Pydantic DTO |
| `BranchDiffSummary` | `src/projectbrain/graphify_engine/hybrid_resolver.py:85` | 5 | `new_nodes_count, modified_nodes_count, deleted_nodes_count, new_edges_count...` | Pydantic DTO |
| `CodeDocDrift` | `src/projectbrain/graphify_engine/kb_linker.py:103` | 7 | `symbol_id, doc_chunk_id, drift_type, severity...` | Detected misalignment between documentation and code. |
| `CodeSymbolRef` | `src/projectbrain/graphify_engine/kb_linker.py:77` | 11 | `node_id, symbol_name, symbol_type, file_path...` | Reference index item for AST code & test symbols. |
| `CreateDocxInput` | `extensions_mcp/convert_docx/server_stdio.py:12` | 2 | `file_path, content` | Pydantic DTO |
| `CreatePptxInput` | `extensions_mcp/convert_pptx/server_stdio.py:19` | 3 | `file_path, title, content` | Pydantic DTO |
| `CreateProjectRequest` | `src/projectbrain/server/routes/codegraph.py:53` | 3 | `id, name, description` | Pydantic DTO |
| `CrossLinkEdge` | `src/projectbrain/graphify_engine/kb_linker.py:92` | 7 | `source_id, target_id, relation, confidence...` | Bidirectional cross-domain relationship. |
| `DeleteAllRequest` | `src/projectbrain/server/routes/memory.py:24` | 1 | `user_id` | Pydantic DTO |
| `DocChunk` | `src/projectbrain/graphify_engine/kb_linker.py:61` | 12 | `chunk_id, doc_node_id, doc_path, heading_breadcrumb...` | Hierarchically scoped document segment. |
| `DocxFilePath` | `extensions_mcp/convert_docx/server_stdio.py:9` | 1 | `file_path` | Pydantic DTO |
| `ExternalMCPRequest` | `src/projectbrain/server/routes/mcp_registry.py:150` | 4 | `name, command, args, env` | Pydantic DTO |
| `ExtractImagesInput` | `extensions_mcp/convert_pptx/server_stdio.py:15` | 2 | `file_path, output_dir` | Pydantic DTO |
| `GraphDelta` | `src/projectbrain/graphify_engine/doc_synthesizer.py:92` | 9 | `project_id, branch, nodes, edges...` | Pydantic DTO |
| `GraphDelta` | `src/projectbrain/graphify_engine/models.py:68` | 9 | `project_id, branch, nodes, edges...` | Pydantic DTO |
| `GraphDelta` | `src/projectbrain/graphify_engine/extractor.py:89` | 9 | `project_id, branch, nodes, edges...` | Pydantic DTO |
| `GraphDelta` | `src/projectbrain/graphify_engine/doc_pipeline.py:89` | 9 | `project_id, branch, nodes, edges...` | Pydantic DTO |
| `GraphDelta` | `src/projectbrain/graphify_engine/hybrid_resolver.py:74` | 9 | `project_id, branch, nodes, edges...` | Pydantic DTO |
| `GraphEdge` | `src/projectbrain/graphify_engine/doc_synthesizer.py:71` | 9 | `project_id, source, target, relation...` | Pydantic DTO |
| `GraphEdge` | `src/projectbrain/graphify_engine/models.py:45` | 9 | `project_id, source, target, relation...` | Pydantic DTO |
| `GraphEdge` | `src/projectbrain/graphify_engine/extractor.py:68` | 9 | `project_id, source, target, relation...` | Pydantic DTO |
| `GraphEdge` | `src/projectbrain/graphify_engine/doc_pipeline.py:68` | 9 | `project_id, source, target, relation...` | Pydantic DTO |
| `GraphEdge` | `src/projectbrain/graphify_engine/hybrid_resolver.py:52` | 10 | `project_id, source, target, relation...` | Pydantic DTO |
| `GraphHyperedge` | `src/projectbrain/graphify_engine/doc_synthesizer.py:82` | 8 | `id, project_id, label, nodes...` | Pydantic DTO |
| `GraphHyperedge` | `src/projectbrain/graphify_engine/models.py:57` | 8 | `id, project_id, label, nodes...` | Pydantic DTO |
| `GraphHyperedge` | `src/projectbrain/graphify_engine/extractor.py:79` | 8 | `id, project_id, label, nodes...` | Pydantic DTO |
| `GraphHyperedge` | `src/projectbrain/graphify_engine/doc_pipeline.py:79` | 8 | `id, project_id, label, nodes...` | Pydantic DTO |
| `GraphHyperedge` | `src/projectbrain/graphify_engine/hybrid_resolver.py:64` | 8 | `id, project_id, label, nodes...` | Pydantic DTO |
| `GraphNode` | `src/projectbrain/graphify_engine/doc_synthesizer.py:60` | 9 | `id, project_id, label, file_type...` | Pydantic DTO |
| `GraphNode` | `src/projectbrain/graphify_engine/models.py:33` | 9 | `id, project_id, label, file_type...` | Pydantic DTO |
| `GraphNode` | `src/projectbrain/graphify_engine/extractor.py:57` | 9 | `id, project_id, label, file_type...` | Pydantic DTO |
| `GraphNode` | `src/projectbrain/graphify_engine/doc_pipeline.py:57` | 9 | `id, project_id, label, file_type...` | Pydantic DTO |
| `GraphNode` | `src/projectbrain/graphify_engine/hybrid_resolver.py:40` | 10 | `id, project_id, label, file_type...` | Pydantic DTO |
| `ImageResult` | `extensions_mcp/image_to_markdown/extractor.py:17` | 5 | `index, file, ok, error...` | Pydantic DTO |
| `IngestReq` | `src/projectbrain/core/types.py:32` | 6 | `source, content_type, data, metadata...` | Pydantic DTO |
| `KBBuildRequest` | `src/projectbrain/server/routes/kb.py:103` | 6 | `project_id, branch, workspace_path, persist_memory...` | Pydantic DTO |
| `KBCoverageReport` | `src/projectbrain/graphify_engine/kb_linker.py:126` | 18 | `project_id, branch, total_symbols, documented_symbols...` | Repository-wide documentation & test traceability analytics. |
| `KBExportRequest` | `src/projectbrain/server/routes/kb.py:112` | 5 | `project_id, branch, workspace_path, output_dir...` | Pydantic DTO |
| `KBTraceResult` | `src/projectbrain/graphify_engine/kb_linker.py:114` | 8 | `query, code_nodes, doc_chunks, tests...` | Unified inspection output for any target query. |
| `LgmStoreReq` | `src/projectbrain/core/types.py:40` | 8 | `node, content, tags, metadata...` | Pydantic DTO |
| `LocalDiffRequest` | `src/projectbrain/server/routes/graphify.py:163` | 5 | `project_id, branch, local_delta, local_path...` | Pydantic DTO |
| `MemRow` | `src/projectbrain/core/types.py:18` | 12 | `id, content, primary_sector, tags...` | Pydantic DTO |
| `OfficeExportResult` | `src/projectbrain/graphify_engine/doc_synthesizer.py:117` | 5 | `file_path, export_type, file_size_bytes, sections_count...` | Summary of a generated Office document. |
| `PMDocRequest` | `src/projectbrain/server/routes/pm.py:16` | 4 | `prompt, doc_type, user_id, author` | Pydantic DTO |
| `PMSearchRequest` | `src/projectbrain/server/routes/pm.py:10` | 4 | `query, user_id, limit, author` | Pydantic DTO |
| `PptxFilePath` | `extensions_mcp/convert_pptx/server_stdio.py:12` | 1 | `file_path` | Pydantic DTO |
| `PromoteRequest` | `src/projectbrain/server/routes/graphify.py:147` | 2 | `project_id, branch` | Pydantic DTO |
| `PushDocumentRequest` | `src/projectbrain/server/routes/sources.py:39` | 4 | `project_id, branch, document, graph_delta` | Pydantic DTO |
| `QueryReq` | `src/projectbrain/core/types.py:12` | 4 | `query, k, filters, user_id` | Pydantic DTO |
| `QueryRequest` | `src/projectbrain/server/routes/graphify.py:152` | 8 | `project_id, query, branch, local_delta...` | Pydantic DTO |
| `RegisterMCPRequest` | `src/projectbrain/server/routes/mcp_registry.py:11` | 4 | `transport_type, user_id, tags, sse_url` | Pydantic DTO |
| `SearchMemoryRequest` | `src/projectbrain/server/routes/memory.py:17` | 5 | `query, user_id, limit, filters...` | Pydantic DTO |
| `SyncBranchRequest` | `src/projectbrain/server/routes/graphify.py:127` | 15 | `project_id, branch, nodes, edges...` | Pydantic DTO |
| `SyncRequest` | `src/projectbrain/server/routes/codegraph.py:45` | 6 | `project_id, project_name, nodes, edges...` | Pydantic DTO |
| `SynthesisResult` | `src/projectbrain/graphify_engine/doc_synthesizer.py:126` | 3 | `wiki_pages, office_exports, stats` | Aggregate output from doc synthesis. |
| `WikiPage` | `src/projectbrain/graphify_engine/doc_synthesizer.py:107` | 6 | `slug, title, relative_path, content...` | Represents a generated Markdown wiki document. |
| `ingest_req` | `src/projectbrain/server/routes/sources.py:270` | 3 | `creds, filters, user_id` | Pydantic DTO |
