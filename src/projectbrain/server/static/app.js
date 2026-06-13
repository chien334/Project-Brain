document.addEventListener('DOMContentLoaded', () => {
    // DOM Elements
    const currentProjectSelect = document.getElementById('currentProject');
    const btnAddProject = document.getElementById('btnAddProject');
    const currentUser = document.getElementById('currentUser');
    
    // Version Comparison Elements
    const diffBaseProject = document.getElementById('diffBaseProject');
    const diffTargetProject = document.getElementById('diffTargetProject');
    const btnCompareVersions = document.getElementById('btnCompareVersions');
    const diffResultsArea = document.getElementById('diffResultsArea');
    const diffCountAdded = document.getElementById('diffCountAdded');
    const diffCountDeleted = document.getElementById('diffCountDeleted');
    const diffCountModified = document.getElementById('diffCountModified');
    const diffCgList = document.getElementById('diffCgList');
    const diffOmList = document.getElementById('diffOmList');

    // Document Upload Elements
    const docUploadFile = document.getElementById('docUploadFile');
    const docUploadTags = document.getElementById('docUploadTags');
    const btnUploadDoc = document.getElementById('btnUploadDoc');
    const uploadStatusText = document.getElementById('uploadStatusText');
    const uploadDropzone = document.getElementById('uploadDropzone');

    const totalMemories = document.getElementById('totalMemories');
    const totalFacts = document.getElementById('totalFacts');
    const sectorCounts = document.getElementById('sectorCounts');
    const activeTags = document.getElementById('activeTags');
    const btnDeleteAll = document.getElementById('btnDeleteAll');
    
    const tabLinks = document.querySelectorAll('.tab-link');
    const tabContents = document.querySelectorAll('.tab-content');
    
    const newMemoryContent = document.getElementById('newMemoryContent');
    const newMemoryTags = document.getElementById('newMemoryTags');
    const btnSaveMemory = document.getElementById('btnSaveMemory');
    const btnRefreshHistory = document.getElementById('btnRefreshHistory');
    const memoriesFeed = document.getElementById('memoriesFeed');
    
    const searchQuery = document.getElementById('searchQuery');
    const btnSearch = document.getElementById('btnSearch');
    const searchResults = document.getElementById('searchResults');
    
    const docType = document.getElementById('docType');
    const copilotPrompt = document.getElementById('copilotPrompt');
    const btnGenerateDoc = document.getElementById('btnGenerateDoc');
    const copilotResults = document.getElementById('copilotResults');
    const generatedDocContent = document.getElementById('generatedDocContent');
    const docSourcesFeed = document.getElementById('docSourcesFeed');
    const btnCopyDoc = document.getElementById('btnCopyDoc');

    // MCP Elements
    const mcpTransportRadios = document.getElementsByName('mcpTransport');
    const mcpStdioConfig = document.getElementById('mcpStdioConfig');
    const mcpSseConfig = document.getElementById('mcpSseConfig');
    const mcpUserId = document.getElementById('mcpUserId');
    const mcpTags = document.getElementById('mcpTags');
    const mcpSseUrl = document.getElementById('mcpSseUrl');
    const btnRegisterMcp = document.getElementById('btnRegisterMcp');
    const btnUnregisterMcp = document.getElementById('btnUnregisterMcp');
    const mcpFileStatus = document.getElementById('mcpFileStatus');
    const mcpRegisteredStatus = document.getElementById('mcpRegisteredStatus');
    const mcpConfigPath = document.getElementById('mcpConfigPath');
    const mcpActiveConfig = document.getElementById('mcpActiveConfig');

    // Code Graph Elements
    const cgProjectSelect = document.getElementById('cgProjectSelect');
    const cgSearchInput = document.getElementById('cgSearchInput');
    const cgKindSelect = document.getElementById('cgKindSelect');
    const btnLoadCodeGraph = document.getElementById('btnLoadCodeGraph');
    const graphLoader = document.getElementById('graphLoader');
    const graphEmptyState = document.getElementById('graphEmptyState');
    const nodeDetailsPanel = document.getElementById('nodeDetailsPanel');
    const ndName = document.getElementById('ndName');
    const ndKind = document.getElementById('ndKind');
    const ndFilePath = document.getElementById('ndFilePath');
    const ndLines = document.getElementById('ndLines');
    const ndSignature = document.getElementById('ndSignature');
    const ndDocstring = document.getElementById('ndDocstring');
    const ndLineRow = document.getElementById('ndLineRow');
    const ndSignatureRow = document.getElementById('ndSignatureRow');
    const ndDocstringRow = document.getElementById('ndDocstringRow');
    
    let cgNetwork = null;
    let cgNodesList = [];

    // State
    let activeUser = currentProjectSelect.value || 'default';
    
    // Initialize Dashboard
    init();

    function init() {
        setupTabs();
        loadStats();
        loadMemories();
        loadMcpStatus();
        loadProjects();
        
        // Sync initial active project to the server
        fetch('/memory/active-project', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ project_id: activeUser })
        }).catch(err => console.error('Failed to sync initial active project:', err));
        
        // Listeners
        currentUser.value = localStorage.getItem('currentUser') || '';
        currentUser.addEventListener('input', () => {
            localStorage.setItem('currentUser', currentUser.value.trim());
        });

        currentProjectSelect.addEventListener('change', (e) => {
            activeUser = e.target.value.trim() || 'default';
            loadStats();
            loadMemories();
            
            // Sync active project to the server
            fetch('/memory/active-project', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ project_id: activeUser })
            }).catch(err => console.error('Failed to sync active project to server:', err));
            
            // Sync with Code Graph selector
            if (cgProjectSelect) {
                cgProjectSelect.value = activeUser === 'default' ? '' : activeUser;
            }
            
            // Trigger graph reload if Code Graph tab is active
            const cgTab = document.getElementById('codeGraphTab');
            if (cgTab && cgTab.classList.contains('active')) {
                loadCodeGraph();
            }
        });
        btnAddProject.addEventListener('click', addProject);
        btnCompareVersions.addEventListener('click', compareVersions);

        btnSaveMemory.addEventListener('click', saveMemory);
        btnUploadDoc.addEventListener('click', uploadDocument);
        docUploadFile.addEventListener('change', handleFileSelect);
        
        // Drag and drop dropzone behavior
        uploadDropzone.addEventListener('dragover', (e) => {
            e.preventDefault();
            uploadDropzone.style.borderColor = '#818cf8';
            uploadDropzone.style.background = 'rgba(129, 140, 248, 0.05)';
        });
        uploadDropzone.addEventListener('dragleave', () => {
            uploadDropzone.style.borderColor = 'rgba(255, 255, 255, 0.1)';
            uploadDropzone.style.background = 'rgba(255, 255, 255, 0.01)';
        });
        uploadDropzone.addEventListener('drop', (e) => {
            e.preventDefault();
            uploadDropzone.style.borderColor = 'rgba(255, 255, 255, 0.1)';
            uploadDropzone.style.background = 'rgba(255, 255, 255, 0.01)';
            if (e.dataTransfer.files.length > 0) {
                docUploadFile.files = e.dataTransfer.files;
                handleFileSelect();
            }
        });

        btnRefreshHistory.addEventListener('click', loadMemories);
        btnSearch.addEventListener('click', performSearch);
        btnGenerateDoc.addEventListener('click', generateDoc);
        btnCopyDoc.addEventListener('click', copyDocToClipboard);
        btnDeleteAll.addEventListener('click', deleteAllMemories);

        searchQuery.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') performSearch();
        });
        copilotPrompt.addEventListener('keypress', (e) => {
            if (e.key === 'Enter' && e.ctrlKey) generateDoc();
        });

        // MCP Listeners
        mcpTransportRadios.forEach(radio => {
            radio.addEventListener('change', (e) => {
                if (e.target.value === 'stdio') {
                    mcpStdioConfig.style.display = 'block';
                    mcpSseConfig.style.display = 'none';
                } else {
                    mcpStdioConfig.style.display = 'none';
                    mcpSseConfig.style.display = 'block';
                }
            });
        });

        btnRegisterMcp.addEventListener('click', registerMcp);
        btnUnregisterMcp.addEventListener('click', unregisterMcp);
        btnLoadCodeGraph.addEventListener('click', loadCodeGraph);
        cgSearchInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') loadCodeGraph();
        });
        cgProjectSelect.addEventListener('change', (e) => {
            const val = e.target.value.trim();
            activeUser = val || 'default';
            if (currentProjectSelect) {
                currentProjectSelect.value = activeUser;
            }
            loadStats();
            loadMemories();
            loadCodeGraph();
        });
        document.getElementById('btnSaveExtMcp').addEventListener('click', saveExternalMcp);
    }

    // Tabs logic
    function setupTabs() {
        tabLinks.forEach(link => {
            link.addEventListener('click', () => {
                const targetTab = link.getAttribute('data-tab');
                
                tabLinks.forEach(btn => btn.classList.remove('active'));
                tabContents.forEach(content => content.classList.remove('active'));
                
                link.classList.add('active');
                document.getElementById(targetTab).classList.add('active');
                
                if (targetTab === 'codeGraphTab') {
                    loadProjects();
                    setTimeout(loadCodeGraph, 100);
                }
            });
        });
    }

    // API calls
    async function loadStats() {
        try {
            const res = await fetch(`/memory/stats?user_id=${activeUser}`);
            if (!res.ok) throw new Error("Failed to load stats");
            const data = await res.json();
            
            totalMemories.textContent = data.total_memories || 0;
            totalFacts.textContent = data.total_temporal_facts || 0;
            
            // Render sectors count
            sectorCounts.innerHTML = '';
            const sectors = data.sectors || {};
            if (Object.keys(sectors).length === 0) {
                sectorCounts.innerHTML = `<div class="sector-item placeholder-item">No memories stored</div>`;
            } else {
                for (const [secName, count] of Object.entries(sectors)) {
                    const secDiv = document.createElement('div');
                    secDiv.className = `sector-item ${secName}`;
                    secDiv.innerHTML = `
                        <span class="sector-name">${secName}</span>
                        <span class="sector-count">${count}</span>
                    `;
                    sectorCounts.appendChild(secDiv);
                }
            }
            
            // Render active tags
            activeTags.innerHTML = '';
            const tags = data.tags || [];
            if (tags.length === 0) {
                activeTags.innerHTML = `<span class="placeholder-item">No tags</span>`;
            } else {
                tags.forEach(tag => {
                    const tagSpan = document.createElement('span');
                    tagSpan.className = 'tag';
                    tagSpan.textContent = tag;
                    tagSpan.addEventListener('click', () => {
                        searchQuery.value = `tag:${tag}`;
                        document.querySelector('[data-tab="searchTab"]').click();
                        performSearch();
                    });
                    activeTags.appendChild(tagSpan);
                });
            }
        } catch (err) {
            console.error("Stats loading error:", err);
        }
    }

    async function loadMemories() {
        memoriesFeed.innerHTML = `<div class="loader"><i class="fa-solid fa-spinner fa-spin"></i> Loading...</div>`;
        try {
            const res = await fetch(`/memory/history?user_id=${activeUser}&limit=30`);
            if (!res.ok) throw new Error("Failed to load history");
            const data = await res.json();
            
            const history = data.history || [];
            memoriesFeed.innerHTML = '';
            
            if (history.length === 0) {
                memoriesFeed.innerHTML = `
                    <div class="empty-state">
                        <i class="fa-solid fa-folder-open"></i>
                        <p>No cognitive memories found for this user.</p>
                    </div>`;
                return;
            }
            
            history.forEach(node => {
                const nodeCard = document.createElement('div');
                nodeCard.className = `memory-node-card card ${node.primary_sector}`;
                
                const dateStr = new Date(node.created_at || Date.now()).toLocaleString();
                const tagsList = JSON.parse(node.tags || '[]');
                
                let tagsHtml = '';
                if (tagsList && tagsList.length > 0) {
                    tagsHtml = `<div class="memory-tags-row">` + 
                        tagsList.map(t => `<span class="tag">${t}</span>`).join('') + 
                        `</div>`;
                }

                const saliencePercent = Math.min(100, Math.max(0, (node.salience || 0.5) * 100));

                nodeCard.innerHTML = `
                    <div class="memory-node-header">
                        <span class="memory-sector-tag">${node.primary_sector}</span>
                        <div class="memory-meta">
                            <span><i class="fa-regular fa-clock"></i> ${dateStr}</span>
                            <span>ID: ${node.id.substring(0, 8)}...</span>
                        </div>
                    </div>
                    <div class="memory-content">${escapeHTML(node.content)}</div>
                    ${tagsHtml}
                    <div class="memory-actions">
                        <div class="salience-indicator" title="Salience measures memory strength & relevance">
                            <span>Salience: ${round(node.salience, 2)}</span>
                            <div class="salience-bar-bg">
                                <div class="salience-bar-fill" style="width: ${saliencePercent}%"></div>
                            </div>
                        </div>
                        <div class="action-buttons-group">
                            <button class="btn btn-secondary btn-sm btn-reinforce" data-id="${node.id}"><i class="fa-solid fa-bolt"></i> Reinforce</button>
                            <button class="btn btn-danger btn-sm btn-delete" data-id="${node.id}"><i class="fa-solid fa-trash"></i> Delete</button>
                        </div>
                    </div>
                `;
                
                // Event listeners on actions
                nodeCard.querySelector('.btn-reinforce').addEventListener('click', () => reinforceMemory(node.id));
                nodeCard.querySelector('.btn-delete').addEventListener('click', () => deleteMemory(node.id));
                
                memoriesFeed.appendChild(nodeCard);
            });
        } catch (err) {
            memoriesFeed.innerHTML = `<div class="error-msg">Error loading memories: ${err.message}</div>`;
        }
    }

    async function saveMemory() {
        const content = newMemoryContent.value.trim();
        if (!content) return alert("Memory content cannot be empty.");
        
        const tags = newMemoryTags.value.split(',').map(t => t.trim()).filter(t => t.length > 0);
        
        btnSaveMemory.disabled = true;
        btnSaveMemory.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Storing...`;
        
        try {
            const res = await fetch('/memory/add', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    content: content,
                    user_id: activeUser,
                    tags: tags,
                    metadata: { source: 'dashboard' },
                    author: currentUser.value.trim()
                })
            });
            
            if (!res.ok) throw new Error("Failed to store memory.");
            
            newMemoryContent.value = '';
            newMemoryTags.value = '';
            
            loadStats();
            await loadMemories();
        } catch (err) {
            alert(err.message);
        } finally {
            btnSaveMemory.disabled = false;
            btnSaveMemory.innerHTML = `<i class="fa-solid fa-floppy-disk"></i> Store Memory`;
        }
    }

    async function deleteMemory(id) {
        if (!confirm("Are you sure you want to delete this memory?")) return;
        try {
            const res = await fetch(`/memory/${id}`, { method: 'DELETE' });
            if (!res.ok) throw new Error("Failed to delete memory.");
            loadStats();
            loadMemories();
        } catch (err) {
            alert(err.message);
        }
    }

    async function reinforceMemory(id) {
        try {
            const res = await fetch(`/memory/${id}/reinforce`, { method: 'POST' });
            if (!res.ok) throw new Error("Failed to reinforce memory.");
            loadMemories();
        } catch (err) {
            alert(err.message);
        }
    }

    async function deleteAllMemories() {
        if (!confirm(`WARNING: This will permanently delete all memories for user '${activeUser}'. Proceed?`)) return;
        try {
            const res = await fetch('/memory/delete_all', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user_id: activeUser })
            });
            if (!res.ok) throw new Error("Failed to delete memories.");
            loadStats();
            loadMemories();
        } catch (err) {
            alert(err.message);
        }
    }

    // Search and RAG Search logic
    async function performSearch() {
        const query = searchQuery.value.trim();
        if (!query) return;

        searchResults.innerHTML = `<div class="loader"><i class="fa-solid fa-spinner fa-spin"></i> Searching ProjectBrain...</div>`;
        const mode = document.querySelector('input[name="searchMode"]:checked').value;

        try {
            if (mode === 'rag') {
                // Gemini-powered RAG Search
                const res = await fetch('/pm/search', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        query: query,
                        user_id: activeUser,
                        author: currentUser.value.trim()
                    })
                });
                if (!res.ok) throw new Error("Failed to query Gemini PM search");
                const data = await res.json();
                
                searchResults.innerHTML = '';
                
                // 1. Render Gemini answer
                const ragCard = document.createElement('div');
                ragCard.className = 'card rag-response-card';
                ragCard.innerHTML = `
                    <div class="rag-header">
                        <i class="fa-solid fa-wand-magic-sparkles"></i> Gemini AI Answer
                    </div>
                    <div class="rag-body">${formatMarkdownLike(data.results)}</div>
                `;
                searchResults.appendChild(ragCard);
                
                // 2. Render source memories
                const sourceHeader = document.createElement('h3');
                sourceHeader.style.marginTop = '2rem';
                sourceHeader.style.marginBottom = '1rem';
                sourceHeader.innerHTML = `<i class="fa-solid fa-link"></i> Supporting Memories`;
                searchResults.appendChild(sourceHeader);
                
                renderMemoriesList(data.source_memories, searchResults);
            } else {
                // Raw context retrieval only
                const res = await fetch('/memory/search', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        query: query,
                        user_id: activeUser,
                        limit: 10,
                        author: currentUser.value.trim()
                    })
                });
                if (!res.ok) throw new Error("Failed to perform raw search");
                const data = await res.json();
                
                searchResults.innerHTML = '';
                
                const countHeader = document.createElement('h3');
                countHeader.style.marginBottom = '1rem';
                countHeader.textContent = `Found ${data.results.length} matching memory nodes`;
                searchResults.appendChild(countHeader);
                
                renderMemoriesList(data.results, searchResults);
            }
        } catch (err) {
            searchResults.innerHTML = `<div class="error-msg">Search failed: ${err.message}</div>`;
        }
    }

    // Document Generation logic
    async function generateDoc() {
        const prompt = copilotPrompt.value.trim();
        if (!prompt) return alert("Please specify what documentation you want to generate.");
        
        btnGenerateDoc.disabled = true;
        btnGenerateDoc.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Generating Document...`;
        copilotResults.style.display = 'none';

        try {
            const res = await fetch('/pm/generate-doc', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    prompt: prompt,
                    doc_type: docType.value,
                    user_id: activeUser,
                    author: currentUser.value.trim()
                })
            });
            if (!res.ok) throw new Error("Failed to generate document.");
            const data = await res.json();
            
            // Show result area
            copilotResults.style.display = 'grid';
            copilotResults.style.gridTemplateColumns = '1fr 300px';
            copilotResults.style.gap = '2rem';
            
            // Set Markdown content
            generatedDocContent.innerHTML = formatMarkdownLike(data.document);
            
            // Set Source Memories
            docSourcesFeed.innerHTML = '';
            const sources = data.source_memories || [];
            if (sources.length === 0) {
                docSourcesFeed.innerHTML = `<div class="placeholder-item">No sources used.</div>`;
            } else {
                sources.forEach(src => {
                    const srcItem = document.createElement('div');
                    srcItem.className = 'sector-item';
                    srcItem.style.marginBottom = '0.5rem';
                    srcItem.style.fontSize = '0.8rem';
                    srcItem.style.display = 'block';
                    srcItem.innerHTML = `
                        <div style="font-weight:600; font-size:0.7rem; color:var(--primary-color); text-transform:uppercase;">${src.primary_sector}</div>
                        <div>${escapeHTML(src.content.substring(0, 100))}${src.content.length > 100 ? '...' : ''}</div>
                    `;
                    docSourcesFeed.appendChild(srcItem);
                });
            }
        } catch (err) {
            alert(err.message);
        } finally {
            btnGenerateDoc.disabled = false;
            btnGenerateDoc.innerHTML = `<i class="fa-solid fa-magic"></i> Generate Document`;
        }
    }

    function copyDocToClipboard() {
        const text = generatedDocContent.innerText;
        navigator.clipboard.writeText(text).then(() => {
            const originalText = btnCopyDoc.innerHTML;
            btnCopyDoc.innerHTML = `<i class="fa-solid fa-check"></i> Copied!`;
            setTimeout(() => {
                btnCopyDoc.innerHTML = originalText;
            }, 2000);
        }).catch(err => {
            alert("Copy failed: " + err);
        });
    }

    // UI Helpers
    function renderMemoriesList(memories, container) {
        if (!memories || memories.length === 0) {
            container.innerHTML += `
                <div class="empty-state">
                    <p>No memories found matching the query.</p>
                </div>`;
            return;
        }

        memories.forEach(node => {
            const nodeCard = document.createElement('div');
            nodeCard.className = `memory-node-card card ${node.primary_sector}`;
            
            const dateStr = node.created_at ? new Date(node.created_at).toLocaleString() : 'Recent';
            const tagsList = node.tags ? (typeof node.tags === 'string' ? JSON.parse(node.tags) : node.tags) : [];
            let tagsHtml = '';
            if (tagsList && tagsList.length > 0) {
                tagsHtml = `<div class="memory-tags-row">` + 
                    tagsList.map(t => `<span class="tag">${t}</span>`).join('') + 
                    `</div>`;
            }

            const saliencePercent = Math.min(100, Math.max(0, (node.salience || 0.5) * 100));

            nodeCard.innerHTML = `
                <div class="memory-node-header">
                    <span class="memory-sector-tag">${node.primary_sector}</span>
                    <div class="memory-meta">
                        <span><i class="fa-regular fa-clock"></i> ${dateStr}</span>
                        <span>Score: ${round(node.score || node.salience, 3)}</span>
                    </div>
                </div>
                <div class="memory-content">${escapeHTML(node.content)}</div>
                ${tagsHtml}
            `;
            container.appendChild(nodeCard);
        });
    }

    // Helper functions
    function escapeHTML(str) {
        return str.replace(/[&<>'"]/g, 
            tag => ({
                '&': '&amp;',
                '<': '&lt;',
                '>': '&gt;',
                "'": '&#39;',
                '"': '&quot;'
            }[tag] || tag)
        );
    }

    function round(num, decimals) {
        if (num === null || num === undefined) return 0;
        return Number(Math.round(num + 'e' + decimals) + 'e-' + decimals);
    }

    // Very basic markdown parser to make the generated PM docs look good in UI
    function formatMarkdownLike(mdText) {
        if (!mdText) return '';
        let html = escapeHTML(mdText);
        
        // Headers
        html = html.replace(/^### (.*$)/gim, '<h3>$1</h3>');
        html = html.replace(/^## (.*$)/gim, '<h2>$1</h2>');
        html = html.replace(/^# (.*$)/gim, '<h1>$1</h1>');
        
        // Bold
        html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
        
        // Lists
        html = html.replace(/^\s*-\s+(.*$)/gim, '<li>$1</li>');
        html = html.replace(/(<li>.*<\/li>)/gim, '<ul>$1</ul>');
        // Clean double nested lists
        html = html.replace(/<\/ul>\s*<ul>/g, '');
        
        return html;
    }

    async function loadMcpStatus() {
        try {
            const res = await fetch('/memory/mcp/status');
            if (!res.ok) throw new Error("Failed to load MCP status");
            const data = await res.json();
            
            mcpConfigPath.value = data.config_file_path || '';
            
            if (data.config_file_exists) {
                mcpFileStatus.textContent = 'Found';
                mcpFileStatus.className = 'status-tag success';
            } else {
                mcpFileStatus.textContent = 'Not Found';
                mcpFileStatus.className = 'status-tag danger';
            }
            
            if (data.registered) {
                mcpRegisteredStatus.textContent = 'Registered';
                mcpRegisteredStatus.className = 'status-tag success';
                
                const config = data.server_config || {};
                mcpActiveConfig.textContent = JSON.stringify(config, null, 2);
                
                if (config.command === 'npx' || (config.args && config.args.includes('mcp-remote'))) {
                    document.querySelector('input[name="mcpTransport"][value="sse"]').checked = true;
                    mcpStdioConfig.style.display = 'none';
                    mcpSseConfig.style.display = 'block';
                    if (config.args && config.args.length >= 3) {
                        mcpSseUrl.value = config.args[2];
                    }
                } else {
                    document.querySelector('input[name="mcpTransport"][value="stdio"]').checked = true;
                    mcpStdioConfig.style.display = 'block';
                    mcpSseConfig.style.display = 'none';
                    if (config.env) {
                        mcpUserId.value = config.env.PB_DEFAULT_USER_ID || config.env.OM_DEFAULT_USER_ID || 'default';
                        mcpTags.value = config.env.PB_DEFAULT_TAGS || config.env.OM_DEFAULT_TAGS || 'source:mcp';
                    }
                }
            } else {
                mcpRegisteredStatus.textContent = 'Not Registered';
                mcpRegisteredStatus.className = 'status-tag danger';
                mcpActiveConfig.textContent = '{}';
            }
        } catch (err) {
            console.error("MCP status error:", err);
            mcpFileStatus.textContent = 'Error';
            mcpFileStatus.className = 'status-tag danger';
            mcpRegisteredStatus.textContent = 'Error';
            mcpRegisteredStatus.className = 'status-tag danger';
        }
        await loadExternalMcpServers();
    }

    async function registerMcp() {
        const transport = document.querySelector('input[name="mcpTransport"]:checked').value;
        const payload = {
            transport_type: transport,
            user_id: mcpUserId.value.trim() || 'default',
            tags: mcpTags.value.trim() || 'source:mcp',
            sse_url: mcpSseUrl.value.trim() || 'http://localhost:8080/mcp/sse'
        };
        
        btnRegisterMcp.disabled = true;
        btnRegisterMcp.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Registering...`;
        
        try {
            const res = await fetch('/memory/mcp/register', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            
            if (!res.ok) throw new Error("Failed to register MCP server.");
            const data = await res.json();
            
            alert("Successfully applied configuration! Make sure to fully restart Claude Desktop for the changes to take effect.");
            await loadMcpStatus();
        } catch (err) {
            alert("Registration failed: " + err.message);
        } finally {
            btnRegisterMcp.disabled = false;
            btnRegisterMcp.innerHTML = `<i class="fa-solid fa-circle-check"></i> Install & Apply Configuration`;
        }
    }

    async function unregisterMcp() {
        if (!confirm("Are you sure you want to remove ProjectBrain integration from Claude Desktop?")) return;
        
        btnUnregisterMcp.disabled = true;
        btnUnregisterMcp.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Removing...`;
        
        try {
            const res = await fetch('/memory/mcp/unregister', {
                method: 'POST'
            });
            
            if (!res.ok) throw new Error("Failed to remove integration.");
            
            alert("Successfully removed ProjectBrain integration from Claude Desktop config.");
            await loadMcpStatus();
        } catch (err) {
            alert("Removal failed: " + err.message);
        } finally {
            btnUnregisterMcp.disabled = false;
            btnUnregisterMcp.innerHTML = `<i class="fa-solid fa-circle-xmark"></i> Remove Integration`;
        }
    }

    async function loadExternalMcpServers() {
        try {
            const res = await fetch('/memory/mcp/external');
            if (!res.ok) throw new Error("Failed to load external MCP servers");
            const data = await res.json();
            const servers = data.servers || {};
            
            const externalMcpList = document.getElementById('externalMcpList');
            externalMcpList.innerHTML = '';
            
            const serverNames = Object.keys(servers);
            if (serverNames.length === 0) {
                externalMcpList.innerHTML = '<div class="placeholder-item">No external MCP servers registered yet.</div>';
                return;
            }
            
            serverNames.forEach(name => {
                const s = servers[name];
                const item = document.createElement('div');
                item.className = 'ext-mcp-item';
                
                const argsStr = JSON.stringify(s.args || []);
                const envStr = JSON.stringify(s.env || {});
                
                item.innerHTML = `
                    <div class="ext-mcp-details">
                        <div class="ext-mcp-title">${escapeHTML(name)}</div>
                        <div class="ext-mcp-sub">Command: ${escapeHTML(s.command)}</div>
                        <div class="ext-mcp-sub" style="font-size:0.75rem; color:var(--text-secondary);">Args: ${escapeHTML(argsStr)}</div>
                        ${Object.keys(s.env || {}).length > 0 ? `<div class="ext-mcp-sub" style="font-size:0.75rem; color:var(--text-secondary);">Env: ${escapeHTML(envStr)}</div>` : ''}
                    </div>
                    <button class="btn btn-danger btn-sm btn-delete-ext" data-name="${escapeHTML(name)}"><i class="fa-solid fa-trash"></i> Delete</button>
                `;
                
                item.querySelector('.btn-delete-ext').addEventListener('click', () => deleteExternalMcp(name));
                externalMcpList.appendChild(item);
            });
        } catch (err) {
            console.error("Failed to load external MCP servers:", err);
        }
    }

    async function saveExternalMcp() {
        const name = document.getElementById('extMcpName').value.trim();
        const command = document.getElementById('extMcpCommand').value.trim();
        const argsRaw = document.getElementById('extMcpArgs').value.trim();
        const envRaw = document.getElementById('extMcpEnv').value.trim();
        
        if (!name || !command) {
            return alert("Server name and command are required.");
        }
        
        let args = [];
        try {
            args = JSON.parse(argsRaw);
            if (!Array.isArray(args)) throw new Error("Must be a JSON array");
        } catch (e) {
            return alert("Arguments must be a valid JSON array, e.g. [\"--arg1\", \"--arg2\"]");
        }
        
        let env = {};
        try {
            env = JSON.parse(envRaw);
            if (typeof env !== 'object' || Array.isArray(env)) throw new Error("Must be a JSON object");
        } catch (e) {
            return alert("Environment variables must be a valid JSON object, e.g. {\"KEY\": \"VALUE\"}");
        }
        
        const btnSave = document.getElementById('btnSaveExtMcp');
        btnSave.disabled = true;
        btnSave.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Saving...`;
        
        try {
            const res = await fetch('/memory/mcp/external', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, command, args, env })
            });
            if (!res.ok) {
                const errData = await res.json();
                throw new Error(errData.detail || "Failed to save external MCP server.");
            }
            
            // Clear inputs
            document.getElementById('extMcpName').value = '';
            document.getElementById('extMcpCommand').value = '';
            document.getElementById('extMcpArgs').value = '[]';
            document.getElementById('extMcpEnv').value = '{}';
            
            alert("Successfully saved MCP server! Make sure to fully restart Claude Desktop for changes to take effect.");
            await loadExternalMcpServers();
        } catch (err) {
            alert(err.message);
        } finally {
            btnSave.disabled = false;
            btnSave.innerHTML = `<i class="fa-solid fa-save"></i> Save MCP Server`;
        }
    }

    async function deleteExternalMcp(name) {
        if (!confirm(`Are you sure you want to remove external MCP server '${name}'?`)) return;
        
        try {
            const res = await fetch(`/memory/mcp/external/${encodeURIComponent(name)}`, {
                method: 'DELETE'
            });
            if (!res.ok) throw new Error("Failed to delete external MCP server.");
            
            alert("Successfully deleted MCP server! Restart Claude Desktop for changes to take effect.");
            await loadExternalMcpServers();
        } catch (err) {
            alert(err.message);
        }
    }

    // Code Graph Drawing and Fetching
    async function loadCodeGraph() {
        graphLoader.style.display = 'flex';
        graphEmptyState.style.display = 'none';
        nodeDetailsPanel.style.display = 'none';
        
        const q = cgSearchInput.value.trim();
        const k = cgKindSelect.value;
        const pId = cgProjectSelect.value;
        
        try {
            const url = `/codegraph/data?project_id=${encodeURIComponent(pId)}&query=${encodeURIComponent(q)}&kinds=${encodeURIComponent(k)}`;
            const res = await fetch(url);
            if (!res.ok) throw new Error("Failed to load codegraph data.");
            
            const data = await res.json();
            cgNodesList = data.nodes || [];
            const edges = data.edges || [];
            
            if (cgNodesList.length === 0) {
                graphEmptyState.style.display = 'flex';
                if (cgNetwork) {
                    cgNetwork.destroy();
                    cgNetwork = null;
                }
                return;
            }
            
            renderCodeGraph(cgNodesList, edges);
        } catch (err) {
            console.error("CodeGraph Error:", err);
            graphEmptyState.style.display = 'flex';
            graphEmptyState.querySelector('p').textContent = "Error loading graph: " + err.message;
        } finally {
            graphLoader.style.display = 'none';
        }
    }
    
    function renderCodeGraph(nodes, edges) {
        const networkContainer = document.getElementById('codegraphNetwork');
        
        // Map kinds to shapes/colors
        const kindColors = {
            'class': { background: '#312e81', border: '#6366f1', highlight: { background: '#4338ca', border: '#818cf8' } },
            'function': { background: '#064e3b', border: '#10b981', highlight: { background: '#047857', border: '#34d399' } },
            'method': { background: '#1e3a8a', border: '#3b82f6', highlight: { background: '#1d4ed8', border: '#60a5fa' } },
            'file': { background: '#7c2d12', border: '#ea580c', highlight: { background: '#9a3412', border: '#fb923c' } },
            'import': { background: '#374151', border: '#9ca3af', highlight: { background: '#4b5563', border: '#d1d5db' } }
        };
        
        const defaultColor = { background: '#1f2937', border: '#4b5563', highlight: { background: '#374151', border: '#9ca3af' } };
        
        const visNodes = nodes.map(n => {
            const colors = kindColors[n.kind] || defaultColor;
            return {
                id: n.id,
                label: n.name,
                title: `${n.qualified_name} (${n.kind})`,
                shape: n.kind === 'file' ? 'box' : 'dot',
                size: n.kind === 'class' ? 22 : (n.kind === 'file' ? 18 : 14),
                color: colors,
                font: {
                    color: '#f3f4f6',
                    size: n.kind === 'class' ? 14 : 11,
                    face: 'Outfit'
                }
            };
        });
        
        const visEdges = edges.map(e => {
            return {
                id: e.id,
                from: e.source,
                to: e.target,
                label: e.kind,
                arrows: 'to',
                color: { color: 'rgba(156, 163, 175, 0.4)', highlight: '#818cf8', hover: '#34d399' },
                font: { size: 9, color: '#9ca3af', strokeWidth: 0, face: 'Outfit' },
                smooth: { type: 'continuous' }
            };
        });
        
        const visData = {
            nodes: new vis.DataSet(visNodes),
            edges: new vis.DataSet(visEdges)
        };
        
        const options = {
            physics: {
                stabilization: { iterations: 150 },
                barnesHut: {
                    gravitationalConstant: -1800,
                    centralGravity: 0.25,
                    springLength: 90,
                    springConstant: 0.04,
                    damping: 0.09,
                    avoidOverlap: 0.4
                }
            },
            interaction: {
                hover: true,
                tooltipDelay: 150,
                selectConnectedEdges: true
            }
        };
        
        if (cgNetwork) {
            cgNetwork.destroy();
        }
        
        cgNetwork = new vis.Network(networkContainer, visData, options);
        
        cgNetwork.on('click', (params) => {
            if (params.nodes.length > 0) {
                const nodeId = params.nodes[0];
                const node = cgNodesList.find(n => n.id === nodeId);
                if (node) {
                    showNodeDetails(node);
                }
            } else {
                nodeDetailsPanel.style.display = 'none';
            }
        });
    }
    
    function showNodeDetails(node) {
        ndName.textContent = node.name;
        ndKind.textContent = node.kind.toUpperCase();
        ndKind.className = `status-tag info`;
        
        ndFilePath.textContent = node.file_path;
        
        if (node.start_line && node.end_line) {
            ndLineRow.style.display = 'flex';
            ndLines.textContent = `Lines ${node.start_line} - ${node.end_line}`;
        } else {
            ndLineRow.style.display = 'none';
        }
        
        if (node.signature) {
            ndSignatureRow.style.display = 'flex';
            ndSignature.textContent = node.signature;
        } else {
            ndSignatureRow.style.display = 'none';
        }
        
        if (node.docstring) {
            ndDocstringRow.style.display = 'flex';
            ndDocstring.textContent = node.docstring;
        } else {
            ndDocstringRow.style.display = 'none';
        }
        
        nodeDetailsPanel.style.display = 'flex';
    }

    async function loadProjects() {
        try {
            const res = await fetch('/codegraph/projects');
            if (!res.ok) throw new Error("Failed to load projects.");
            const data = await res.json();
            const projects = data.projects || [];
            
            // Populate cgProjectSelect
            const currentVal = cgProjectSelect.value;
            cgProjectSelect.innerHTML = '<option value="">Local Codegraph (Default)</option>';
            
            // Populate currentProjectSelect
            const currentGlobalVal = currentProjectSelect.value;
            currentProjectSelect.innerHTML = '<option value="default">default</option>';
            
            // Populate base and target selects
            const currentBaseVal = diffBaseProject.value;
            const currentTargetVal = diffTargetProject.value;
            diffBaseProject.innerHTML = '<option value="">Select Base Project...</option>';
            diffTargetProject.innerHTML = '<option value="">Select Target Project...</option>';
            
            projects.forEach(p => {
                const authorStr = p.sync_author ? ` by ${p.sync_author}` : '';
                const ipSuffix = (p.sync_ip || p.sync_author) ? ` (${p.sync_ip || ''}${authorStr})` : '';
                
                // Code Graph dropdown
                const opt = document.createElement('option');
                opt.value = p.id;
                opt.textContent = `${p.name}${ipSuffix} (Server)`;
                cgProjectSelect.appendChild(opt);
                
                // Global active dropdown
                const optGlobal = document.createElement('option');
                optGlobal.value = p.id;
                optGlobal.textContent = `${p.name}${ipSuffix}`;
                currentProjectSelect.appendChild(optGlobal);
                
                // Diff dropdowns
                const optBase = document.createElement('option');
                optBase.value = p.id;
                optBase.textContent = `${p.name}${ipSuffix}`;
                diffBaseProject.appendChild(optBase);
                
                const optTarget = document.createElement('option');
                optTarget.value = p.id;
                optTarget.textContent = `${p.name}${ipSuffix}`;
                diffTargetProject.appendChild(optTarget);
            });
            
            if (currentVal && Array.from(cgProjectSelect.options).some(o => o.value === currentVal)) {
                cgProjectSelect.value = currentVal;
            }
            if (currentGlobalVal && Array.from(currentProjectSelect.options).some(o => o.value === currentGlobalVal)) {
                currentProjectSelect.value = currentGlobalVal;
            }
            if (currentBaseVal && Array.from(diffBaseProject.options).some(o => o.value === currentBaseVal)) {
                diffBaseProject.value = currentBaseVal;
            }
            if (currentTargetVal && Array.from(diffTargetProject.options).some(o => o.value === currentTargetVal)) {
                diffTargetProject.value = currentTargetVal;
            }
        } catch (err) {
            console.error("Failed to load projects:", err);
        }
    }

    async function addProject() {
        const projectId = prompt("Enter unique Project ID (e.g. 'projectbrain-py:dev' or 'my-project'):");
        if (!projectId) return;
        const name = prompt("Enter Project Name:", projectId);
        if (!name) return;
        
        try {
            const res = await fetch('/codegraph/projects', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id: projectId, name: name, description: `Manual project ${name}` })
            });
            if (!res.ok) throw new Error("Failed to register project");
            alert("Project registered successfully!");
            await loadProjects();
            currentProjectSelect.value = projectId;
            currentProjectSelect.dispatchEvent(new Event('change'));
        } catch (err) {
            alert("Error registering project: " + err.message);
        }
    }

    async function compareVersions() {
        const base = diffBaseProject.value;
        const target = diffTargetProject.value;
        if (!base || !target) {
            alert("Please select both base and target versions.");
            return;
        }
        
        btnCompareVersions.disabled = true;
        btnCompareVersions.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Comparing...`;
        diffResultsArea.style.display = 'none';
        
        try {
            // Fetch structural/codegraph diff
            const res = await fetch(`/codegraph/diff?base_project_id=${encodeURIComponent(base)}&target_project_id=${encodeURIComponent(target)}`);
            if (!res.ok) throw new Error("Failed to fetch project diff data");
            const diffData = await res.json();
            
            // Render diff counts
            diffCountAdded.textContent = diffData.added.length;
            diffCountDeleted.textContent = diffData.deleted.length;
            diffCountModified.textContent = diffData.modified.length;
            
            // Render codebase changes (cg list)
            diffCgList.innerHTML = '';
            if (diffData.added.length === 0 && diffData.deleted.length === 0 && diffData.modified.length === 0) {
                diffCgList.innerHTML = '<div class="empty-placeholder">No codebase changes.</div>';
            } else {
                // Render added symbols
                diffData.added.forEach(node => {
                    const el = document.createElement('div');
                    el.className = 'diff-item';
                    el.innerHTML = `
                        <div class="diff-item-header">
                            <span class="diff-item-title">${escapeHTML(node.name)}</span>
                            <span class="diff-change-badge added">Added</span>
                        </div>
                        <div class="diff-item-sub">${escapeHTML(node.kind.toUpperCase())} • ${escapeHTML(node.file_path)}</div>
                    `;
                    diffCgList.appendChild(el);
                });
                
                // Render deleted symbols
                diffData.deleted.forEach(node => {
                    const el = document.createElement('div');
                    el.className = 'diff-item';
                    el.innerHTML = `
                        <div class="diff-item-header">
                            <span class="diff-item-title">${escapeHTML(node.name)}</span>
                            <span class="diff-change-badge deleted">Deleted</span>
                        </div>
                        <div class="diff-item-sub">${escapeHTML(node.kind.toUpperCase())} • ${escapeHTML(node.file_path)}</div>
                    `;
                    diffCgList.appendChild(el);
                });
                
                // Render modified symbols
                diffData.modified.forEach(mod => {
                    const node = mod.node;
                    const el = document.createElement('div');
                    el.className = 'diff-item';
                    let detailHtml = '';
                    if (mod.changes && mod.changes.length > 0) {
                        detailHtml = `<div class="diff-item-detail">Changed: ${escapeHTML(mod.changes.join(', '))}</div>`;
                    }
                    el.innerHTML = `
                        <div class="diff-item-header">
                            <span class="diff-item-title">${escapeHTML(node.name)}</span>
                            <span class="diff-change-badge modified">Modified</span>
                        </div>
                        <div class="diff-item-sub">${escapeHTML(node.kind.toUpperCase())} • ${escapeHTML(node.file_path)}</div>
                        ${detailHtml}
                    `;
                    diffCgList.appendChild(el);
                });
            }
            
            // Diff memories by fetching history
            const baseMemsRes = await fetch(`/memory/history?user_id=${encodeURIComponent(base)}&limit=200`);
            const targetMemsRes = await fetch(`/memory/history?user_id=${encodeURIComponent(target)}&limit=200`);
            
            const baseMemsData = baseMemsRes.ok ? await baseMemsRes.json() : { history: [] };
            const targetMemsData = targetMemsRes.ok ? await targetMemsRes.json() : { history: [] };
            
            const baseHistory = baseMemsData.history || [];
            const targetHistory = targetMemsData.history || [];
            
            const baseContents = baseHistory.map(m => m.content.trim());
            const targetContents = targetHistory.map(m => m.content.trim());
            
            const addedMems = targetHistory.filter(m => !baseContents.includes(m.content.trim()));
            const deletedMems = baseHistory.filter(m => !targetContents.includes(m.content.trim()));
            
            diffOmList.innerHTML = '';
            if (addedMems.length === 0 && deletedMems.length === 0) {
                diffOmList.innerHTML = '<div class="empty-placeholder">No memory changes.</div>';
            } else {
                addedMems.forEach(m => {
                    const el = document.createElement('div');
                    el.className = 'diff-item';
                    el.innerHTML = `
                        <div class="diff-item-header">
                            <span class="diff-item-title">[${escapeHTML(m.primary_sector)}]</span>
                            <span class="diff-change-badge added">Added</span>
                        </div>
                        <div class="diff-item-sub">${escapeHTML(m.content)}</div>
                    `;
                    diffOmList.appendChild(el);
                });
                
                deletedMems.forEach(m => {
                    const el = document.createElement('div');
                    el.className = 'diff-item';
                    el.innerHTML = `
                        <div class="diff-item-header">
                            <span class="diff-item-title">[${escapeHTML(m.primary_sector)}]</span>
                            <span class="diff-change-badge deleted">Deleted</span>
                        </div>
                        <div class="diff-item-sub">${escapeHTML(m.content)}</div>
                    `;
                    diffOmList.appendChild(el);
                });
            }
            
            diffResultsArea.style.display = 'block';
        } catch (err) {
            alert("Error running comparison: " + err.message);
        } finally {
            btnCompareVersions.disabled = false;
            btnCompareVersions.innerHTML = `<i class="fa-solid fa-arrow-right-arrow-left"></i> Run Comparison`;
        }
    }

    function handleFileSelect() {
        if (docUploadFile.files.length > 0) {
            const file = docUploadFile.files[0];
            uploadStatusText.innerHTML = `<strong>Selected:</strong> ${escapeHTML(file.name)} (${(file.size/1024).toFixed(1)} KB)`;
        } else {
            uploadStatusText.textContent = "Click to select PDF, DOCX, XLSX, TXT";
        }
    }

    async function uploadDocument() {
        if (docUploadFile.files.length === 0) {
            return alert("Please select a file to upload first.");
        }
        
        const file = docUploadFile.files[0];
        const tags = docUploadTags.value.trim();
        const activeProj = currentProjectSelect.value || 'default';
        
        btnUploadDoc.disabled = true;
        btnUploadDoc.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Uploading...`;
        
        const formData = new FormData();
        formData.append('file', file);
        formData.append('project_id', activeProj);
        formData.append('tags', tags);
        formData.append('author', currentUser.value.trim());
        
        try {
            const res = await fetch('/sources/upload', {
                method: 'POST',
                body: formData
            });
            
            if (!res.ok) {
                const errData = await res.json();
                throw new Error(errData.detail || "Upload failed");
            }
            
            alert(`Document '${file.name}' uploaded and parsed successfully!`);
            
            // Clear file and tags inputs
            docUploadFile.value = '';
            docUploadTags.value = '';
            uploadStatusText.textContent = "Click to select PDF, DOCX, XLSX, TXT";
            
            // Reload stats and memory feed
            loadStats();
            loadMemories();
        } catch (err) {
            alert("Upload error: " + err.message);
        } finally {
            btnUploadDoc.disabled = false;
            btnUploadDoc.innerHTML = `<i class="fa-solid fa-upload"></i> Upload`;
        }
    }
});
