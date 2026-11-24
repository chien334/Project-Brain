document.addEventListener('DOMContentLoaded', () => {
    // =========================================================================
    // 1. Safe Storage & Authenticated Fetch Wrapper
    // =========================================================================
    function createSafeStorage(backingStore) {
        return {
            _fallback: {},
            getItem(key) {
                try {
                    return backingStore.getItem(key);
                } catch (_) {
                    return Object.prototype.hasOwnProperty.call(this._fallback, key)
                        ? this._fallback[key]
                        : null;
                }
            },
            setItem(key, value) {
                try {
                    backingStore.setItem(key, value);
                } catch (_) {
                    this._fallback[key] = String(value);
                }
            },
            removeItem(key) {
                try {
                    backingStore.removeItem(key);
                } catch (_) {
                    delete this._fallback[key];
                }
            }
        };
    }

    const safeStorage = createSafeStorage(window.localStorage);
    const safeSessionStorage = createSafeStorage(window.sessionStorage);
    const localStorage = safeStorage;

    function getStoredToken() {
        return localStorage.getItem('pb_auth_token') || safeSessionStorage.getItem('pb_auth_token') || '';
    }

    function setStoredToken(token) {
        if (token) {
            localStorage.setItem('pb_auth_token', token);
        } else {
            localStorage.removeItem('pb_auth_token');
            safeSessionStorage.removeItem('pb_auth_token');
        }
    }

    const originalFetch = window.fetch.bind(window);
    window.fetch = async function (url, options = {}) {
        const nextOptions = { ...options };
        if (!nextOptions.credentials) {
            nextOptions.credentials = 'same-origin';
        }
        const token = getStoredToken();
        if (token) {
            if (nextOptions.headers instanceof Headers) {
                if (!nextOptions.headers.has('Authorization')) {
                    nextOptions.headers.set('Authorization', `Bearer ${token}`);
                }
            } else {
                const headers = { ...(nextOptions.headers || {}) };
                if (!headers['Authorization'] && !headers['authorization']) {
                    headers['Authorization'] = `Bearer ${token}`;
                }
                nextOptions.headers = headers;
            }
        }
        return originalFetch(url, nextOptions);
    };

    function toDisplayText(val, fallback = '') {
        if (val === null || val === undefined) return fallback;
        if (typeof val === 'string' || typeof val === 'number' || typeof val === 'boolean') {
            return String(val);
        }
        if (Array.isArray(val)) {
            const joined = val.map(item => toDisplayText(item, '')).filter(Boolean).join(', ');
            return joined || fallback;
        }
        if (typeof val === 'object') {
            const preferred = [
                val.label,
                val.qualified_name,
                val.name,
                val.title,
                val.backend,
                val.device_str,
                val.device,
                val.engine,
                val.provider,
                val.id,
                val.doc_key,
                val.heading_breadcrumb,
                val.detail,
                val.message
            ].find(v => v !== undefined && v !== null && v !== '');
            if (preferred !== undefined) {
                return toDisplayText(preferred, fallback);
            }
            const entries = Object.entries(val)
                .filter(([, v]) => v !== undefined && v !== null && v !== '')
                .slice(0, 6)
                .map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : String(v)}`);
            if (entries.length > 0) return entries.join(', ');
            try {
                return JSON.stringify(val);
            } catch (_) {
                return fallback;
            }
        }
        return String(val);
    }

    function formatEpochTimestamp(val, fallback = 'N/A', dateOnly = false) {
        if (val === null || val === undefined || val === '' || val === 0 || val === '0') {
            return fallback;
        }
        const raw = Number(val);
        const dt = Number.isFinite(raw)
            ? new Date(raw > 1e11 ? raw : raw * 1000)
            : new Date(val);
        if (Number.isNaN(dt.getTime())) return fallback;
        return dateOnly ? dt.toLocaleDateString() : dt.toLocaleString();
    }

    function formatDetailsSummary(details) {
        if (details === null || details === undefined || details === '') return '-';
        let obj = details;
        if (typeof details === 'string') {
            const trimmed = details.trim();
            if (!trimmed) return '-';
            if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
                try {
                    obj = JSON.parse(trimmed);
                } catch (_) {
                    return trimmed;
                }
            } else {
                return trimmed;
            }
        }
        if (Array.isArray(obj)) {
            return toDisplayText(obj, '-');
        }
        if (obj && typeof obj === 'object') {
            const pairs = Object.entries(obj)
                .filter(([, v]) => v !== undefined && v !== null && v !== '')
                .map(([k, v]) => `${k}=${typeof v === 'object' ? toDisplayText(v, JSON.stringify(v)) : String(v)}`);
            return pairs.length > 0 ? pairs.join(', ') : '-';
        }
        return String(obj);
    }

    async function extractErrorMessage(res, fallback = 'Request failed.') {
        try {
            const data = await res.json();
            if (Array.isArray(data?.detail)) {
                return data.detail.map(item => item.msg || toDisplayText(item)).join('; ');
            }
            if (data?.detail) return toDisplayText(data.detail, fallback);
            if (data?.message) return toDisplayText(data.message, fallback);
        } catch (_) {
            // Ignore non-JSON error body
        }
        return fallback;
    }

    function sha256Hex(input) {
        const bytes = new TextEncoder().encode(String(input ?? ''));
        const k = [
            0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
            0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
            0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
            0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
            0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
            0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
            0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
            0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
        ];
        const h = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19];
        const bitLen = bytes.length * 8;
        const paddedLen = Math.ceil((bytes.length + 9) / 64) * 64;
        const padded = new Uint8Array(paddedLen);
        padded.set(bytes);
        padded[bytes.length] = 0x80;
        const view = new DataView(padded.buffer);
        view.setUint32(paddedLen - 8, Math.floor(bitLen / 0x100000000), false);
        view.setUint32(paddedLen - 4, bitLen >>> 0, false);

        const w = new Uint32Array(64);
        const rotr = (x, n) => (x >>> n) | (x << (32 - n));
        for (let offset = 0; offset < paddedLen; offset += 64) {
            for (let i = 0; i < 16; i++) {
                w[i] = view.getUint32(offset + i * 4, false);
            }
            for (let i = 16; i < 64; i++) {
                const s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
                const s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
                w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0;
            }
            let [a, b, c, d, e, f, g, hh] = h;
            for (let i = 0; i < 64; i++) {
                const S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
                const ch = (e & f) ^ ((~e) & g);
                const temp1 = (hh + S1 + ch + k[i] + w[i]) >>> 0;
                const S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
                const maj = (a & b) ^ (a & c) ^ (b & c);
                const temp2 = (S0 + maj) >>> 0;
                hh = g; g = f; f = e; e = (d + temp1) >>> 0;
                d = c; c = b; b = a; a = (temp1 + temp2) >>> 0;
            }
            h[0] = (h[0] + a) >>> 0; h[1] = (h[1] + b) >>> 0; h[2] = (h[2] + c) >>> 0; h[3] = (h[3] + d) >>> 0;
            h[4] = (h[4] + e) >>> 0; h[5] = (h[5] + f) >>> 0; h[6] = (h[6] + g) >>> 0; h[7] = (h[7] + hh) >>> 0;
        }
        return h.map(v => v.toString(16).padStart(8, '0')).join('');
    }

    function sanitizeProjectDbName(projectId) {
        const raw = (projectId || 'default').trim() || 'default';
        let cleanProj = raw.toLowerCase().replace(/[^a-z0-9_]/g, '_').replace(/^_+|_+$/g, '');
        if (!cleanProj) cleanProj = 'proj';
        cleanProj = cleanProj.slice(0, 36).replace(/_+$/g, '') || 'proj';
        const hash8 = sha256Hex(raw).slice(0, 8);
        let dbName = `pb_proj_${cleanProj}_${hash8}`;
        if (dbName.length > 60) dbName = dbName.slice(0, 60);
        return dbName;
    }

    function sanitizeBranchSchemaName(branchName, rootBranch = 'main') {
        const effRoot = (rootBranch || 'main').trim() || 'main';
        const rawBranch = (branchName || '').trim();
        if (!rawBranch || rawBranch === effRoot) return 'pb_root';
        let cleanBranch = rawBranch.toLowerCase().replace(/[^a-z0-9_]/g, '_').replace(/^_+|_+$/g, '');
        if (!cleanBranch) cleanBranch = 'branch';
        cleanBranch = cleanBranch.slice(0, 36).replace(/_+$/g, '') || 'branch';
        const hash8 = sha256Hex(rawBranch).slice(0, 8);
        let schemaName = `pb_br_${cleanBranch}_${hash8}`;
        if (schemaName.length > 60) schemaName = schemaName.slice(0, 60);
        return schemaName;
    }

    // =========================================================================
    // 2. DOM References
    // =========================================================================
    const dashboardContainer = document.querySelector('.dashboard-container');
    const btnToggleSidebar = document.getElementById('btnToggleSidebar');
    const breadcrumbDomainLink = document.getElementById('breadcrumbDomainLink');
    const breadcrumbPageLink = document.getElementById('breadcrumbPageLink');
    const activeRoutePathBadge = document.getElementById('activeRoutePathBadge');
    const btnCopyRouteUrl = document.getElementById('btnCopyRouteUrl');
    const domainSubnavBar = document.getElementById('domainSubnavBar');
    const domainSubnavList = document.getElementById('domainSubnavList');
    const backendStatus = document.getElementById('backendStatus');
    const headerHardwareBadge = document.getElementById('headerHardwareBadge');
    const btnThemeToggle = document.getElementById('btnThemeToggle');

    // Workspace context bar
    const currentProjectSelect = document.getElementById('currentProject');
    const currentBranchSelect = document.getElementById('currentBranch');
    const branchSelectorContainer = document.getElementById('branchSelectorContainer');
    const btnAddProject = document.getElementById('btnAddProject');
    const btnOpenCommandPalette = document.getElementById('btnOpenCommandPalette');

    // User profile header widget
    const userProfileWidget = document.getElementById('userProfileWidget');
    const userAvatarInitials = document.getElementById('userAvatarInitials');
    const userProfileName = document.getElementById('userProfileName');
    const userProfileRole = document.getElementById('userProfileRole');
    const currentUser = document.getElementById('currentUser');
    const btnQuickProfile = document.getElementById('btnQuickProfile');
    const btnLogout = document.getElementById('btnLogout');

    // Auth Gate Elements
    const loginScreen = document.getElementById('loginScreen');
    const tabAuthPassword = document.getElementById('tabAuthPassword');
    const tabAuthToken = document.getElementById('tabAuthToken');
    const tabAuthRegister = document.getElementById('tabAuthRegister');
    const authModePasswordPane = document.getElementById('authModePasswordPane');
    const authModeTokenPane = document.getElementById('authModeTokenPane');
    const authModeRegisterPane = document.getElementById('authModeRegisterPane');
    const loginError = document.getElementById('loginError');
    const loginErrorMsg = document.getElementById('loginErrorMsg');

    const passwordLoginForm = document.getElementById('passwordLoginForm');
    const loginIdentifier = document.getElementById('loginIdentifier');
    const loginPassword = document.getElementById('loginPassword');
    const loginRememberMe = document.getElementById('loginRememberMe');
    const btnPasswordLoginSubmit = document.getElementById('btnPasswordLoginSubmit');

    const loginForm = document.getElementById('loginForm');
    const loginToken = document.getElementById('loginToken');
    const btnLoginSubmit = document.getElementById('btnLoginSubmit');

    const registerForm = document.getElementById('registerForm');
    const regUsername = document.getElementById('regUsername');
    const regDisplayName = document.getElementById('regDisplayName');
    const regEmail = document.getElementById('regEmail');
    const regPassword = document.getElementById('regPassword');
    const btnRegisterSubmit = document.getElementById('btnRegisterSubmit');

    // Navigation & Tabs
    const tabLinks = document.querySelectorAll('.menu-link');
    const tabContents = document.querySelectorAll('.tab-content');
    const btnTabAuth = document.getElementById('btnTabAuth');
    const btnTabProfile = document.getElementById('btnTabProfile');

    // Overview Dashboard
    const securitySeedBanner = document.getElementById('securitySeedBanner');
    const btnBannerSetPassword = document.getElementById('btnBannerSetPassword');
    const totalMemories = document.getElementById('totalMemories');
    const totalFacts = document.getElementById('totalFacts');
    const statActivePlugins = document.getElementById('statActivePlugins');
    const statComputeBackend = document.getElementById('statComputeBackend');
    const hwDashOcrDevice = document.getElementById('hwDashOcrDevice');
    const hwDashGraphBackend = document.getElementById('hwDashGraphBackend');
    const hwDashAccelerators = document.getElementById('hwDashAccelerators');
    const hwDashLocalAi = document.getElementById('hwDashLocalAi');
    const sectorCounts = document.getElementById('sectorCounts');
    const activeTags = document.getElementById('activeTags');
    const btnDeleteAll = document.getElementById('btnDeleteAll');

    // Memories & Docs
    const newMemoryContent = document.getElementById('newMemoryContent');
    const newMemoryTags = document.getElementById('newMemoryTags');
    const btnSaveMemory = document.getElementById('btnSaveMemory');
    const btnRefreshHistory = document.getElementById('btnRefreshHistory');
    const memoryFilterInput = document.getElementById('memoryFilterInput');
    const memoriesFeed = document.getElementById('memoriesFeed');

    const docUploadFile = document.getElementById('docUploadFile');
    const docUploadTags = document.getElementById('docUploadTags');
    const docOcrEngine = document.getElementById('docOcrEngine');
    const docOcrDevice = document.getElementById('docOcrDevice');
    const btnUploadDoc = document.getElementById('btnUploadDoc');
    const uploadStatusText = document.getElementById('uploadStatusText');
    const uploadDropzone = document.getElementById('uploadDropzone');
    const btnRefreshDocs = document.getElementById('btnRefreshDocs');
    const ingestedDocsList = document.getElementById('ingestedDocsList');
    const activeTenantStorageHint = document.getElementById('activeTenantStorageHint');

    // Projects, Databases & Topology Explorer
    const btnRefreshTopology = document.getElementById('btnRefreshTopology');
    const topoMasterDb = document.getElementById('topoMasterDb');
    const topoMasterMeta = document.getElementById('topoMasterMeta');
    const topoProjectDbCount = document.getElementById('topoProjectDbCount');
    const topoBranchSchemaCount = document.getElementById('topoBranchSchemaCount');
    const projectProvisionCard = document.getElementById('projectProvisionCard');
    const regProjectIdInput = document.getElementById('regProjectId');
    const regDbNamePreview = document.getElementById('regDbNamePreview');

    // Search
    const searchQuery = document.getElementById('searchQuery');
    const btnSearch = document.getElementById('btnSearch');
    const searchResults = document.getElementById('searchResults');
    const searchActiveAiBadge = document.getElementById('searchActiveAiBadge');
    const btnOpenAiSettingsSearch = document.getElementById('btnOpenAiSettingsSearch');

    // PM Copilot
    const docType = document.getElementById('docType');
    const copilotPrompt = document.getElementById('copilotPrompt');
    const btnGenerateDoc = document.getElementById('btnGenerateDoc');
    const copilotResults = document.getElementById('copilotResults');
    const generatedDocContent = document.getElementById('generatedDocContent');
    const docSourcesFeed = document.getElementById('docSourcesFeed');
    const btnCopyDoc = document.getElementById('btnCopyDoc');
    const copilotActiveAiBadge = document.getElementById('copilotActiveAiBadge');
    const btnOpenAiSettingsCopilot = document.getElementById('btnOpenAiSettingsCopilot');
    const btnOpenAiSettingsOverview = document.getElementById('btnOpenAiSettingsOverview');

    // MCP Integration
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
    const btnSaveExtMcp = document.getElementById('btnSaveExtMcp');

    // Code Graph
    const cgProjectSelect = document.getElementById('cgProjectSelect');
    const cgSearchInput = document.getElementById('cgSearchInput');
    const cgKindSelect = document.getElementById('cgKindSelect');
    const btnLoadCodeGraph = document.getElementById('btnLoadCodeGraph');
    const graphLayout = document.getElementById('graphLayout');
    const graphContainer = document.getElementById('graphContainer');
    const graphHudCount = document.getElementById('graphHudCount');
    const btnGraphZoomIn = document.getElementById('btnGraphZoomIn');
    const btnGraphZoomOut = document.getElementById('btnGraphZoomOut');
    const btnGraphFit = document.getElementById('btnGraphFit');
    const btnGraphPhysics = document.getElementById('btnGraphPhysics');
    const btnGraphFullscreen = document.getElementById('btnGraphFullscreen');
    const btnCloseNodeDetails = document.getElementById('btnCloseNodeDetails');
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

    // Branch Diff
    const diffBaseProject = document.getElementById('diffBaseProject');
    const diffTargetProject = document.getElementById('diffTargetProject');
    const btnCompareVersions = document.getElementById('btnCompareVersions');
    const diffResultsArea = document.getElementById('diffResultsArea');
    const diffCountAdded = document.getElementById('diffCountAdded');
    const diffCountDeleted = document.getElementById('diffCountDeleted');
    const diffCountModified = document.getElementById('diffCountModified');
    const diffCgList = document.getElementById('diffCgList');
    const diffOmList = document.getElementById('diffOmList');

    // Unified KB & Traceability
    const kbWorkspacePath = document.getElementById('kbWorkspacePath');
    const kbBranchInput = document.getElementById('kbBranchInput');
    const kbDeviceSelect = document.getElementById('kbDeviceSelect');
    const kbWorkersInput = document.getElementById('kbWorkersInput');
    const btnRunBuildKb = document.getElementById('btnRunBuildKb');
    const kbBuildResult = document.getElementById('kbBuildResult');
    const btnRefreshKbCoverage = document.getElementById('btnRefreshKbCoverage');
    const kbCoveragePanel = document.getElementById('kbCoveragePanel');
    const kbTraceTarget = document.getElementById('kbTraceTarget');
    const btnRunKbTrace = document.getElementById('btnRunKbTrace');
    const kbTraceResults = document.getElementById('kbTraceResults');

    // Plugins & Hardware
    const btnRefreshHardware = document.getElementById('btnRefreshHardware');
    const hardwareDetailGrid = document.getElementById('hardwareDetailGrid');
    const btnReloadPlugins = document.getElementById('btnReloadPlugins');
    const pluginSearchInput = document.getElementById('pluginSearchInput');
    const pluginsGrid = document.getElementById('pluginsGrid');

    // v3.0 Testing Layer 1
    const v3PlanFiles = document.getElementById('v3PlanFiles');
    const v3PlanVersionTag = document.getElementById('v3PlanVersionTag');
    const btnRunV3Plan = document.getElementById('btnRunV3Plan');
    const v3PlanOutput = document.getElementById('v3PlanOutput');
    const v3CacheIntent = document.getElementById('v3CacheIntent');
    const v3CachePlatform = document.getElementById('v3CachePlatform');
    const v3CacheVersion = document.getElementById('v3CacheVersion');
    const btnLookupV3Cache = document.getElementById('btnLookupV3Cache');
    const v3CacheOutput = document.getElementById('v3CacheOutput');
    const v3EvidenceRunId = document.getElementById('v3EvidenceRunId');
    const v3EvidenceCenterTs = document.getElementById('v3EvidenceCenterTs');
    const v3EvidenceWindowMs = document.getElementById('v3EvidenceWindowMs');
    const btnQueryV3Evidence = document.getElementById('btnQueryV3Evidence');
    const v3EvidenceOutput = document.getElementById('v3EvidenceOutput');
    const v3RcaLogInput = document.getElementById('v3RcaLogInput');
    const btnRunV3Rca = document.getElementById('btnRunV3Rca');
    const v3RcaOutput = document.getElementById('v3RcaOutput');

    // Profile & API Keys
    const profileForm = document.getElementById('profileForm');
    const profileUsername = document.getElementById('profileUsername');
    const profileRoleBadge = document.getElementById('profileRoleBadge');
    const profileDisplayName = document.getElementById('profileDisplayName');
    const profileEmail = document.getElementById('profileEmail');
    const profileAllowedProjects = document.getElementById('profileAllowedProjects');
    const btnSaveProfile = document.getElementById('btnSaveProfile');

    const changePasswordForm = document.getElementById('changePasswordForm');
    const currentPasswordInput = document.getElementById('currentPasswordInput');
    const newPasswordInput = document.getElementById('newPasswordInput');
    const revokeOtherSessionsCheck = document.getElementById('revokeOtherSessionsCheck');
    const btnChangePassword = document.getElementById('btnChangePassword');

    const createApiKeyForm = document.getElementById('createApiKeyForm');
    const apiKeyName = document.getElementById('apiKeyName');
    const apiKeyExpiryDays = document.getElementById('apiKeyExpiryDays');
    const apiKeyRoleScope = document.getElementById('apiKeyRoleScope');
    const apiKeyAllowedProjects = document.getElementById('apiKeyAllowedProjects');
    const apiKeyAllowedTools = document.getElementById('apiKeyAllowedTools');
    const btnCreateApiKey = document.getElementById('btnCreateApiKey');
    const personalApiKeysList = document.getElementById('personalApiKeysList');
    const btnRevokeOtherSessions = document.getElementById('btnRevokeOtherSessions');
    const activeSessionsList = document.getElementById('activeSessionsList');

    // Identity & RBAC Admin
    const authPolicyForm = document.getElementById('authPolicyForm');
    const settingAllowRegistration = document.getElementById('settingAllowRegistration');
    const settingDefaultRole = document.getElementById('settingDefaultRole');
    const settingSessionTtl = document.getElementById('settingSessionTtl');
    const settingRequireRestAuth = document.getElementById('settingRequireRestAuth');
    const btnSaveAuthSettings = document.getElementById('btnSaveAuthSettings');
    const authAccountForm = document.getElementById('authAccountForm');
    const authAccountsList = document.getElementById('authAccountsList');
    const auditLogEventFilter = document.getElementById('auditLogEventFilter');
    const auditLogUserFilter = document.getElementById('auditLogUserFilter');
    const btnRefreshAuditLogs = document.getElementById('btnRefreshAuditLogs');
    const authAuditLogsTable = document.getElementById('authAuditLogsTable');

    // Native Dialogs & Popover Toast Containers
    const confirmDialog = document.getElementById('confirmDialog');
    const confirmDialogTitle = document.getElementById('confirmDialogTitle');
    const confirmDialogMessage = document.getElementById('confirmDialogMessage');
    const confirmDialogSubmit = document.getElementById('confirmDialogSubmit');

    const newProjectDialog = document.getElementById('newProjectDialog');
    const newProjectDialogForm = document.getElementById('newProjectDialogForm');
    const dialogProjectId = document.getElementById('dialogProjectId');
    const dialogProjectName = document.getElementById('dialogProjectName');
    const dialogProjectDesc = document.getElementById('dialogProjectDesc');

    const newBranchSchemaDialog = document.getElementById('newBranchSchemaDialog');
    const newBranchSchemaForm = document.getElementById('newBranchSchemaForm');
    const newBranchSchemaProjectHint = document.getElementById('newBranchSchemaProjectHint');
    const branchDialogProjectId = document.getElementById('branchDialogProjectId');
    const branchDialogName = document.getElementById('branchDialogName');
    const branchDialogBase = document.getElementById('branchDialogBase');
    const branchDialogSchemaPreview = document.getElementById('branchDialogSchemaPreview');
    const btnCancelBranchDialog = document.getElementById('btnCancelBranchDialog');
    const btnSubmitBranchDialog = document.getElementById('btnSubmitBranchDialog');

    const secretRevealDialog = document.getElementById('secretRevealDialog');
    const secretRevealTitle = document.getElementById('secretRevealTitle');
    const secretRevealSubtitle = document.getElementById('secretRevealSubtitle');
    const secretRevealValue = document.getElementById('secretRevealValue');
    const btnCopySecretReveal = document.getElementById('btnCopySecretReveal');

    const commandPaletteDialog = document.getElementById('commandPaletteDialog');
    const commandPaletteInput = document.getElementById('commandPaletteInput');
    const commandPaletteResults = document.getElementById('commandPaletteResults');

    // AI & LLM Settings Dialog & Dedicated Page (/app/settings/ai)
    const aiSettingsDialog = document.getElementById('aiSettingsDialog');
    const aiSettingsStatusBanner = document.getElementById('aiSettingsStatusBanner');
    const btnRefreshAiProbe = document.getElementById('btnRefreshAiProbe');
    const aiSettingsForm = document.getElementById('aiSettingsForm');
    const aiProviderSelect = document.getElementById('aiProviderSelect');
    const ollamaModelSuggestions = document.getElementById('ollamaModelSuggestions');
    const aiSettingsTestOutput = document.getElementById('aiSettingsTestOutput');
    const btnCancelAiSettings = document.getElementById('btnCancelAiSettings');
    const btnTestAiSettings = document.getElementById('btnTestAiSettings');
    const btnSaveAiSettings = document.getElementById('btnSaveAiSettings');

    const aiPageStatusBanner = document.getElementById('aiPageStatusBanner');
    const btnPageProbeAi = document.getElementById('btnPageProbeAi');
    const aiPageForm = document.getElementById('aiPageForm');
    const aiPageTestOutput = document.getElementById('aiPageTestOutput');
    const btnPageTestAi = document.getElementById('btnPageTestAi');
    const btnPageSaveAi = document.getElementById('btnPageSaveAi');

    const toastRegion = document.getElementById('toastRegion');
    const srAnnouncer = document.getElementById('srAnnouncer');

    // =========================================================================
    // 3. Application State & Canonical Route Table (History API)
    // =========================================================================
    let activeUser = 'default';
    let projectGroups = {};
    let lastTopologyData = null;
    let topologyProjectsMap = {};
    let currentAccount = null;
    let userRole = 'reader';
    let isInitialized = false;
    let listenersBound = false;
    let allowRegistrationConfig = false;
    let pendingNextUrl = '';

    let loadedMemories = [];
    let allPlugins = [];
    let activePluginCategory = 'all';
    let lastHardwareStatus = null;
    let currentAiSettings = null;
    let cgNetwork = null;
    let cgNodesList = [];
    let cgEdgesList = [];
    let cgPhysicsEnabled = true;
    let cgResizeObserver = null;
    let cgResizeTimer = null;
    const pendingToasts = [];

    const DOMAINS = {
        workspace: { slug: 'workspace', label: 'Workspace', defaultPath: '/app/overview' },
        graph: { slug: 'graph', label: 'Code & Knowledge', defaultPath: '/app/graph/explorer' },
        platform: { slug: 'platform', label: 'Platform & QA', defaultPath: '/app/platform/plugins' },
        settings: { slug: 'settings', label: 'Settings & IAM', defaultPath: '/app/settings/ai' }
    };

    const ROUTES = [
        { path: '/app/overview', tabId: 'dashboardTab', title: 'Overview', icon: 'fa-gauge-high', domainSlug: 'workspace', domainLabel: 'Workspace' },
        { path: '/app/workspace/projects', tabId: 'projectRegistryTab', title: 'Projects & Databases', icon: 'fa-folder-tree', domainSlug: 'workspace', domainLabel: 'Workspace' },
        { path: '/app/workspace/memories', tabId: 'memoriesTab', title: 'Memories & Docs', icon: 'fa-layer-group', domainSlug: 'workspace', domainLabel: 'Workspace' },
        { path: '/app/workspace/search', tabId: 'searchTab', title: 'Semantic Search', icon: 'fa-magnifying-glass', domainSlug: 'workspace', domainLabel: 'Workspace' },

        { path: '/app/graph/explorer', tabId: 'codeGraphTab', title: 'Graph Explorer', icon: 'fa-circle-nodes', domainSlug: 'graph', domainLabel: 'Code & Knowledge' },
        { path: '/app/graph/diff', tabId: 'compareTab', title: 'Branch Diff', icon: 'fa-code-compare', domainSlug: 'graph', domainLabel: 'Code & Knowledge' },
        { path: '/app/kb', tabId: 'kbTab', title: 'Unified KB & Trace', icon: 'fa-book-atlas', domainSlug: 'graph', domainLabel: 'Code & Knowledge' },
        { path: '/app/copilot', tabId: 'copilotTab', title: 'PM Copilot', icon: 'fa-file-pen', domainSlug: 'graph', domainLabel: 'Code & Knowledge' },

        { path: '/app/platform/plugins', tabId: 'pluginsTab', title: 'Plugins & Hardware', icon: 'fa-cubes', domainSlug: 'platform', domainLabel: 'Platform & QA' },
        { path: '/app/platform/qa', tabId: 'v3TestingTab', title: 'v3.0 QA Layer 1', icon: 'fa-vial-circle-check', domainSlug: 'platform', domainLabel: 'Platform & QA' },
        { path: '/app/platform/mcp', tabId: 'mcpTab', title: 'MCP Clients', icon: 'fa-network-wired', domainSlug: 'platform', domainLabel: 'Platform & QA' },

        { path: '/app/settings/ai', tabId: 'aiSettingsTab', title: 'AI & LLM Settings', icon: 'fa-wand-magic-sparkles', domainSlug: 'settings', domainLabel: 'Settings & IAM' },
        { path: '/app/settings/profile', tabId: 'profileTab', title: 'My Profile & Keys', icon: 'fa-id-badge', domainSlug: 'settings', domainLabel: 'Settings & IAM' },
        { path: '/app/settings/iam', tabId: 'authAdminTab', title: 'Identity & RBAC', icon: 'fa-user-shield', domainSlug: 'settings', domainLabel: 'Settings & IAM', adminOnly: true }
    ];

    const ROUTE_ALIASES = {
        '/': '/app/overview',
        '/app': '/app/overview',
        '/app/workspace': '/app/overview',
        '/app/projects': '/app/workspace/projects',
        '/app/memories': '/app/workspace/memories',
        '/app/search': '/app/workspace/search',
        '/app/graph': '/app/graph/explorer',
        '/app/diff': '/app/graph/diff',
        '/app/platform': '/app/platform/plugins',
        '/app/plugins': '/app/platform/plugins',
        '/app/qa': '/app/platform/qa',
        '/app/v3': '/app/platform/qa',
        '/app/mcp': '/app/platform/mcp',
        '/app/settings': '/app/settings/ai',
        '/app/ai': '/app/settings/ai',
        '/app/profile': '/app/settings/profile',
        '/app/iam': '/app/settings/iam',
        '/app/auth': '/app/settings/iam'
    };

    let activeRoutePath = '/app/overview';

    function normalizePathname(rawPathname) {
        let p = String(rawPathname || '/').trim() || '/';
        if (p.length > 1 && p.endsWith('/')) {
            p = p.replace(/\/+$/, '') || '/';
        }
        if (p === '/login' || p === '/register') return p;
        if (p === '/dashboard') return '/app/overview';
        if (p.startsWith('/dashboard/')) {
            p = '/app/' + p.slice('/dashboard/'.length);
        }
        if (ROUTE_ALIASES[p]) {
            p = ROUTE_ALIASES[p];
        }
        const matched = ROUTES.find(r => r.path === p);
        return matched ? matched.path : '/app/overview';
    }

    function getRouteByPath(pathname) {
        const norm = normalizePathname(pathname);
        return ROUTES.find(r => r.path === norm) || ROUTES[0];
    }

    function getRouteByTabId(tabId) {
        const resolvedId = tabId === 'authTab' ? 'authAdminTab' : tabId;
        return ROUTES.find(r => r.tabId === resolvedId) || ROUTES[0];
    }

    function isSafeInternalAppUrl(candidate) {
        if (!candidate || typeof candidate !== 'string') return false;
        if (!candidate.startsWith('/') || candidate.startsWith('//')) return false;
        try {
            const parsed = new URL(candidate, window.location.origin);
            if (parsed.origin !== window.location.origin) return false;
            const norm = normalizePathname(parsed.pathname);
            return norm.startsWith('/app/');
        } catch (_) {
            return false;
        }
    }

    // Seed workspace project/branch selection from initial URL query parameters if present
    (function seedStorageFromInitialUrl() {
        try {
            const initParams = new URLSearchParams(window.location.search);
            const urlProj = (initParams.get('project') || '').trim();
            const urlBranch = (initParams.get('branch') || '').trim();
            if (urlProj) {
                localStorage.setItem('activeProject', urlProj);
                if (urlBranch) {
                    localStorage.setItem(`branch_${urlProj}`, urlBranch === 'all' ? '%' : urlBranch);
                }
            }
        } catch (_) {
            // Ignore malformed query string
        }
    })();

    // =========================================================================
    // 4. Accessible Popover Toasts, Screen Reader Announcer & Native Dialogs
    // =========================================================================
    function announceSr(message) {
        if (!srAnnouncer || !message) return;
        srAnnouncer.textContent = '';
        requestAnimationFrame(() => {
            srAnnouncer.textContent = message;
        });
    }

    function hasOpenModalDialog() {
        return Boolean(document.querySelector('dialog[open]'));
    }

    function repositionOpenToasts() {
        if (!toastRegion) return;
        const openToasts = Array.from(toastRegion.querySelectorAll('.toast-item'));
        const count = openToasts.length;
        openToasts.forEach((item, idx) => {
            const stackIndex = count - 1 - idx;
            item.style.setProperty('--toast-index', String(stackIndex));
        });
    }

    function showToast(message, type = 'info', durationMs = 4500) {
        const text = toDisplayText(message, '').trim();
        if (!text) return;
        announceSr(text);

        if (hasOpenModalDialog()) {
            pendingToasts.push({ message: text, type, durationMs });
            return;
        }

        if (!toastRegion) return;

        const normalizedType = type === 'error' ? 'danger' : type;
        const iconMap = {
            success: 'fa-circle-check',
            danger: 'fa-circle-exclamation',
            warning: 'fa-triangle-exclamation',
            info: 'fa-circle-info'
        };

        const toast = document.createElement('div');
        toast.setAttribute('popover', 'manual');
        toast.className = `toast-item toast-${normalizedType}`;

        const bodyWrap = document.createElement('div');
        bodyWrap.style.display = 'flex';
        bodyWrap.style.alignItems = 'center';
        bodyWrap.style.gap = '0.55rem';
        bodyWrap.style.minWidth = '0';

        const icon = document.createElement('i');
        icon.className = `fa-solid ${iconMap[normalizedType] || iconMap.info}`;
        icon.setAttribute('aria-hidden', 'true');

        const span = document.createElement('span');
        span.textContent = text;
        span.style.fontSize = '0.85rem';
        span.style.lineHeight = '1.4';
        span.style.wordBreak = 'break-word';

        bodyWrap.appendChild(icon);
        bodyWrap.appendChild(span);

        const closeBtn = document.createElement('button');
        closeBtn.type = 'button';
        closeBtn.className = 'btn-icon-only';
        closeBtn.setAttribute('aria-label', 'Dismiss notification');
        closeBtn.innerHTML = '<i class="fa-solid fa-xmark" aria-hidden="true"></i>';

        toast.appendChild(bodyWrap);
        toast.appendChild(closeBtn);
        toastRegion.appendChild(toast);
        repositionOpenToasts();

        if (typeof toast.showPopover === 'function') {
            try {
                toast.showPopover();
            } catch (_) {
                toast.classList.add(':popover-open');
                toast.style.display = 'flex';
            }
        } else {
            toast.classList.add(':popover-open');
            toast.style.display = 'flex';
        }

        let timerId = null;
        let dismissed = false;

        const dismiss = () => {
            if (dismissed) return;
            dismissed = true;
            if (timerId) clearTimeout(timerId);
            if (typeof toast.hidePopover === 'function') {
                try {
                    toast.hidePopover();
                } catch (_) {
                    // ignore
                }
            }
            toast.remove();
            repositionOpenToasts();
        };

        const startTimer = () => {
            if (durationMs <= 0) return;
            if (timerId) clearTimeout(timerId);
            timerId = setTimeout(dismiss, durationMs);
        };

        const pauseTimer = () => {
            if (timerId) {
                clearTimeout(timerId);
                timerId = null;
            }
        };

        closeBtn.addEventListener('click', dismiss);
        toast.addEventListener('focusin', pauseTimer);
        toast.addEventListener('mouseenter', pauseTimer);
        toast.addEventListener('focusout', startTimer);
        toast.addEventListener('mouseleave', startTimer);

        startTimer();
    }

    function flushPendingToasts() {
        if (hasOpenModalDialog() || pendingToasts.length === 0) return;
        const queued = pendingToasts.splice(0, pendingToasts.length);
        queued.forEach(item => showToast(item.message, item.type, item.durationMs));
    }

    document.querySelectorAll('dialog').forEach(dlg => {
        dlg.addEventListener('close', () => {
            setTimeout(flushPendingToasts, 50);
        });
    });

    function confirmAction({
        title = 'Confirm Action',
        message = 'Are you sure you want to proceed?',
        confirmLabel = 'Confirm',
        danger = true
    } = {}) {
        if (!confirmDialog) return Promise.resolve(false);

        return new Promise((resolve) => {
            confirmDialog.returnValue = '';
            if (confirmDialogTitle) confirmDialogTitle.textContent = title;
            if (confirmDialogMessage) confirmDialogMessage.textContent = message;
            if (confirmDialogSubmit) {
                confirmDialogSubmit.textContent = confirmLabel;
                confirmDialogSubmit.className = danger ? 'btn btn-danger' : 'btn btn-primary';
            }

            const onClose = () => {
                const confirmed = confirmDialog.returnValue === 'confirm';
                confirmDialog.returnValue = '';
                resolve(confirmed);
            };

            confirmDialog.addEventListener('close', onClose, { once: true });
            confirmDialog.showModal();

            const cancelBtn = confirmDialog.querySelector('button[value="cancel"]');
            if (cancelBtn) {
                requestAnimationFrame(() => cancelBtn.focus());
            }
        });
    }

    function showSecretReveal({ title = 'Credential Created', subtitle, secret }) {
        if (!secretRevealDialog || !secretRevealValue) return;
        if (secretRevealTitle) secretRevealTitle.textContent = title;
        if (secretRevealSubtitle && subtitle) secretRevealSubtitle.textContent = subtitle;
        secretRevealValue.textContent = secret || '';
        secretRevealDialog.returnValue = '';
        secretRevealDialog.showModal();
    }

    if (btnCopySecretReveal) {
        btnCopySecretReveal.addEventListener('click', async () => {
            const val = secretRevealValue ? secretRevealValue.textContent : '';
            if (!val) return;
            try {
                await navigator.clipboard.writeText(val);
                const orig = btnCopySecretReveal.innerHTML;
                btnCopySecretReveal.innerHTML = '<i class="fa-solid fa-check" aria-hidden="true"></i> Copied';
                setTimeout(() => {
                    btnCopySecretReveal.innerHTML = orig;
                }, 1800);
                announceSr('Secret copied to clipboard');
            } catch (err) {
                showToast('Clipboard copy failed: ' + err.message, 'error');
            }
        });
    }

    // =========================================================================
    // 5. Theme Toggle (modern-web-guidance: dark-mode-toggle)
    // =========================================================================
    const darkSchemeMedia = window.matchMedia('(prefers-color-scheme: dark)');
    const colorSchemeMeta = document.querySelector('meta[name="color-scheme"]');

    function getSystemScheme() {
        return darkSchemeMedia.matches ? 'dark' : 'light';
    }

    function getRenderedScheme() {
        const stored = localStorage.getItem('pb-color-scheme');
        if (stored === 'dark' || stored === 'light') return stored;
        const metaVal = colorSchemeMeta ? colorSchemeMeta.content.trim() : '';
        if (metaVal === 'dark' || metaVal === 'light') return metaVal;
        return getSystemScheme();
    }

    function syncThemeControls() {
        const activeScheme = getRenderedScheme();
        if (colorSchemeMeta) {
            const stored = localStorage.getItem('pb-color-scheme');
            colorSchemeMeta.content = (stored === 'dark' || stored === 'light') ? stored : 'light dark';
        }
        if (btnThemeToggle) {
            const nextLabel = activeScheme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme';
            btnThemeToggle.setAttribute('aria-label', nextLabel);
            btnThemeToggle.setAttribute('aria-pressed', activeScheme === 'dark' ? 'true' : 'false');
        }
    }

    function toggleColorTheme() {
        const current = getRenderedScheme();
        const next = current === 'dark' ? 'light' : 'dark';
        const sys = getSystemScheme();

        if (next === sys) {
            localStorage.removeItem('pb-color-scheme');
        } else {
            localStorage.setItem('pb-color-scheme', next);
        }
        syncThemeControls();

        if (cgNetwork && cgNodesList.length > 0) {
            renderCodeGraph(cgNodesList, cgEdgesList);
        }
        showToast(`Theme switched to ${next} mode`, 'info', 2500);
    }

    if (btnThemeToggle) {
        btnThemeToggle.addEventListener('click', toggleColorTheme);
    }

    window.addEventListener('storage', (e) => {
        if (e.key === 'pb-color-scheme') {
            syncThemeControls();
            if (cgNetwork && cgNodesList.length > 0) {
                renderCodeGraph(cgNodesList, cgEdgesList);
            }
        }
    });

    if (typeof darkSchemeMedia.addEventListener === 'function') {
        darkSchemeMedia.addEventListener('change', () => {
            syncThemeControls();
            if (cgNetwork && cgNodesList.length > 0) {
                renderCodeGraph(cgNodesList, cgEdgesList);
            }
        });
    }

    syncThemeControls();

    // =========================================================================
    // 6. Form Validation ARIA Sync (validate-input-after-interaction)
    // =========================================================================
    function syncFieldValidationState(el, forceCheck = false) {
        if (!el || typeof el.checkValidity !== 'function') return true;
        if (!el.hasAttribute('data-base-describedby')) {
            el.setAttribute('data-base-describedby', el.getAttribute('aria-describedby') || '');
        }
        const baseDesc = el.getAttribute('data-base-describedby') || '';
        const errId = el.getAttribute('data-error');
        const isValid = el.validity.valid;

        if (!isValid && (forceCheck || el.dataset.interacted === 'true')) {
            el.classList.add('user-invalid-fallback');
            el.setAttribute('aria-invalid', 'true');
            if (errId) {
                const tokens = baseDesc.split(/\s+/).filter(Boolean);
                if (!tokens.includes(errId)) tokens.push(errId);
                el.setAttribute('aria-describedby', tokens.join(' '));
            }
        } else if (isValid) {
            el.classList.remove('user-invalid-fallback');
            el.setAttribute('aria-invalid', 'false');
            if (baseDesc) {
                el.setAttribute('aria-describedby', baseDesc);
            } else {
                el.removeAttribute('aria-describedby');
            }
        }
        return isValid;
    }

    function setupFormValidationAria() {
        const fields = document.querySelectorAll('input[data-error], input[required], select[required], textarea[required]');
        fields.forEach(el => {
            el.setAttribute('aria-invalid', 'false');
            if (!el.hasAttribute('data-base-describedby')) {
                el.setAttribute('data-base-describedby', el.getAttribute('aria-describedby') || '');
            }
            el.addEventListener('blur', () => {
                if (el.value !== '' || el.required) {
                    el.dataset.interacted = 'true';
                    syncFieldValidationState(el, true);
                }
            });
            el.addEventListener('input', () => {
                if (el.dataset.interacted === 'true' || el.classList.contains('user-invalid-fallback')) {
                    syncFieldValidationState(el, true);
                }
            });
            el.addEventListener('invalid', () => {
                el.dataset.interacted = 'true';
                syncFieldValidationState(el, true);
            });
        });
    }

    setupFormValidationAria();

    // =========================================================================
    // 7. New Project Dialog & Command Palette Dialog
    // =========================================================================
    function openNewProjectModal() {
        if (!newProjectDialog) return;
        if (!newProjectDialog.open) {
            newProjectDialog.returnValue = '';
            newProjectDialog.showModal();
        }
        if (dialogProjectId) {
            requestAnimationFrame(() => dialogProjectId.focus());
        }
    }

    if (btnAddProject) {
        btnAddProject.addEventListener('click', () => {
            // Ensure dialog opens even if Invoker Commands API is unsupported
            setTimeout(() => {
                if (newProjectDialog && !newProjectDialog.open) {
                    openNewProjectModal();
                }
            }, 0);
        });
    }

    if (newProjectDialog) {
        const closeBtn = newProjectDialog.querySelector('[command="close"][commandfor="newProjectDialog"]');
        if (closeBtn) {
            closeBtn.addEventListener('click', () => {
                if (newProjectDialog.open) newProjectDialog.close('cancel');
            });
        }
    }

    if (newProjectDialogForm) {
        newProjectDialogForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const id = (dialogProjectId?.value || '').trim();
            const name = (dialogProjectName?.value || '').trim();
            const description = (dialogProjectDesc?.value || '').trim();
            if (!id || !name) {
                showToast('Project ID and Display Name are required.', 'warning');
                return;
            }

            const submitBtn = newProjectDialogForm.querySelector('button[type="submit"]');
            if (submitBtn) submitBtn.disabled = true;

            try {
                const res = await fetch('/codegraph/projects', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        id,
                        name,
                        description: description || `Workspace ${name}`
                    })
                });
                if (!res.ok) {
                    throw new Error(await extractErrorMessage(res, 'Failed to register project.'));
                }
                newProjectDialogForm.reset();
                if (newProjectDialog.open) newProjectDialog.close('created');
                showToast(`Project '${id}' created successfully.`, 'success');

                await loadProjects();
                if (currentProjectSelect) {
                    const baseId = id.includes(':') ? id.split(':')[0] : id;
                    if (Array.from(currentProjectSelect.options).some(o => o.value === baseId)) {
                        currentProjectSelect.value = baseId;
                        currentProjectSelect.dispatchEvent(new Event('change'));
                    }
                }
                const regTab = document.getElementById('projectRegistryTab');
                if (regTab && regTab.classList.contains('active')) {
                    loadProjectsList();
                }
            } catch (err) {
                if (newProjectDialog.open) newProjectDialog.close('error');
                showToast('Error creating project: ' + err.message, 'error');
            } finally {
                if (submitBtn) submitBtn.disabled = false;
            }
        });
    }

    function isReaderRole() {
        const effectiveRole = (currentAccount?.role || userRole || 'reader').toLowerCase();
        return effectiveRole === 'reader';
    }

    function updateRegDbPreview() {
        if (!regDbNamePreview) return;
        const raw = (regProjectIdInput?.value || '').trim();
        if (!raw) {
            regDbNamePreview.textContent = 'DB: pb_proj_<slug>_<hash8> · Root Schema: pb_root';
            return;
        }
        regDbNamePreview.textContent = `DB: ${sanitizeProjectDbName(raw)} · Root Schema: pb_root`;
    }

    function updateBranchDialogSchemaPreview() {
        if (!branchDialogSchemaPreview) return;
        const projectId = (branchDialogProjectId?.value || '').trim();
        const projTopo = topologyProjectsMap[projectId];
        const rootBranch = projTopo?.root_branch || 'main';
        const rawBranch = (branchDialogName?.value || '').trim();
        if (!rawBranch) {
            branchDialogSchemaPreview.textContent = 'Schema: pb_br_<slug>_<hash8> (Graph + Docs & Memory)';
            return;
        }
        const schemaName = sanitizeBranchSchemaName(rawBranch, rootBranch);
        branchDialogSchemaPreview.textContent = `Schema: ${schemaName} (Graph + Docs & Memory)`;
    }

    function openNewBranchSchemaDialog(projectTopo) {
        if (!newBranchSchemaDialog || !projectTopo) return;
        const projectId = toDisplayText(projectTopo.project_id || projectTopo.id, '');
        const dbName = toDisplayText(projectTopo.db_name, sanitizeProjectDbName(projectId));
        const rootBranch = toDisplayText(projectTopo.root_branch, 'main');
        const branches = Array.isArray(projectTopo.branches) ? projectTopo.branches : [];

        if (branchDialogProjectId) branchDialogProjectId.value = projectId;
        if (newBranchSchemaProjectHint) {
            newBranchSchemaProjectHint.textContent = `Project: ${projectId} · DB: ${dbName}`;
        }
        if (branchDialogBase) {
            branchDialogBase.innerHTML = '';
            const branchNames = new Set([rootBranch]);
            branches.forEach(b => {
                const bn = toDisplayText(b?.branch_name, '').trim();
                if (bn) branchNames.add(bn);
            });
            Array.from(branchNames).forEach(bn => {
                const opt = document.createElement('option');
                opt.value = bn;
                opt.textContent = bn === rootBranch ? `${bn} (ROOT)` : bn;
                branchDialogBase.appendChild(opt);
            });
            branchDialogBase.value = rootBranch;
        }
        if (branchDialogName) branchDialogName.value = '';
        updateBranchDialogSchemaPreview();

        if (!newBranchSchemaDialog.open) {
            newBranchSchemaDialog.returnValue = '';
            newBranchSchemaDialog.showModal();
        }
        if (branchDialogName) {
            requestAnimationFrame(() => branchDialogName.focus());
        }
    }

    if (newBranchSchemaDialog) {
        const closeBranchBtn = newBranchSchemaDialog.querySelector('[command="close"][commandfor="newBranchSchemaDialog"]');
        if (closeBranchBtn) {
            closeBranchBtn.addEventListener('click', () => {
                if (newBranchSchemaDialog.open) newBranchSchemaDialog.close('cancel');
            });
        }
        if (btnCancelBranchDialog && btnCancelBranchDialog !== closeBranchBtn) {
            btnCancelBranchDialog.addEventListener('click', () => {
                if (newBranchSchemaDialog.open) newBranchSchemaDialog.close('cancel');
            });
        }
    }

    if (branchDialogName) {
        branchDialogName.addEventListener('input', updateBranchDialogSchemaPreview);
    }

    if (newBranchSchemaForm) {
        newBranchSchemaForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const projectId = (branchDialogProjectId?.value || '').trim();
            const branchName = (branchDialogName?.value || '').trim();
            const baseBranch = (branchDialogBase?.value || 'main').trim() || 'main';
            const author = (currentAccount?.username || currentUser?.value || '').trim();

            if (!projectId || !branchName) {
                showToast('Project ID and Branch Name are required.', 'warning');
                return;
            }

            if (btnSubmitBranchDialog) btnSubmitBranchDialog.disabled = true;
            try {
                const res = await fetch(`/master/projects/${encodeURIComponent(projectId)}/branches`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        branch_name: branchName,
                        base_branch: baseBranch,
                        author
                    })
                });
                if (!res.ok) {
                    throw new Error(await extractErrorMessage(res, 'Failed to provision branch schema.'));
                }
                newBranchSchemaForm.reset();
                if (newBranchSchemaDialog && newBranchSchemaDialog.open) {
                    newBranchSchemaDialog.close('created');
                }
                showToast(`Branch schema '${branchName}' provisioned in '${projectId}'.`, 'success');
                await loadProjects();
                await loadProjectsList();
            } catch (err) {
                showToast('Error provisioning branch schema: ' + err.message, 'error');
            } finally {
                if (btnSubmitBranchDialog) btnSubmitBranchDialog.disabled = false;
            }
        });
    }

    // Command Palette
    let paletteActiveIndex = 0;

    function getCommandPaletteItems() {
        const viewCommands = ROUTES
            .filter(route => !route.adminOnly || userRole === 'admin')
            .map(route => ({
                id: `view:${route.tabId}`,
                title: `${route.title} — ${route.path}`,
                subtitle: route.domainLabel,
                keywords: `${route.title} ${route.domainLabel} ${route.path} ${route.tabId}`.toLowerCase(),
                icon: route.icon || 'fa-arrow-right',
                run: () => navigateTo(buildContextualHref(route.path))
            }));

        const actionCommands = [
            {
                id: 'action:ai-settings-modal',
                title: 'Quick Modal: AI & LLM Provider Settings',
                subtitle: '/app/settings/ai',
                keywords: 'ai llm settings gemini openai ollama minimax local rag provider api key model modal',
                icon: 'fa-sliders',
                run: () => openAiSettingsModal()
            },
            {
                id: 'action:copy-route',
                title: 'Copy Shareable Route URL',
                subtitle: 'Router',
                keywords: 'copy url route share link deep link',
                icon: 'fa-link',
                run: () => copyActiveRouteUrl()
            },
            {
                id: 'action:theme',
                title: 'Toggle Color Theme (Light / Dark)',
                subtitle: 'Appearance',
                keywords: 'theme dark light mode toggle appearance',
                icon: 'fa-circle-half-stroke',
                run: () => toggleColorTheme()
            },
            {
                id: 'action:refresh-hw',
                title: 'Refresh Hardware & Local AI Telemetry',
                subtitle: 'Platform & QA',
                keywords: 'hardware gpu mps cuda ocr ollama refresh telemetry',
                icon: 'fa-microchip',
                run: async () => {
                    await loadHardwareStatus();
                    showToast('Hardware telemetry refreshed.', 'success');
                }
            }
        ];

        if (userRole !== 'reader') {
            actionCommands.unshift({
                id: 'action:new-project',
                title: 'Create New Project Space',
                subtitle: 'Workspace Action',
                keywords: 'new project add create workspace',
                icon: 'fa-folder-plus',
                run: () => openNewProjectModal()
            });
        }

        return [...viewCommands, ...actionCommands];
    }

    function renderCommandPaletteResults(filterText = '') {
        if (!commandPaletteResults) return;
        const q = filterText.trim().toLowerCase();
        const items = getCommandPaletteItems().filter(item =>
            !q || item.title.toLowerCase().includes(q) || item.keywords.includes(q)
        );

        commandPaletteResults.innerHTML = '';
        if (items.length === 0) {
            commandPaletteResults.innerHTML = '<div class="placeholder-item">No matching views or commands.</div>';
            paletteActiveIndex = 0;
            return;
        }

        if (paletteActiveIndex >= items.length) paletteActiveIndex = 0;

        items.forEach((item, idx) => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'menu-link';
            btn.setAttribute('role', 'option');
            btn.setAttribute('aria-selected', idx === paletteActiveIndex ? 'true' : 'false');
            if (idx === paletteActiveIndex) btn.classList.add('active');
            btn.innerHTML = `
                <i class="fa-solid ${item.icon}" aria-hidden="true"></i>
                <span class="nav-label">${escapeHTML(item.title)}</span>
                <span class="nav-badge">${escapeHTML(item.subtitle)}</span>
            `;
            btn.addEventListener('click', () => {
                if (commandPaletteDialog?.open) commandPaletteDialog.close();
                item.run();
            });
            commandPaletteResults.appendChild(btn);
        });
    }

    function openCommandPalette() {
        if (!commandPaletteDialog) return;
        if (!commandPaletteDialog.open) {
            commandPaletteDialog.showModal();
        }
        if (commandPaletteInput) {
            commandPaletteInput.value = '';
            paletteActiveIndex = 0;
            renderCommandPaletteResults('');
            requestAnimationFrame(() => commandPaletteInput.focus());
        }
    }

    if (btnOpenCommandPalette) {
        btnOpenCommandPalette.addEventListener('click', () => {
            setTimeout(() => {
                if (commandPaletteDialog && !commandPaletteDialog.open) {
                    openCommandPalette();
                } else if (commandPaletteDialog?.open) {
                    paletteActiveIndex = 0;
                    if (commandPaletteInput) commandPaletteInput.value = '';
                    renderCommandPaletteResults('');
                }
            }, 0);
        });
    }

    if (commandPaletteInput) {
        commandPaletteInput.addEventListener('input', () => {
            paletteActiveIndex = 0;
            renderCommandPaletteResults(commandPaletteInput.value);
        });

        commandPaletteInput.addEventListener('keydown', (e) => {
            const options = commandPaletteResults ? Array.from(commandPaletteResults.querySelectorAll('[role="option"]')) : [];
            if (options.length === 0) return;

            if (e.key === 'ArrowDown') {
                e.preventDefault();
                paletteActiveIndex = (paletteActiveIndex + 1) % options.length;
                renderCommandPaletteResults(commandPaletteInput.value);
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                paletteActiveIndex = (paletteActiveIndex - 1 + options.length) % options.length;
                renderCommandPaletteResults(commandPaletteInput.value);
            } else if (e.key === 'Enter') {
                e.preventDefault();
                const selected = options[paletteActiveIndex];
                if (selected) selected.click();
            }
        });
    }

    if (commandPaletteDialog) {
        // Fallback outside-click light dismiss for browsers without closedby="any"
        commandPaletteDialog.addEventListener('click', (e) => {
            if (e.target === commandPaletteDialog && commandPaletteDialog.open) {
                commandPaletteDialog.close();
            }
        });
    }

    document.addEventListener('keydown', (e) => {
        if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
            if (loginScreen && loginScreen.style.display !== 'none') return;
            e.preventDefault();
            if (commandPaletteDialog?.open) {
                commandPaletteDialog.close();
            } else {
                openCommandPalette();
            }
            return;
        }

        if (e.key === 'Escape' && graphLayout?.classList.contains('is-fullscreen') && !hasOpenModalDialog()) {
            e.preventDefault();
            setGraphFullscreen(false);
            return;
        }

        if (!e.metaKey && !e.ctrlKey && !e.altKey && e.key.toLowerCase() === 'f') {
            const activeTag = document.activeElement?.tagName;
            const isEditable = activeTag === 'INPUT' || activeTag === 'TEXTAREA' || activeTag === 'SELECT' || document.activeElement?.isContentEditable;
            const cgTab = document.getElementById('codeGraphTab');
            if (!isEditable && !hasOpenModalDialog() && cgTab?.classList.contains('active')) {
                e.preventDefault();
                toggleGraphFullscreen();
            }
        }
    });

    // =========================================================================
    // 8. Enterprise Auth Gate & Session Lifecycle
    // =========================================================================
    const authModeTabs = [tabAuthPassword, tabAuthToken, tabAuthRegister].filter(Boolean);
    const authModePanes = {
        tabAuthPassword: authModePasswordPane,
        tabAuthToken: authModeTokenPane,
        tabAuthRegister: authModeRegisterPane
    };

    function clearLoginError() {
        if (loginError) loginError.style.display = 'none';
    }

    function showLoginError(msg) {
        if (loginErrorMsg) loginErrorMsg.textContent = msg;
        if (loginError) loginError.style.display = 'flex';
    }

    function getPendingNextFromUrl() {
        const params = new URLSearchParams(window.location.search);
        const rawNext = params.get('next') || pendingNextUrl || '';
        if (isSafeInternalAppUrl(rawNext)) {
            const parsed = new URL(rawNext, window.location.origin);
            return normalizePathname(parsed.pathname) + parsed.search;
        }
        return '';
    }

    function buildAuthUrl(tabEl, nextOverride = undefined) {
        const nextTarget = nextOverride !== undefined ? nextOverride : getPendingNextFromUrl();
        const params = new URLSearchParams();
        let authPath = '/login';
        if (tabEl === tabAuthRegister && allowRegistrationConfig) {
            authPath = '/register';
        } else if (tabEl === tabAuthToken) {
            params.set('mode', 'token');
        }
        if (nextTarget && nextTarget !== '/app/overview') {
            params.set('next', nextTarget);
        }
        const qs = params.toString();
        return qs ? `${authPath}?${qs}` : authPath;
    }

    function switchAuthMode(tabEl, { updateHistory = false, replaceHistory = false } = {}) {
        if (!tabEl || tabEl.hidden) return;
        clearLoginError();
        authModeTabs.forEach(t => {
            const isSelected = t === tabEl;
            t.setAttribute('aria-selected', isSelected ? 'true' : 'false');
            t.setAttribute('tabindex', isSelected ? '0' : '-1');
            const pane = authModePanes[t.id];
            if (pane) pane.hidden = !isSelected;
        });

        const isReg = tabEl === tabAuthRegister;
        document.title = isReg
            ? 'Create Account — ProjectBrain v3.0'
            : 'Sign In — ProjectBrain v3.0';

        if (updateHistory || replaceHistory) {
            const targetUrl = buildAuthUrl(tabEl);
            const currentFull = window.location.pathname + window.location.search;
            if (targetUrl !== currentFull) {
                if (replaceHistory) {
                    window.history.replaceState({ auth: true }, '', targetUrl);
                } else {
                    window.history.pushState({ auth: true }, '', targetUrl);
                }
            }
        }
    }

    function syncAuthScreenFromUrl({ replaceNormalized = true } = {}) {
        const normPath = normalizePathname(window.location.pathname);
        const params = new URLSearchParams(window.location.search);
        if (normPath === '/register') {
            if (allowRegistrationConfig && tabAuthRegister && !tabAuthRegister.hidden) {
                switchAuthMode(tabAuthRegister, { replaceHistory: replaceNormalized });
            } else {
                switchAuthMode(tabAuthPassword, { replaceHistory: true });
            }
            return;
        }
        const mode = (params.get('mode') || '').toLowerCase();
        if (mode === 'token' && tabAuthToken) {
            switchAuthMode(tabAuthToken, { replaceHistory: replaceNormalized });
        } else {
            switchAuthMode(tabAuthPassword, { replaceHistory: replaceNormalized });
        }
    }

    authModeTabs.forEach(tab => {
        tab.addEventListener('click', () => switchAuthMode(tab, { updateHistory: true }));
        tab.addEventListener('keydown', (e) => {
            const visibleTabs = authModeTabs.filter(t => !t.hidden);
            const idx = visibleTabs.indexOf(tab);
            if (idx === -1) return;
            let nextIdx = idx;
            if (e.key === 'ArrowRight') nextIdx = (idx + 1) % visibleTabs.length;
            else if (e.key === 'ArrowLeft') nextIdx = (idx - 1 + visibleTabs.length) % visibleTabs.length;
            else if (e.key === 'Home') nextIdx = 0;
            else if (e.key === 'End') nextIdx = visibleTabs.length - 1;
            else return;
            e.preventDefault();
            visibleTabs[nextIdx].focus();
            switchAuthMode(visibleTabs[nextIdx], { updateHistory: true });
        });
    });

    function wirePasswordToggle(btnId, inputId) {
        const btn = document.getElementById(btnId);
        const input = document.getElementById(inputId);
        if (!btn || !input) return;
        btn.addEventListener('click', () => {
            const isHidden = input.type === 'password';
            input.type = isHidden ? 'text' : 'password';
            btn.setAttribute('aria-pressed', isHidden ? 'true' : 'false');
            const baseLabel = btn.getAttribute('aria-label') || 'password';
            btn.setAttribute('aria-label', isHidden ? baseLabel.replace(/^Show/i, 'Hide') : baseLabel.replace(/^Hide/i, 'Show'));
            const icon = btn.querySelector('i');
            if (icon) {
                icon.className = isHidden ? 'fa-regular fa-eye-slash' : 'fa-regular fa-eye';
            }
        });
    }

    wirePasswordToggle('btnToggleLoginPassword', 'loginPassword');
    wirePasswordToggle('btnToggleTokenInput', 'loginToken');
    wirePasswordToggle('btnToggleRegPassword', 'regPassword');
    wirePasswordToggle('btnToggleCurrentPassword', 'currentPasswordInput');
    wirePasswordToggle('btnToggleNewPassword', 'newPasswordInput');
    wirePasswordToggle('btnToggleAiGeminiKey', 'aiGeminiKey');
    wirePasswordToggle('btnToggleAiOpenaiKey', 'aiOpenaiKey');
    wirePasswordToggle('btnToggleAiLocalAiKey', 'aiLocalAiKey');
    wirePasswordToggle('btnToggleAiPageGeminiKey', 'aiPageGeminiKey');
    wirePasswordToggle('btnToggleAiPageOpenaiKey', 'aiPageOpenaiKey');
    wirePasswordToggle('btnToggleAiPageLocalAiKey', 'aiPageLocalAiKey');

    async function loadAuthConfig() {
        try {
            const res = await fetch('/auth/config');
            if (!res.ok) return;
            const cfg = await res.json();
            allowRegistrationConfig = Boolean(cfg.allow_registration);
            if (tabAuthRegister) {
                tabAuthRegister.hidden = !allowRegistrationConfig;
                if (!allowRegistrationConfig && tabAuthRegister.getAttribute('aria-selected') === 'true') {
                    switchAuthMode(tabAuthPassword, { replaceHistory: loginScreen?.style.display !== 'none' });
                }
            }
        } catch (err) {
            console.warn('Could not load /auth/config:', err);
        }
    }

    async function checkAuth() {
        await loadAuthConfig();
        const initialNormPath = normalizePathname(window.location.pathname);
        if (initialNormPath === '/login' || initialNormPath === '/register') {
            pendingNextUrl = getPendingNextFromUrl();
            showLoginGate({ preserveAuthUrl: true });
            return;
        }

        try {
            const res = await fetch('/auth/verify');
            if (res.ok) {
                const data = await res.json();
                await handleSuccessfulLogin(data, getStoredToken(), { fromInitialVerify: true });
            } else {
                setStoredToken('');
                showLoginGate();
            }
        } catch (err) {
            console.error('Error during session verification:', err);
            showLoginGate();
        }
    }

    function showLoginGate({ preserveAuthUrl = false, pushHistory = false } = {}) {
        if (dashboardContainer) dashboardContainer.style.display = 'none';
        if (loginScreen) loginScreen.style.display = 'flex';
        if (userProfileWidget) userProfileWidget.style.display = 'none';
        if (securitySeedBanner) securitySeedBanner.hidden = true;

        const normPath = normalizePathname(window.location.pathname);
        if (!preserveAuthUrl && normPath.startsWith('/app/')) {
            const requestedAppTarget = normPath + window.location.search;
            pendingNextUrl = requestedAppTarget !== '/app/overview' ? requestedAppTarget : '';
            const targetAuthUrl = buildAuthUrl(tabAuthPassword, pendingNextUrl);
            if (pushHistory) {
                window.history.pushState({ auth: true }, '', targetAuthUrl);
            } else {
                window.history.replaceState({ auth: true }, '', targetAuthUrl);
            }
            switchAuthMode(tabAuthPassword);
        } else {
            syncAuthScreenFromUrl({ replaceNormalized: !pushHistory });
        }
    }

    function computeInitials(account) {
        const src = (account?.display_name || account?.username || 'PB').trim();
        const parts = src.split(/[\s._-]+/).filter(Boolean);
        if (parts.length >= 2) {
            return (parts[0][0] + parts[1][0]).toUpperCase();
        }
        return src.slice(0, 2).toUpperCase();
    }

    function updateSeedSecurityBanner(account) {
        if (!securitySeedBanner) return;
        const needsHardening = Boolean(account && (account.has_default_token || !account.has_password));
        securitySeedBanner.hidden = !needsHardening;
    }

    async function handleSuccessfulLogin(userData, tokenOverride = '', { fromInitialVerify = false } = {}) {
        const account = userData.account || {};
        currentAccount = account;
        userRole = (account.role || 'reader').toLowerCase();

        const effectiveToken = userData.session_token || tokenOverride || getStoredToken();
        if (effectiveToken) {
            setStoredToken(effectiveToken);
        }

        if (userAvatarInitials) userAvatarInitials.textContent = computeInitials(account);
        if (userProfileName) userProfileName.textContent = toDisplayText(account.display_name || account.username, 'unknown');
        if (userProfileRole) userProfileRole.textContent = toDisplayText(userRole, 'reader');
        if (userProfileWidget) userProfileWidget.style.display = 'flex';

        if (currentUser) {
            currentUser.value = account.username || '';
            currentUser.disabled = true;
            localStorage.setItem('currentUser', account.username || '');
        }

        const resolvedNext = getPendingNextFromUrl();
        pendingNextUrl = '';

        applyRoleRestrictions();
        updateSeedSecurityBanner(account);

        if (loginScreen) loginScreen.style.display = 'none';
        if (dashboardContainer) dashboardContainer.style.display = 'grid';

        updateLocalProjectConfig();

        if (!fromInitialVerify) {
            const targetDestination = resolvedNext || buildContextualHref('/app/overview');
            window.history.replaceState({}, '', targetDestination);
            try {
                const destUrl = new URL(targetDestination, window.location.origin);
                const urlProj = (destUrl.searchParams.get('project') || '').trim();
                const urlBr = (destUrl.searchParams.get('branch') || '').trim();
                if (urlProj) {
                    localStorage.setItem('activeProject', urlProj);
                    if (urlBr) localStorage.setItem(`branch_${urlProj}`, urlBr === 'all' ? '%' : urlBr);
                }
            } catch (_) {
                // ignore
            }
        }

        if (!isInitialized) {
            isInitialized = true;
            await initWorkbench();
        } else {
            await applyRouteFromLocation({ replaceHistory: true, skipTransition: true, autoRunQuery: true });
        }
    }

    function applyRoleRestrictions() {
        const isAdmin = userRole === 'admin';
        const isReader = isReaderRole();

        if (btnTabAuth) {
            btnTabAuth.hidden = !isAdmin;
            btnTabAuth.style.display = isAdmin ? 'flex' : 'none';
        }
        if (btnTabProfile) {
            btnTabProfile.hidden = false;
            btnTabProfile.style.display = 'flex';
        }

        const addMemoryBox = document.querySelector('.add-memory-box');
        const uploadDocBox = document.querySelector('.upload-doc-box');
        const dangerZone = document.querySelector('.danger-zone');
        const projectRegCard = projectProvisionCard || document.querySelector('#projectRegistryTab .auth-layout > .card:first-child');
        const projectRegForm = document.getElementById('projectRegistryForm');
        const mcpConfigCard = document.querySelector('#mcpTab .mcp-config-container');
        const extMcpCard = document.getElementById('externalMcpCard');

        if (addMemoryBox) addMemoryBox.style.display = isReader ? 'none' : 'flex';
        if (uploadDocBox) uploadDocBox.style.display = isReader ? 'none' : 'flex';
        if (dangerZone) dangerZone.style.display = isReader ? 'none' : 'flex';
        if (btnAddProject) btnAddProject.style.display = isReader ? 'none' : 'inline-flex';
        if (btnRunBuildKb) btnRunBuildKb.style.display = isReader ? 'none' : 'inline-flex';
        if (projectRegCard) projectRegCard.classList.toggle('is-hidden', isReader);
        if (projectRegForm) projectRegForm.classList.toggle('is-hidden', isReader);
        document.querySelectorAll('.btn-new-branch-schema, .btn-drop-branch-schema, .btn-drop-project-db').forEach(btn => {
            btn.classList.toggle('is-hidden', isReader);
        });
        if (mcpConfigCard) mcpConfigCard.style.display = isAdmin ? 'flex' : 'none';
        if (extMcpCard) extMcpCard.style.display = isAdmin ? 'block' : 'none';
        if (btnReloadPlugins) btnReloadPlugins.style.display = isAdmin ? 'inline-flex' : 'none';

        const activeAuthTab = document.getElementById('authAdminTab');
        if (!isAdmin && activeAuthTab && activeAuthTab.classList.contains('active')) {
            navigateTo('/app/overview', { replace: true });
        } else if (isInitialized) {
            renderDomainSubnav(getRouteByPath(activeRoutePath));
        }
    }

    if (passwordLoginForm) {
        passwordLoginForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const identifier = (loginIdentifier?.value || '').trim();
            const password = loginPassword?.value || '';
            const remember_me = Boolean(loginRememberMe?.checked);
            if (!identifier || !password) return;

            clearLoginError();
            if (btnPasswordLoginSubmit) {
                btnPasswordLoginSubmit.disabled = true;
                btnPasswordLoginSubmit.innerHTML = '<span>Signing In...</span> <i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i>';
            }

            try {
                const res = await fetch('/auth/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ identifier, password, remember_me })
                });
                if (!res.ok) {
                    throw new Error(await extractErrorMessage(res, 'Invalid username, email, or password.'));
                }
                const data = await res.json();
                if (loginPassword) loginPassword.value = '';
                handleSuccessfulLogin(data, data.session_token || '');
                showToast(`Signed in as ${data.account?.username || identifier}`, 'success');
            } catch (err) {
                showLoginError(err.message);
            } finally {
                if (btnPasswordLoginSubmit) {
                    btnPasswordLoginSubmit.disabled = false;
                    btnPasswordLoginSubmit.innerHTML = '<span>Sign In</span> <i class="fa-solid fa-right-to-bracket" aria-hidden="true"></i>';
                }
            }
        });
    }

    if (loginForm) {
        loginForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const token = (loginToken?.value || '').trim();
            if (!token) return;

            clearLoginError();
            if (btnLoginSubmit) {
                btnLoginSubmit.disabled = true;
                btnLoginSubmit.innerHTML = '<span>Signing In...</span> <i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i>';
            }

            try {
                const res = await fetch('/auth/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ token })
                });
                if (!res.ok) {
                    throw new Error(await extractErrorMessage(res, 'Invalid access token.'));
                }
                const data = await res.json();
                if (loginToken) loginToken.value = '';
                handleSuccessfulLogin(data, data.session_token || token);
                showToast(`Signed in as ${data.account?.username || 'user'}`, 'success');
            } catch (err) {
                showLoginError(err.message);
            } finally {
                if (btnLoginSubmit) {
                    btnLoginSubmit.disabled = false;
                    btnLoginSubmit.innerHTML = '<span>Sign In with Token</span> <i class="fa-solid fa-key" aria-hidden="true"></i>';
                }
            }
        });
    }

    if (registerForm) {
        registerForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const usernameOk = syncFieldValidationState(regUsername, true);
            const emailOk = !regEmail?.value.trim() || syncFieldValidationState(regEmail, true);
            const pwOk = syncFieldValidationState(regPassword, true);
            if (!usernameOk || !emailOk || !pwOk) return;

            const username = (regUsername?.value || '').trim();
            const display_name = (regDisplayName?.value || '').trim() || null;
            const email = (regEmail?.value || '').trim() || null;
            const password = regPassword?.value || '';

            clearLoginError();
            if (btnRegisterSubmit) {
                btnRegisterSubmit.disabled = true;
                btnRegisterSubmit.innerHTML = '<span>Creating Account...</span> <i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i>';
            }

            try {
                const res = await fetch('/auth/register', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username, display_name, email, password })
                });
                if (!res.ok) {
                    throw new Error(await extractErrorMessage(res, 'Account registration failed.'));
                }
                const data = await res.json();
                registerForm.reset();
                handleSuccessfulLogin(data, data.session_token || '');
                showToast(`Welcome, ${data.account?.username || username}!`, 'success');
            } catch (err) {
                showLoginError(err.message);
            } finally {
                if (btnRegisterSubmit) {
                    btnRegisterSubmit.disabled = false;
                    btnRegisterSubmit.innerHTML = '<span>Create Account</span> <i class="fa-solid fa-user-plus" aria-hidden="true"></i>';
                }
            }
        });
    }

    if (btnLogout) {
        btnLogout.addEventListener('click', async () => {
            try {
                await fetch('/auth/logout', { method: 'POST' });
            } catch (err) {
                console.warn('Logout request error:', err);
            }
            setStoredToken('');
            currentAccount = null;
            userRole = 'reader';
            isInitialized = false;
            pendingNextUrl = '';
            if (loginToken) loginToken.value = '';
            if (loginPassword) loginPassword.value = '';
            await loadAuthConfig();
            window.history.pushState({ auth: true }, '', '/login');
            showLoginGate({ preserveAuthUrl: true });
            showToast('Signed out cleanly.', 'info', 2500);
        });
    }

    if (btnBannerSetPassword) {
        btnBannerSetPassword.addEventListener('click', () => {
            navigateTo(buildContextualHref('/app/settings/profile'));
            if (newPasswordInput) {
                requestAnimationFrame(() => newPasswordInput.focus());
            }
        });
    }

    // =========================================================================
    // 9. Workspace Context, URL Router (History API), Breadcrumbs & Sub-Nav
    // =========================================================================
    function getProjectContext() {
        const baseProject = (currentProjectSelect?.value || '').trim() || 'default';
        const rawBranch = currentBranchSelect ? currentBranchSelect.value : '';
        const branch = (!rawBranch || rawBranch === '%') ? 'main' : rawBranch;
        return { baseProject, rawBranch, branch, scopedId: activeUser || 'default' };
    }

    function buildContextualSearchParams(targetPath, { includeViewState = false, baseSearch = '', extraParams = {} } = {}) {
        const params = new URLSearchParams(baseSearch || '');
        // Strip auth-only params on /app/... routes
        params.delete('next');

        const { baseProject, rawBranch } = getProjectContext();
        if (!baseSearch) {
            if (baseProject && baseProject !== 'default') {
                params.set('project', baseProject);
            } else {
                params.delete('project');
            }
            if (rawBranch && rawBranch !== '%') {
                params.set('branch', rawBranch);
            } else {
                params.delete('branch');
            }
        } else {
            if (!params.has('project') && baseProject && baseProject !== 'default') {
                params.set('project', baseProject);
            }
            if (!params.has('branch') && rawBranch && rawBranch !== '%') {
                params.set('branch', rawBranch);
            }
        }

        if (includeViewState) {
            if (targetPath === '/app/workspace/search') {
                const qVal = (searchQuery?.value || '').trim();
                const modeEl = document.querySelector('input[name="searchMode"]:checked');
                const modeVal = modeEl ? modeEl.value : 'rag';
                if (qVal) params.set('q', qVal);
                else params.delete('q');
                if (qVal || modeVal === 'raw') params.set('mode', modeVal);
                else params.delete('mode');
            } else {
                params.delete('q');
                if (params.get('mode') === 'rag' || params.get('mode') === 'raw') {
                    params.delete('mode');
                }
            }

            if (targetPath === '/app/graph/explorer') {
                const symVal = (cgSearchInput?.value || '').trim();
                const kindsVal = cgKindSelect ? cgKindSelect.value : 'class,function,method,file';
                if (symVal) params.set('symbol', symVal);
                else params.delete('symbol');
                if (kindsVal && kindsVal !== 'class,function,method,file') params.set('kinds', kindsVal);
                else params.delete('kinds');
            } else {
                params.delete('symbol');
                params.delete('kinds');
            }

            if (targetPath === '/app/graph/diff') {
                const baseVal = (diffBaseProject?.value || '').trim();
                const targetVal = (diffTargetProject?.value || '').trim();
                if (baseVal) params.set('base', baseVal);
                else params.delete('base');
                if (targetVal) params.set('target', targetVal);
                else params.delete('target');
            } else {
                params.delete('base');
                params.delete('target');
            }
        }

        Object.entries(extraParams || {}).forEach(([k, v]) => {
            if (v === null || v === undefined || v === '') params.delete(k);
            else params.set(k, String(v));
        });

        return params;
    }

    function buildContextualHref(routePath, opts = {}) {
        const normPath = normalizePathname(routePath);
        if (normPath === '/login' || normPath === '/register') return normPath;
        const params = buildContextualSearchParams(normPath, opts);
        const qs = params.toString();
        return qs ? `${normPath}?${qs}` : normPath;
    }

    function updateDocumentTitleAndRouteBadge(route) {
        const activeRoute = route || getRouteByPath(activeRoutePath);
        const { baseProject } = getProjectContext();
        if (loginScreen && loginScreen.style.display !== 'none') {
            return;
        }
        document.title = `${activeRoute.title} · ${baseProject} — ProjectBrain v3.0`;
        if (activeRoutePathBadge) {
            const fullRel = window.location.pathname + window.location.search;
            activeRoutePathBadge.textContent = fullRel;
            activeRoutePathBadge.title = window.location.href;
        }
    }

    function refreshAllRouteLinks() {
        document.querySelectorAll('[data-route]').forEach(el => {
            const baseRoute = el.getAttribute('data-route');
            if (!baseRoute) return;
            el.setAttribute('href', buildContextualHref(baseRoute));
        });
        updateDocumentTitleAndRouteBadge(getRouteByPath(activeRoutePath));
    }

    function renderDomainSubnav(activeRoute) {
        if (!domainSubnavList || !activeRoute) return;
        const siblings = ROUTES.filter(r =>
            r.domainSlug === activeRoute.domainSlug && (!r.adminOnly || userRole === 'admin')
        );
        domainSubnavList.innerHTML = siblings.map(r => {
            const isCurrent = r.path === activeRoute.path;
            const href = buildContextualHref(r.path);
            return `
                <li>
                    <a href="${escapeHTML(href)}" class="domain-subnav-link" data-route="${escapeHTML(r.path)}" data-route-link${isCurrent ? ' aria-current="page"' : ''}>
                        <i class="fa-solid ${escapeHTML(r.icon)}" aria-hidden="true"></i>
                        <span>${escapeHTML(r.title)}</span>
                    </a>
                </li>
            `;
        }).join('');
        if (domainSubnavBar) {
            domainSubnavBar.setAttribute('aria-label', `${activeRoute.domainLabel} routes`);
        }
    }

    function updateBreadcrumbsForRoute(route) {
        if (!route) return;
        const domainMeta = DOMAINS[route.domainSlug] || DOMAINS.workspace;
        if (breadcrumbDomainLink) {
            breadcrumbDomainLink.textContent = route.domainLabel;
            breadcrumbDomainLink.setAttribute('data-route', domainMeta.defaultPath);
            breadcrumbDomainLink.setAttribute('href', buildContextualHref(domainMeta.defaultPath));
        }
        if (breadcrumbPageLink) {
            breadcrumbPageLink.textContent = route.title;
            breadcrumbPageLink.setAttribute('data-route', route.path);
            breadcrumbPageLink.setAttribute('href', buildContextualHref(route.path));
            breadcrumbPageLink.setAttribute('aria-current', 'page');
        }
    }

    function focusActiveRouteHeading(tabEl) {
        if (!tabEl) return;
        const heading = tabEl.querySelector('h2');
        if (!heading) return;
        if (!heading.hasAttribute('tabindex')) {
            heading.setAttribute('tabindex', '-1');
        }
        heading.focus({ preventScroll: true });
    }

    async function copyActiveRouteUrl() {
        const shareUrl = window.location.href;
        try {
            await navigator.clipboard.writeText(shareUrl);
            if (btnCopyRouteUrl) {
                const icon = btnCopyRouteUrl.querySelector('i');
                if (icon) {
                    const prevClass = icon.className;
                    icon.className = 'fa-solid fa-check';
                    setTimeout(() => {
                        icon.className = prevClass;
                    }, 1600);
                }
            }
            showToast('Shareable route URL copied to clipboard.', 'success', 2400);
        } catch (err) {
            showToast('Failed to copy URL: ' + err.message, 'error');
        }
    }

    if (btnCopyRouteUrl) {
        btnCopyRouteUrl.addEventListener('click', copyActiveRouteUrl);
    }

    function syncCurrentRouteUrl({ replace = true } = {}) {
        if (loginScreen && loginScreen.style.display !== 'none') return;
        const normPath = normalizePathname(activeRoutePath || window.location.pathname);
        if (!normPath.startsWith('/app/')) return;
        const params = buildContextualSearchParams(normPath, { includeViewState: true });
        const qs = params.toString();
        const nextUrl = qs ? `${normPath}?${qs}` : normPath;
        const currentUrl = window.location.pathname + window.location.search;
        if (nextUrl !== currentUrl) {
            if (replace) {
                window.history.replaceState({ route: normPath }, '', nextUrl);
            } else {
                window.history.pushState({ route: normPath }, '', nextUrl);
            }
        }
        refreshAllRouteLinks();
    }

    function updateBranchDropdown(baseId) {
        if (!currentBranchSelect || !branchSelectorContainer) return;
        currentBranchSelect.innerHTML = '';
        const group = projectGroups[baseId];

        if (!group || !group.branches || group.branches.size === 0) {
            branchSelectorContainer.style.display = 'none';
            const opt = document.createElement('option');
            opt.value = '';
            opt.textContent = 'Default Branch';
            currentBranchSelect.appendChild(opt);
            return;
        }

        branchSelectorContainer.style.display = 'flex';

        const optAll = document.createElement('option');
        optAll.value = '%';
        optAll.textContent = 'All Branches';
        currentBranchSelect.appendChild(optAll);

        const branchList = Array.from(group.branches).sort();
        branchList.forEach(branch => {
            const opt = document.createElement('option');
            opt.value = branch;
            opt.textContent = branch;
            currentBranchSelect.appendChild(opt);
        });

        const cached = localStorage.getItem(`branch_${baseId}`) || '%';
        if (Array.from(currentBranchSelect.options).some(o => o.value === cached)) {
            currentBranchSelect.value = cached;
        } else {
            currentBranchSelect.value = '%';
        }
    }

    function updateActiveTenantStorageHint() {
        if (!activeTenantStorageHint) return;
        const { baseProject, rawBranch } = getProjectContext();
        const projTopo = topologyProjectsMap[baseProject];
        const dbName = toDisplayText(projTopo?.db_name, sanitizeProjectDbName(baseProject));
        const rootBranch = toDisplayText(projTopo?.root_branch, 'main');
        const textSpan = activeTenantStorageHint.querySelector('span');

        if (rawBranch === '%') {
            activeTenantStorageHint.classList.add('is-warning');
            const msg = `Target Storage: DB ${dbName} → All Branches (select a specific branch to upload)`;
            if (textSpan) textSpan.textContent = msg;
            else activeTenantStorageHint.textContent = msg;
            return;
        }

        activeTenantStorageHint.classList.remove('is-warning');
        const targetBranch = rawBranch || rootBranch;
        const branchList = Array.isArray(projTopo?.branches) ? projTopo.branches : [];
        const matchedBranch = branchList.find(b => b?.branch_name === targetBranch);
        const schemaName = toDisplayText(
            matchedBranch?.schema_name,
            sanitizeBranchSchemaName(targetBranch, rootBranch)
        );
        const msg = `Target Storage: DB ${dbName} → Schema ${schemaName} (Graph + Docs)`;
        if (textSpan) textSpan.textContent = msg;
        else activeTenantStorageHint.textContent = msg;
    }

    function recalculateActiveUser() {
        const selectedProj = (currentProjectSelect?.value || '').trim() || 'default';
        const selectedBranch = currentBranchSelect ? currentBranchSelect.value : '';

        if (selectedProj === 'default') {
            activeUser = 'default';
        } else if (selectedBranch === '%') {
            activeUser = `${selectedProj}:%`;
        } else if (selectedBranch) {
            activeUser = `${selectedProj}:${selectedBranch}`;
        } else {
            activeUser = selectedProj;
        }
        updateLocalProjectConfig();
        updateActiveTenantStorageHint();
        const { branch } = getProjectContext();
        if (kbBranchInput && branch) {
            kbBranchInput.value = branch;
        }
    }

    function applyQueryStateFromSearch(searchStr) {
        const params = new URLSearchParams(searchStr || '');
        const urlProj = (params.get('project') || '').trim();
        const rawUrlBranch = (params.get('branch') || '').trim();
        const urlBranch = rawUrlBranch === 'all' ? '%' : rawUrlBranch;

        let contextChanged = false;
        if (urlProj && currentProjectSelect) {
            if (!Array.from(currentProjectSelect.options).some(o => o.value === urlProj)) {
                const opt = document.createElement('option');
                opt.value = urlProj;
                opt.textContent = urlProj;
                currentProjectSelect.appendChild(opt);
            }
            if (currentProjectSelect.value !== urlProj) {
                currentProjectSelect.value = urlProj;
                localStorage.setItem('activeProject', urlProj);
                updateBranchDropdown(urlProj);
                contextChanged = true;
            }
            if (urlBranch && currentBranchSelect) {
                if (!Array.from(currentBranchSelect.options).some(o => o.value === urlBranch)) {
                    if (branchSelectorContainer) branchSelectorContainer.style.display = 'flex';
                    const optB = document.createElement('option');
                    optB.value = urlBranch;
                    optB.textContent = urlBranch;
                    currentBranchSelect.appendChild(optB);
                }
                if (currentBranchSelect.value !== urlBranch) {
                    currentBranchSelect.value = urlBranch;
                    localStorage.setItem(`branch_${urlProj}`, urlBranch);
                    contextChanged = true;
                }
            }
        }

        if (contextChanged) {
            recalculateActiveUser();
            syncActiveProjectOnServer();
            if (cgProjectSelect) {
                if (activeUser !== 'default' && !Array.from(cgProjectSelect.options).some(o => o.value === activeUser)) {
                    const optCg = document.createElement('option');
                    optCg.value = activeUser;
                    optCg.textContent = activeUser;
                    cgProjectSelect.appendChild(optCg);
                }
                cgProjectSelect.value = activeUser === 'default' ? '' : activeUser;
            }
        }

        return {
            params,
            q: params.has('q') ? (params.get('q') || '') : null,
            mode: params.get('mode') || null,
            symbol: params.has('symbol') ? (params.get('symbol') || '') : null,
            kinds: params.get('kinds') || null,
            base: params.get('base') || null,
            target: params.get('target') || null
        };
    }

    function activateRoute(route, { skipTransition = false, skipFocus = false, queryContext = null, autoRunQuery = false } = {}) {
        if (!route) return;
        if (route.adminOnly && userRole !== 'admin') {
            showToast('Admin privileges required for Identity & RBAC.', 'warning');
            navigateTo(buildContextualHref('/app/overview'), { replace: true, skipTransition: true });
            return;
        }

        const targetTabId = route.tabId;
        const targetContent = document.getElementById(targetTabId);
        if (!targetContent) return;

        const prevActiveContent = Array.from(tabContents).find(c => c.classList.contains('active'));
        const prevTabId = prevActiveContent ? prevActiveContent.id : '';

        if (targetTabId !== 'codeGraphTab' && graphLayout?.classList.contains('is-fullscreen')) {
            setGraphFullscreen(false);
        }

        activeRoutePath = route.path;

        const runDomSwitch = () => {
            tabLinks.forEach(link => {
                const isMatch = link.getAttribute('data-tab') === targetTabId;
                link.classList.toggle('active', isMatch);
                if (isMatch) {
                    link.setAttribute('aria-current', 'page');
                } else {
                    link.removeAttribute('aria-current');
                }
            });
            tabContents.forEach(content => {
                content.classList.toggle('active', content === targetContent);
            });
            updateBreadcrumbsForRoute(route);
            renderDomainSubnav(route);
            refreshAllRouteLinks();
        };

        const involvesCodeGraph = targetTabId === 'codeGraphTab' || prevTabId === 'codeGraphTab';
        const canAnimateTransition = (
            !skipTransition &&
            !involvesCodeGraph &&
            prevTabId !== targetTabId &&
            typeof document.startViewTransition === 'function' &&
            !window.matchMedia('(prefers-reduced-motion: reduce)').matches &&
            !hasOpenModalDialog()
        );

        if (canAnimateTransition) {
            const vt = document.startViewTransition(runDomSwitch);
            vt.finished.finally(() => {
                if (!skipFocus) focusActiveRouteHeading(targetContent);
            });
        } else {
            runDomSwitch();
            if (!skipFocus) focusActiveRouteHeading(targetContent);
        }

        // Trigger data loaders and apply view-specific query parameters
        if (targetTabId === 'dashboardTab') {
            loadStats();
            loadPluginSummary();
            loadHardwareStatus();
        } else if (targetTabId === 'projectRegistryTab') {
            loadProjectsList();
        } else if (targetTabId === 'memoriesTab') {
            loadMemories();
            loadIngestedDocuments();
        } else if (targetTabId === 'searchTab') {
            if (queryContext) {
                if (queryContext.mode === 'raw' || queryContext.mode === 'rag') {
                    const radio = document.querySelector(`input[name="searchMode"][value="${queryContext.mode}"]`);
                    if (radio) radio.checked = true;
                }
                if (queryContext.q !== null && searchQuery) {
                    searchQuery.value = queryContext.q;
                }
                if (autoRunQuery && queryContext.q && queryContext.q.trim()) {
                    performSearch({ skipUrlSync: true });
                }
            }
        } else if (targetTabId === 'codeGraphTab') {
            if (cgProjectSelect) {
                const desiredCg = activeUser === 'default' ? '' : activeUser;
                if (desiredCg && !Array.from(cgProjectSelect.options).some(o => o.value === desiredCg)) {
                    const opt = document.createElement('option');
                    opt.value = desiredCg;
                    opt.textContent = desiredCg;
                    cgProjectSelect.appendChild(opt);
                }
                cgProjectSelect.value = desiredCg;
            }
            if (queryContext) {
                if (queryContext.symbol !== null && cgSearchInput) {
                    cgSearchInput.value = queryContext.symbol;
                }
                if (queryContext.kinds && cgKindSelect) {
                    if (!Array.from(cgKindSelect.options).some(o => o.value === queryContext.kinds)) {
                        const optK = document.createElement('option');
                        optK.value = queryContext.kinds;
                        optK.textContent = queryContext.kinds;
                        cgKindSelect.appendChild(optK);
                    }
                    cgKindSelect.value = queryContext.kinds;
                }
            }
            setTimeout(() => loadCodeGraph({ skipUrlSync: true }), 40);
        } else if (targetTabId === 'compareTab') {
            if (queryContext) {
                if (queryContext.base && diffBaseProject) {
                    if (!Array.from(diffBaseProject.options).some(o => o.value === queryContext.base)) {
                        const optB = document.createElement('option');
                        optB.value = queryContext.base;
                        optB.textContent = queryContext.base;
                        diffBaseProject.appendChild(optB);
                    }
                    diffBaseProject.value = queryContext.base;
                }
                if (queryContext.target && diffTargetProject) {
                    if (!Array.from(diffTargetProject.options).some(o => o.value === queryContext.target)) {
                        const optT = document.createElement('option');
                        optT.value = queryContext.target;
                        optT.textContent = queryContext.target;
                        diffTargetProject.appendChild(optT);
                    }
                    diffTargetProject.value = queryContext.target;
                }
                if (autoRunQuery && queryContext.base && queryContext.target) {
                    compareVersions({ skipUrlSync: true });
                }
            }
        } else if (targetTabId === 'kbTab') {
            loadKbCoverage();
        } else if (targetTabId === 'pluginsTab') {
            loadHardwareStatus();
            loadPlugins();
        } else if (targetTabId === 'mcpTab') {
            if (userRole === 'admin') {
                loadMcpStatus();
            }
            updateLocalProjectConfig();
        } else if (targetTabId === 'aiSettingsTab') {
            loadAiSettings({ probeOllama: true });
        } else if (targetTabId === 'profileTab') {
            loadProfileView();
        } else if (targetTabId === 'authAdminTab') {
            loadAuthAdminView();
        }
    }

    function navigateTo(rawUrl, { replace = false, skipTransition = false, skipFocus = false, autoRunQuery = true } = {}) {
        let parsed;
        try {
            parsed = new URL(rawUrl || '/app/overview', window.location.origin);
        } catch (_) {
            parsed = new URL('/app/overview', window.location.origin);
        }

        const normPath = normalizePathname(parsed.pathname);
        if (normPath === '/login' || normPath === '/register') {
            const nextFull = normPath + parsed.search;
            if (nextFull !== window.location.pathname + window.location.search) {
                if (replace) window.history.replaceState({ auth: true }, '', nextFull);
                else window.history.pushState({ auth: true }, '', nextFull);
            }
            showLoginGate({ preserveAuthUrl: true });
            return;
        }

        const route = getRouteByPath(normPath);
        if (route.adminOnly && userRole !== 'admin') {
            showToast('Admin privileges required for Identity & RBAC.', 'warning');
            return;
        }

        const queryContext = applyQueryStateFromSearch(parsed.search);
        const mergedParams = buildContextualSearchParams(route.path, { baseSearch: parsed.search });
        const qs = mergedParams.toString();
        const canonicalUrl = qs ? `${route.path}?${qs}` : route.path;
        const currentUrl = window.location.pathname + window.location.search;

        if (canonicalUrl !== currentUrl) {
            if (replace) {
                window.history.replaceState({ route: route.path }, '', canonicalUrl);
            } else {
                window.history.pushState({ route: route.path }, '', canonicalUrl);
            }
        }

        activateRoute(route, { skipTransition, skipFocus, queryContext, autoRunQuery });
    }

    async function applyRouteFromLocation({ replaceHistory = true, skipTransition = false, skipFocus = false, autoRunQuery = true } = {}) {
        const rawPath = window.location.pathname;
        const rawSearch = window.location.search;
        const normPath = normalizePathname(rawPath);

        if (normPath === '/login' || normPath === '/register') {
            showLoginGate({ preserveAuthUrl: true });
            return;
        }

        if (!currentAccount) {
            showLoginGate({ preserveAuthUrl: false });
            return;
        }

        let route = getRouteByPath(normPath);
        if (route.adminOnly && userRole !== 'admin') {
            showToast('Admin privileges required for Identity & RBAC.', 'warning');
            route = ROUTES[0];
        }

        const queryContext = applyQueryStateFromSearch(rawSearch);
        const mergedParams = buildContextualSearchParams(route.path, { baseSearch: rawSearch });
        const qs = mergedParams.toString();
        const canonicalUrl = qs ? `${route.path}?${qs}` : route.path;

        if (replaceHistory && canonicalUrl !== rawPath + rawSearch) {
            window.history.replaceState({ route: route.path }, '', canonicalUrl);
        }

        if (loginScreen && loginScreen.style.display !== 'none') {
            loginScreen.style.display = 'none';
            if (dashboardContainer) dashboardContainer.style.display = 'grid';
            if (userProfileWidget) userProfileWidget.style.display = 'flex';
        }

        activateRoute(route, { skipTransition, skipFocus, queryContext, autoRunQuery });
    }

    function switchTab(targetTabId, opts = {}) {
        if (!targetTabId) return;
        const route = getRouteByTabId(targetTabId);
        navigateTo(buildContextualHref(route.path), opts);
    }

    // Intercept normal left-clicks on internal route links while preserving Cmd/Ctrl/Middle/Right-click Open in New Tab
    document.addEventListener('click', (e) => {
        if (e.defaultPrevented) return;
        if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        const anchor = e.target.closest('a[href]');
        if (!anchor) return;
        if (anchor.target && anchor.target !== '_self') return;
        if (anchor.hasAttribute('download')) return;

        const rawHref = anchor.getAttribute('href') || '';
        if (!rawHref.startsWith('/') || rawHref.startsWith('//')) return;

        let url;
        try {
            url = new URL(rawHref, window.location.origin);
        } catch (_) {
            return;
        }
        const normPath = normalizePathname(url.pathname);
        if (normPath.startsWith('/app/') || normPath === '/login' || normPath === '/register') {
            e.preventDefault();
            if (aiSettingsDialog?.open && anchor.id === 'btnOpenFullAiPage') {
                aiSettingsDialog.close('navigate');
            }
            navigateTo(url.pathname + url.search);
        }
    });

    window.addEventListener('popstate', () => {
        applyRouteFromLocation({
            replaceHistory: false,
            skipTransition: false,
            skipFocus: false,
            autoRunQuery: true
        });
    });

    function setupTabsAndSidebar() {
        document.querySelectorAll('[data-tab-jump]').forEach(btn => {
            if (btn.tagName === 'A') return;
            btn.addEventListener('click', () => {
                const targetTab = btn.getAttribute('data-tab-jump');
                switchTab(targetTab);
            });
        });

        if (btnToggleSidebar && dashboardContainer) {
            const savedCollapsed = localStorage.getItem('pb-sidebar-collapsed') === 'true';
            if (savedCollapsed) {
                dashboardContainer.classList.add('sidebar-collapsed');
                btnToggleSidebar.setAttribute('aria-expanded', 'false');
            }
            btnToggleSidebar.addEventListener('click', () => {
                const collapsed = dashboardContainer.classList.toggle('sidebar-collapsed');
                btnToggleSidebar.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
                localStorage.setItem('pb-sidebar-collapsed', collapsed ? 'true' : 'false');
            });
        }
    }

    // =========================================================================
    // 10. Workbench Initialization & Event Binding
    // =========================================================================
    async function initWorkbench() {
        if (!listenersBound) {
            listenersBound = true;
            setupTabsAndSidebar();
            bindWorkbenchListeners();
            setupV3SubTabs();
        }

        await loadProjects();
        await applyRouteFromLocation({
            replaceHistory: true,
            skipTransition: true,
            skipFocus: true,
            autoRunQuery: true
        });

        loadStats();
        loadMemories();
        loadIngestedDocuments();
        loadPluginSummary();
        loadHardwareStatus();
        loadAiSettings({ probeOllama: false });
        if (userRole === 'admin') {
            loadMcpStatus();
        }

        syncActiveProjectOnServer();
        updateLocalProjectConfig();
    }

    function syncActiveProjectOnServer() {
        fetch('/memory/active-project', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ project_id: activeUser })
        }).catch(err => console.error('Failed to sync active project:', err));
    }

    function onWorkspaceContextChanged() {
        syncCurrentRouteUrl({ replace: true });
        syncActiveProjectOnServer();
        loadStats();
        loadMemories();
        loadIngestedDocuments();
        loadPluginSummary();

        if (cgProjectSelect) {
            cgProjectSelect.value = activeUser === 'default' ? '' : activeUser;
        }
        const cgTab = document.getElementById('codeGraphTab');
        if (cgTab && cgTab.classList.contains('active')) {
            loadCodeGraph({ skipUrlSync: true });
        }
        const pluginsTab = document.getElementById('pluginsTab');
        if (pluginsTab && pluginsTab.classList.contains('active')) {
            loadPlugins();
        }
        const kbTab = document.getElementById('kbTab');
        if (kbTab && kbTab.classList.contains('active')) {
            loadKbCoverage();
        }
    }

    function bindWorkbenchListeners() {
        if (currentUser) {
            currentUser.value = localStorage.getItem('currentUser') || currentAccount?.username || '';
            currentUser.addEventListener('input', () => {
                localStorage.setItem('currentUser', currentUser.value.trim());
            });
        }

        if (currentProjectSelect) {
            currentProjectSelect.addEventListener('change', (e) => {
                const selectedProj = e.target.value.trim() || 'default';
                localStorage.setItem('activeProject', selectedProj);
                updateBranchDropdown(selectedProj);
                recalculateActiveUser();
                onWorkspaceContextChanged();
            });
        }

        if (currentBranchSelect) {
            currentBranchSelect.addEventListener('change', () => {
                const selectedProj = (currentProjectSelect?.value || '').trim() || 'default';
                localStorage.setItem(`branch_${selectedProj}`, currentBranchSelect.value);
                recalculateActiveUser();
                onWorkspaceContextChanged();
            });
        }

        if (btnCompareVersions) btnCompareVersions.addEventListener('click', () => compareVersions());
        if (diffBaseProject) diffBaseProject.addEventListener('change', () => syncCurrentRouteUrl({ replace: true }));
        if (diffTargetProject) diffTargetProject.addEventListener('change', () => syncCurrentRouteUrl({ replace: true }));
        if (btnSaveMemory) btnSaveMemory.addEventListener('click', saveMemory);
        if (btnUploadDoc) btnUploadDoc.addEventListener('click', uploadDocument);
        if (docUploadFile) docUploadFile.addEventListener('change', handleFileSelect);
        if (btnRefreshDocs) btnRefreshDocs.addEventListener('click', loadIngestedDocuments);
        if (memoryFilterInput) memoryFilterInput.addEventListener('input', renderFilteredMemories);

        if (uploadDropzone && docUploadFile) {
            uploadDropzone.addEventListener('dragover', (e) => {
                e.preventDefault();
                uploadDropzone.style.borderColor = 'var(--brand)';
                uploadDropzone.style.background = 'var(--brand-tint)';
            });
            uploadDropzone.addEventListener('dragleave', () => {
                uploadDropzone.style.borderColor = '';
                uploadDropzone.style.background = '';
            });
            uploadDropzone.addEventListener('drop', (e) => {
                e.preventDefault();
                uploadDropzone.style.borderColor = '';
                uploadDropzone.style.background = '';
                if (e.dataTransfer.files.length > 0) {
                    docUploadFile.files = e.dataTransfer.files;
                    handleFileSelect();
                }
            });
        }

        if (btnRefreshHistory) btnRefreshHistory.addEventListener('click', () => {
            loadMemories();
            loadIngestedDocuments();
        });
        if (btnSearch) btnSearch.addEventListener('click', () => performSearch());
        document.querySelectorAll('input[name="searchMode"]').forEach(radio => {
            radio.addEventListener('change', () => syncCurrentRouteUrl({ replace: true }));
        });
        if (btnGenerateDoc) btnGenerateDoc.addEventListener('click', generateDoc);
        if (btnCopyDoc) btnCopyDoc.addEventListener('click', copyDocToClipboard);
        if (btnDeleteAll) btnDeleteAll.addEventListener('click', deleteAllMemories);

        // AI Settings Modal & Dedicated Page (/app/settings/ai)
        if (btnRefreshAiProbe) btnRefreshAiProbe.addEventListener('click', () => loadAiSettings({ probeOllama: true }));
        if (btnPageProbeAi) btnPageProbeAi.addEventListener('click', () => loadAiSettings({ probeOllama: true }));
        if (btnTestAiSettings) btnTestAiSettings.addEventListener('click', () => testAiSettingsConnection('ai'));
        if (btnPageTestAi) btnPageTestAi.addEventListener('click', () => testAiSettingsConnection('aiPage'));
        if (aiSettingsForm) aiSettingsForm.addEventListener('submit', (e) => saveAiSettings(e, 'ai'));
        if (aiPageForm) aiPageForm.addEventListener('submit', (e) => saveAiSettings(e, 'aiPage'));
        if (aiSettingsDialog) {
            const closeBtns = aiSettingsDialog.querySelectorAll('[command="close"][commandfor="aiSettingsDialog"]');
            closeBtns.forEach(btn => {
                btn.addEventListener('click', () => {
                    if (aiSettingsDialog.open) aiSettingsDialog.close('cancel');
                });
            });
            if (btnCancelAiSettings) {
                btnCancelAiSettings.addEventListener('click', () => {
                    if (aiSettingsDialog.open) aiSettingsDialog.close('cancel');
                });
            }
        }
        document.addEventListener('click', (e) => {
            const trigger = e.target.closest('[data-open-ai-settings="true"]');
            if (trigger) {
                navigateTo(buildContextualHref('/app/settings/ai'));
            }
        });

        if (searchQuery) {
            searchQuery.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') performSearch();
            });
        }
        if (copilotPrompt) {
            copilotPrompt.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) generateDoc();
            });
        }

        // MCP Listeners
        mcpTransportRadios.forEach(radio => {
            radio.addEventListener('change', (e) => {
                if (e.target.value === 'stdio') {
                    if (mcpStdioConfig) mcpStdioConfig.style.display = 'flex';
                    if (mcpSseConfig) mcpSseConfig.style.display = 'none';
                } else {
                    if (mcpStdioConfig) mcpStdioConfig.style.display = 'none';
                    if (mcpSseConfig) mcpSseConfig.style.display = 'flex';
                }
            });
        });

        if (btnRegisterMcp) btnRegisterMcp.addEventListener('click', registerMcp);
        if (btnUnregisterMcp) btnUnregisterMcp.addEventListener('click', unregisterMcp);
        if (btnSaveExtMcp) btnSaveExtMcp.addEventListener('click', saveExternalMcp);

        [
            { id: 'mcpCopilotConfig', label: 'Copilot Configuration' },
            { id: 'mcpClaudeConfig', label: 'Claude / Cursor SSE Configuration' },
            { id: 'mcpStdioLocalConfig', label: 'Stdio Local Configuration' },
            { id: 'mcpStdioRemoteConfig', label: 'Stdio Remote DB Configuration' }
        ].forEach(({ id, label }) => {
            const pre = document.getElementById(id);
            if (!pre) return;
            pre.addEventListener('click', async () => {
                try {
                    await navigator.clipboard.writeText(pre.textContent);
                    showToast(`${label} copied to clipboard!`, 'success', 2500);
                } catch (err) {
                    showToast('Failed to copy: ' + err.message, 'error');
                }
            });
        });

        // CodeGraph Listeners
        if (btnLoadCodeGraph) btnLoadCodeGraph.addEventListener('click', () => loadCodeGraph());
        if (cgKindSelect) cgKindSelect.addEventListener('change', () => loadCodeGraph());
        if (btnGraphZoomIn) {
            btnGraphZoomIn.addEventListener('click', () => {
                if (!cgNetwork) return;
                cgNetwork.moveTo({
                    scale: cgNetwork.getScale() * 1.25,
                    animation: { duration: 200, easingFunction: 'easeInOutQuad' }
                });
            });
        }
        if (btnGraphZoomOut) {
            btnGraphZoomOut.addEventListener('click', () => {
                if (!cgNetwork) return;
                cgNetwork.moveTo({
                    scale: cgNetwork.getScale() * 0.8,
                    animation: { duration: 200, easingFunction: 'easeInOutQuad' }
                });
            });
        }
        if (btnGraphFit) {
            btnGraphFit.addEventListener('click', resizeAndFitCodeGraph);
        }
        if (btnGraphPhysics) {
            btnGraphPhysics.addEventListener('click', () => {
                cgPhysicsEnabled = !cgPhysicsEnabled;
                syncGraphPhysicsButton();
                if (cgNetwork) {
                    cgNetwork.setOptions({ physics: { enabled: cgPhysicsEnabled } });
                }
            });
        }
        if (btnGraphFullscreen) {
            btnGraphFullscreen.addEventListener('click', toggleGraphFullscreen);
        }
        if (btnCloseNodeDetails && nodeDetailsPanel) {
            btnCloseNodeDetails.addEventListener('click', () => {
                nodeDetailsPanel.style.display = 'none';
                if (cgNetwork) cgNetwork.unselectAll();
            });
        }
        if (cgSearchInput) {
            cgSearchInput.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') loadCodeGraph();
            });
        }
        if (cgProjectSelect) {
            cgProjectSelect.addEventListener('change', (e) => {
                const val = e.target.value.trim();
                activeUser = val || 'default';
                if (currentProjectSelect) {
                    const colonIdx = val.indexOf(':');
                    const base = colonIdx >= 0 ? val.slice(0, colonIdx) : (val || 'default');
                    const br = colonIdx >= 0 ? val.slice(colonIdx + 1) : '';
                    if (Array.from(currentProjectSelect.options).some(o => o.value === base)) {
                        currentProjectSelect.value = base;
                        localStorage.setItem('activeProject', base);
                        updateBranchDropdown(base);
                        if (br && currentBranchSelect && Array.from(currentBranchSelect.options).some(o => o.value === br)) {
                            currentBranchSelect.value = br;
                            localStorage.setItem(`branch_${base}`, br);
                        }
                    }
                }
                recalculateActiveUser();
                syncCurrentRouteUrl({ replace: true });
                loadStats();
                loadMemories();
                loadCodeGraph({ skipUrlSync: true });
            });
        }

        // Unified KB Listeners
        if (btnRunBuildKb) btnRunBuildKb.addEventListener('click', buildCompleteKb);
        if (btnRefreshKbCoverage) btnRefreshKbCoverage.addEventListener('click', loadKbCoverage);
        if (btnRunKbTrace) btnRunKbTrace.addEventListener('click', runKbTrace);
        if (kbTraceTarget) {
            kbTraceTarget.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') runKbTrace();
            });
        }

        // Plugins & Hardware Listeners
        if (btnRefreshHardware) btnRefreshHardware.addEventListener('click', async () => {
            await loadHardwareStatus();
            showToast('Hardware diagnostics updated.', 'success', 2500);
        });
        if (btnReloadPlugins) btnReloadPlugins.addEventListener('click', reloadPlugins);
        if (pluginSearchInput) pluginSearchInput.addEventListener('input', renderPluginsGrid);
        document.querySelectorAll('.plugin-filter-pill').forEach(pill => {
            pill.addEventListener('click', () => {
                document.querySelectorAll('.plugin-filter-pill').forEach(p => p.classList.remove('active'));
                pill.classList.add('active');
                activePluginCategory = pill.getAttribute('data-category') || 'all';
                renderPluginsGrid();
            });
        });

        // v3.0 Testing Layer 1 Listeners
        if (btnRunV3Plan) btnRunV3Plan.addEventListener('click', runV3TestPlan);
        if (btnLookupV3Cache) btnLookupV3Cache.addEventListener('click', lookupV3ScriptCache);
        if (btnQueryV3Evidence) btnQueryV3Evidence.addEventListener('click', queryV3EvidenceWindow);
        if (btnRunV3Rca) btnRunV3Rca.addEventListener('click', runV3RcaTriage);

        // Profile & Security Listeners
        if (profileForm) profileForm.addEventListener('submit', saveMyProfile);
        if (changePasswordForm) changePasswordForm.addEventListener('submit', submitChangePassword);
        if (createApiKeyForm) createApiKeyForm.addEventListener('submit', submitCreateApiKey);
        if (btnRevokeOtherSessions) btnRevokeOtherSessions.addEventListener('click', revokeOtherSessions);

        // Auth Admin Listeners
        if (authPolicyForm) authPolicyForm.addEventListener('submit', saveAuthSettings);
        if (authAccountForm) authAccountForm.addEventListener('submit', saveAccount);
        const allowedToolsInput = document.getElementById('authAllowedTools');
        if (allowedToolsInput) {
            allowedToolsInput.addEventListener('input', updateActiveToolTags);
        }
        if (btnRefreshAuditLogs) btnRefreshAuditLogs.addEventListener('click', loadAuditLogs);
        if (auditLogEventFilter) {
            auditLogEventFilter.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') loadAuditLogs();
            });
        }
        if (auditLogUserFilter) {
            auditLogUserFilter.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') loadAuditLogs();
            });
        }

        // Project Registry & Storage Topology Listeners
        const projectRegForm = document.getElementById('projectRegistryForm');
        if (projectRegForm) {
            projectRegForm.addEventListener('submit', saveProject);
        }
        if (regProjectIdInput) {
            regProjectIdInput.addEventListener('input', updateRegDbPreview);
            updateRegDbPreview();
        }
        if (btnRefreshTopology) {
            btnRefreshTopology.addEventListener('click', async () => {
                btnRefreshTopology.disabled = true;
                try {
                    await loadProjectsList();
                    await loadProjects();
                    showToast('Storage topology refreshed.', 'success', 2500);
                } finally {
                    btnRefreshTopology.disabled = false;
                }
            });
        }
    }

    // =========================================================================
    // 11. Overview Stats & Hardware Telemetry
    // =========================================================================
    async function loadStats() {
        try {
            const res = await fetch(`/memory/stats?user_id=${encodeURIComponent(activeUser)}`);
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'Failed to load stats'));
            const data = await res.json();

            if (backendStatus) backendStatus.textContent = 'Connected';
            if (totalMemories) totalMemories.textContent = data.total_memories || 0;
            if (totalFacts) totalFacts.textContent = data.total_temporal_facts || 0;

            if (sectorCounts) {
                sectorCounts.innerHTML = '';
                const rawSectors = data.sectors;
                const sectorEntries = Array.isArray(rawSectors)
                    ? rawSectors.map(item => {
                        if (item && typeof item === 'object') {
                            return [
                                toDisplayText(item.sector || item.name || item.primary_sector || 'unknown'),
                                item.count ?? item.total ?? 0
                            ];
                        }
                        return [toDisplayText(item, 'unknown'), 0];
                    })
                    : Object.entries(rawSectors || {}).map(([secName, count]) => [
                        secName,
                        (count && typeof count === 'object') ? (count.count ?? count.total ?? toDisplayText(count, '0')) : count
                    ]);

                if (sectorEntries.length === 0) {
                    sectorCounts.innerHTML = '<div class="sector-item placeholder-item">No memories stored</div>';
                } else {
                    for (const [secName, count] of sectorEntries) {
                        const secDiv = document.createElement('div');
                        secDiv.className = `sector-item ${escapeHTML(secName)}`;
                        secDiv.innerHTML = `
                            <span class="sector-name">${escapeHTML(secName)}</span>
                            <span class="sector-count mono-num">${escapeHTML(count)}</span>
                        `;
                        sectorCounts.appendChild(secDiv);
                    }
                }
            }

            if (activeTags) {
                activeTags.innerHTML = '';
                const tags = Array.isArray(data.tags) ? data.tags : [];
                if (tags.length === 0) {
                    activeTags.innerHTML = '<span class="placeholder-item">No tags</span>';
                } else {
                    tags.forEach(tag => {
                        const tagName = (tag && typeof tag === 'object')
                            ? toDisplayText(tag.tag || tag.name || tag.label || tag)
                            : toDisplayText(tag);
                        if (!tagName) return;
                        const tagCount = (tag && typeof tag === 'object' && tag.count !== undefined) ? ` (${tag.count})` : '';
                        const tagSpan = document.createElement('button');
                        tagSpan.type = 'button';
                        tagSpan.className = 'tag';
                        tagSpan.textContent = `${tagName}${tagCount}`;
                        tagSpan.addEventListener('click', () => {
                            if (searchQuery) searchQuery.value = `tag:${tagName}`;
                            navigateTo(buildContextualHref('/app/workspace/search', {
                                extraParams: { q: `tag:${tagName}`, mode: 'rag' }
                            }));
                        });
                        activeTags.appendChild(tagSpan);
                    });
                }
            }
        } catch (err) {
            console.error('Stats loading error:', err);
        }
    }

    async function loadPluginSummary() {
        try {
            const { baseProject } = getProjectContext();
            const res = await fetch(`/plugins?project_id=${encodeURIComponent(baseProject)}&include_disabled=true`);
            if (!res.ok) return;
            const data = await res.json();
            const total = data.total_plugins ?? (data.plugins ? data.plugins.length : 0);
            const enabled = data.enabled_plugins ?? total;
            if (statActivePlugins) {
                statActivePlugins.textContent = `${enabled}/${total}`;
            }
            const navBadge = document.querySelector('#btnTabPlugins .nav-badge');
            if (navBadge) navBadge.textContent = String(total);
        } catch (err) {
            console.warn('Plugin summary fetch skipped:', err);
        }
    }

    function extractDeviceLabel(val) {
        if (val === null || val === undefined) return 'CPU';
        const raw = typeof val === 'object'
            ? (val.backend || val.device_str || val.device || val.engine || val.provider || 'cpu')
            : val;
        const key = String(raw || 'cpu').trim();
        const lower = key.toLowerCase();
        const labelMap = {
            numpy_blas_cpu: 'NumPy BLAS (CPU)',
            torch_mps: 'Apple Metal MPS',
            torch_cuda: 'PyTorch CUDA',
            cugraph_cuda: 'NVIDIA cuGraph',
            mps: 'Apple Metal MPS',
            coreml: 'Apple CoreML',
            cuda: 'CUDA',
            gpu: 'GPU',
            cpu: 'CPU',
            auto: 'Auto'
        };
        if (labelMap[lower]) return labelMap[lower];
        if (lower.startsWith('cuda:')) return `CUDA (${lower.slice(5)})`;
        if (lower.startsWith('gpu:')) return `GPU (${lower.slice(4)})`;
        return key;
    }

    async function loadHardwareStatus() {
        try {
            const res = await fetch('/graphify/hardware');
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'Failed to inspect hardware'));
            const data = await res.json();
            lastHardwareStatus = data;

            const ocr = data.ocr || {};
            const graphify = data.graphify || {};
            const ocrHw = ocr.hardware || {};
            const gfHw = graphify.hardware || {};

            const osLabel = [gfHw.os || ocrHw.os, gfHw.arch || ocrHw.arch].filter(Boolean).join('/');
            const cpuCores = gfHw.cpu_count || ocrHw.cpu_count || '?';
            const accelParts = [];
            if (gfHw.cuda_available || ocrHw.cuda_available) {
                accelParts.push(`CUDA (${gfHw.cuda_device_count || 1})`);
            }
            if (gfHw.mps_available || ocrHw.mps_available || gfHw.apple_silicon) {
                accelParts.push('Apple MPS');
            }
            if (accelParts.length === 0) accelParts.push('CPU Only');

            const ocrDevLabel = extractDeviceLabel(ocr.resolved_device || ocr.config?.ocr_device || 'cpu');
            const gfBackendLabel = extractDeviceLabel(graphify.resolved_backend || graphify.config?.graphify_device || 'cpu');
            const endpoints = ocr.local_ai_endpoints || ocr.local_ai || {};
            const localAiParts = [];
            if (endpoints.ollama?.reachable || graphify.local_ai?.ollama_url) {
                localAiParts.push(endpoints.ollama?.reachable ? 'Ollama: Ready' : 'Ollama: Offline');
            }
            if (endpoints.openai_compatible?.reachable || graphify.local_ai?.local_ai_base_url) {
                localAiParts.push(endpoints.openai_compatible?.reachable ? 'Local LLM: Ready' : 'Local LLM: Configured');
            }
            if (localAiParts.length === 0) localAiParts.push('None detected');

            if (statComputeBackend) statComputeBackend.textContent = gfBackendLabel;
            if (headerHardwareBadge) {
                headerHardwareBadge.innerHTML = `<i class="fa-solid fa-microchip" aria-hidden="true"></i><span>${escapeHTML(gfBackendLabel)} &middot; ${escapeHTML(accelParts.join(' / '))}</span>`;
            }
            if (hwDashOcrDevice) {
                hwDashOcrDevice.textContent = `${ocrDevLabel} (${toDisplayText(ocr.config?.ocr_engine, 'auto')})`;
            }
            if (hwDashGraphBackend) {
                hwDashGraphBackend.textContent = `${gfBackendLabel} (${osLabel || 'host'}, ${cpuCores} cores)`;
            }
            if (hwDashAccelerators) {
                const gpuNames = Array.isArray(gfHw.gpu_names) ? gfHw.gpu_names.map(g => toDisplayText(g)).filter(Boolean) : [];
                const gpuList = gpuNames.length ? ` — ${gpuNames.join(', ')}` : '';
                hwDashAccelerators.textContent = `${accelParts.join(', ')}${gpuList}`;
            }
            if (hwDashLocalAi) {
                hwDashLocalAi.textContent = localAiParts.join(' | ');
            }

            renderHardwareDetailGrid(data);
        } catch (err) {
            console.warn('Hardware telemetry error:', err);
            if (hardwareDetailGrid) {
                hardwareDetailGrid.innerHTML = `<div class="placeholder-item">Unable to probe hardware: ${escapeHTML(err.message)}</div>`;
            }
        }
    }

    function renderHardwareDetailGrid(data) {
        if (!hardwareDetailGrid || !data) return;
        const ocr = data.ocr || {};
        const graphify = data.graphify || {};
        const gfHw = graphify.hardware || {};
        const ocrLibs = ocr.ocr_libraries || {};
        const gfLibs = graphify.libraries || {};
        const endpoints = ocr.local_ai_endpoints || ocr.local_ai || {};

        const availableOcrLibs = Object.entries(ocrLibs).filter(([, v]) => v).map(([k]) => k);
        const availableGfLibs = Object.entries(gfLibs)
            .filter(([k, v]) => typeof v === 'boolean' && v)
            .map(([k]) => k);

        const ocrDevLabel = extractDeviceLabel(ocr.resolved_device || ocr.config?.ocr_device || 'auto');
        const gfBackendLabel = extractDeviceLabel(graphify.resolved_backend || graphify.config?.graphify_device || 'cpu');
        const gpuNames = Array.isArray(gfHw.gpu_names) ? gfHw.gpu_names.map(g => toDisplayText(g)).filter(Boolean) : [];

        const cards = [
            { k: 'Host OS & Arch', v: `${toDisplayText(gfHw.os, 'unknown')} / ${toDisplayText(gfHw.arch, 'unknown')} (${gfHw.cpu_count || '?'} CPU cores)` },
            { k: 'GPU / Accelerators', v: `CUDA: ${gfHw.cuda_available ? `Yes (${gfHw.cuda_device_count})` : 'No'} · MPS: ${gfHw.mps_available ? 'Yes' : 'No'}${gpuNames.length ? ` · ${gpuNames.join(', ')}` : ''}` },
            { k: 'OCR Engine & Device', v: `Device: ${ocrDevLabel} · Engine: ${toDisplayText(ocr.config?.ocr_engine, 'auto')} · Precision: ${toDisplayText(ocr.config?.ocr_precision, 'fp32')}` },
            { k: 'OCR Libraries', v: availableOcrLibs.length ? availableOcrLibs.join(', ') : 'None installed' },
            { k: 'Graphify Compute Backend', v: `${gfBackendLabel} (Workers: ${graphify.config?.graphify_workers ?? 4}, Semantic: ${toDisplayText(graphify.config?.graphify_semantic_engine, 'auto')})` },
            { k: 'Graph & ML Libraries', v: availableGfLibs.length ? availableGfLibs.join(', ') : 'NumPy baseline' },
            { k: 'Local AI (Ollama)', v: `${endpoints.ollama?.reachable ? 'Reachable' : 'Offline'} (${toDisplayText(graphify.local_ai?.ollama_url || ocr.config?.ollama_url || endpoints.ollama?.base_url, 'http://localhost:11434')})` },
            { k: 'OpenAI-Compatible Local AI', v: graphify.local_ai?.local_ai_base_url ? `${endpoints.openai_compatible?.reachable ? 'Reachable' : 'Unreachable'} (${toDisplayText(graphify.local_ai.local_ai_base_url)})` : 'Not configured' }
        ];

        hardwareDetailGrid.innerHTML = cards.map(c => `
            <div class="telemetry-kv">
                <span class="telemetry-k">${escapeHTML(c.k)}</span>
                <span class="telemetry-v mono-num">${escapeHTML(c.v)}</span>
            </div>
        `).join('');
    }

    // =========================================================================
    // 12. Memories & Versioned Documents
    // =========================================================================
    async function loadMemories() {
        if (!memoriesFeed) return;
        memoriesFeed.innerHTML = '<div class="loader"><i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i> Loading memories...</div>';
        try {
            const res = await fetch(`/memory/history?user_id=${encodeURIComponent(activeUser)}&limit=30`);
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'Failed to load history'));
            const data = await res.json();
            loadedMemories = data.history || [];
            renderFilteredMemories();
        } catch (err) {
            memoriesFeed.innerHTML = `<div class="error-msg" style="display:block;">Error loading memories: ${escapeHTML(err.message)}</div>`;
        }
    }

    function renderFilteredMemories() {
        if (!memoriesFeed) return;
        const filterTerm = (memoryFilterInput?.value || '').trim().toLowerCase();
        const filtered = !filterTerm
            ? loadedMemories
            : loadedMemories.filter(node => {
                const content = String(node.content || '').toLowerCase();
                const sector = String(node.primary_sector || '').toLowerCase();
                const tags = String(node.tags || '').toLowerCase();
                return content.includes(filterTerm) || sector.includes(filterTerm) || tags.includes(filterTerm);
            });

        memoriesFeed.innerHTML = '';
        if (filtered.length === 0) {
            memoriesFeed.innerHTML = `
                <div class="empty-state">
                    <i class="fa-solid fa-folder-open" aria-hidden="true"></i>
                    <p>${filterTerm ? 'No memories match the active filter.' : 'No cognitive memories found for this project context.'}</p>
                </div>`;
            return;
        }

        filtered.forEach(node => {
            const nodeCard = document.createElement('div');
            nodeCard.className = `memory-node-card card ${escapeHTML(node.primary_sector || '')}`;

            const dateStr = new Date(node.created_at || Date.now()).toLocaleString();
            let tagsList = [];
            try {
                tagsList = Array.isArray(node.tags) ? node.tags : JSON.parse(node.tags || '[]');
            } catch (_) {
                tagsList = [];
            }

            let tagsHtml = '';
            if (tagsList && tagsList.length > 0) {
                tagsHtml = `<div class="memory-tags-row">${tagsList.map(t => `<span class="tag">${escapeHTML(t)}</span>`).join('')}</div>`;
            }

            const saliencePercent = Math.min(100, Math.max(0, (node.salience || 0.5) * 100));
            const actionButtonsHtml = userRole !== 'reader'
                ? `<div class="action-buttons-group">
                        <button type="button" class="btn btn-secondary btn-sm btn-reinforce" data-id="${escapeHTML(node.id)}"><i class="fa-solid fa-bolt" aria-hidden="true"></i> Reinforce</button>
                        <button type="button" class="btn btn-danger btn-sm btn-delete" data-id="${escapeHTML(node.id)}"><i class="fa-solid fa-trash" aria-hidden="true"></i> Delete</button>
                   </div>`
                : '';

            nodeCard.innerHTML = `
                <div class="memory-node-header">
                    <span class="memory-sector-tag">${escapeHTML(node.primary_sector || 'semantic')}</span>
                    <div class="memory-meta">
                        <span><i class="fa-regular fa-clock" aria-hidden="true"></i> ${escapeHTML(dateStr)}</span>
                        <span>ID: ${escapeHTML(String(node.id || '').substring(0, 8))}...</span>
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
                    ${actionButtonsHtml}
                </div>
            `;

            if (userRole !== 'reader') {
                const reinforceBtn = nodeCard.querySelector('.btn-reinforce');
                const deleteBtn = nodeCard.querySelector('.btn-delete');
                if (reinforceBtn) reinforceBtn.addEventListener('click', () => reinforceMemory(node.id));
                if (deleteBtn) deleteBtn.addEventListener('click', () => deleteMemory(node.id));
            }

            memoriesFeed.appendChild(nodeCard);
        });
    }

    async function saveMemory() {
        if (activeUser.includes('%')) {
            showToast("Cannot store memories when 'All Branches' is selected. Please select a specific branch.", 'warning');
            return;
        }
        const content = (newMemoryContent?.value || '').trim();
        if (!content) {
            showToast('Memory content cannot be empty.', 'warning');
            return;
        }

        const tags = (newMemoryTags?.value || '').split(',').map(t => t.trim()).filter(Boolean);

        if (btnSaveMemory) {
            btnSaveMemory.disabled = true;
            btnSaveMemory.innerHTML = '<i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i> Storing...';
        }

        try {
            const res = await fetch('/memory/add', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    content,
                    user_id: activeUser,
                    tags,
                    metadata: { source: 'dashboard' },
                    author: (currentUser?.value || '').trim()
                })
            });
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'Failed to store memory.'));

            if (newMemoryContent) newMemoryContent.value = '';
            if (newMemoryTags) newMemoryTags.value = '';

            showToast('Cognitive memory stored.', 'success');
            loadStats();
            await loadMemories();
        } catch (err) {
            showToast(err.message, 'error');
        } finally {
            if (btnSaveMemory) {
                btnSaveMemory.disabled = false;
                btnSaveMemory.innerHTML = '<i class="fa-solid fa-floppy-disk" aria-hidden="true"></i> Store Memory';
            }
        }
    }

    async function deleteMemory(id) {
        const ok = await confirmAction({
            title: 'Delete Memory Node',
            message: 'Are you sure you want to permanently delete this memory node?',
            confirmLabel: 'Delete Memory',
            danger: true
        });
        if (!ok) return;

        try {
            const res = await fetch(`/memory/${encodeURIComponent(id)}`, { method: 'DELETE' });
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'Failed to delete memory.'));
            showToast('Memory deleted.', 'success');
            loadStats();
            await loadMemories();
        } catch (err) {
            showToast(err.message, 'error');
        }
    }

    async function reinforceMemory(id) {
        try {
            const res = await fetch(`/memory/${encodeURIComponent(id)}/reinforce`, { method: 'POST' });
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'Failed to reinforce memory.'));
            showToast('Memory salience reinforced.', 'success', 2500);
            await loadMemories();
        } catch (err) {
            showToast(err.message, 'error');
        }
    }

    async function deleteAllMemories() {
        const ok = await confirmAction({
            title: 'Clear All Project Memories',
            message: `WARNING: This will permanently remove all cognitive memories, temporal facts, and index entries for '${activeUser}'.`,
            confirmLabel: 'Clear All Memories',
            danger: true
        });
        if (!ok) return;

        try {
            const res = await fetch('/memory/delete_all', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user_id: activeUser })
            });
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'Failed to delete memories.'));
            showToast(`All memories cleared for ${activeUser}.`, 'success');
            loadStats();
            await loadMemories();
        } catch (err) {
            showToast(err.message, 'error');
        }
    }

    function handleFileSelect() {
        if (!docUploadFile || !uploadStatusText) return;
        if (docUploadFile.files.length > 0) {
            const file = docUploadFile.files[0];
            uploadStatusText.innerHTML = `<strong>Selected:</strong> ${escapeHTML(file.name)} (${(file.size / 1024).toFixed(1)} KB)`;
        } else {
            uploadStatusText.textContent = 'Click to select PDF, DOCX, XLSX, PPTX, CSV, MD, or Image';
        }
    }

    async function uploadDocument() {
        if (!docUploadFile || docUploadFile.files.length === 0) {
            showToast('Please select a file to upload first.', 'warning');
            return;
        }
        if (activeUser.includes('%')) {
            showToast("Cannot upload documents when 'All Branches' is selected. Please select a specific branch.", 'warning');
            return;
        }

        const file = docUploadFile.files[0];
        const tags = (docUploadTags?.value || '').trim();
        const { baseProject, branch } = getProjectContext();

        if (btnUploadDoc) {
            btnUploadDoc.disabled = true;
            btnUploadDoc.innerHTML = '<i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i> Uploading...';
        }

        const formData = new FormData();
        formData.append('file', file);
        formData.append('project_id', baseProject);
        formData.append('branch', branch);
        formData.append('tags', tags);
        formData.append('author', (currentUser?.value || '').trim());
        formData.append('ocr_engine', docOcrEngine ? docOcrEngine.value : 'auto');
        formData.append('ocr_device', docOcrDevice ? docOcrDevice.value : 'auto');

        try {
            const res = await fetch('/sources/upload', {
                method: 'POST',
                body: formData
            });
            if (!res.ok) {
                throw new Error(await extractErrorMessage(res, 'Upload failed.'));
            }
            const data = await res.json();
            showToast(
                `Parsed '${file.name}' (${data.doc_version || 'v1'}: ${data.new_units ?? 0} new, ${data.reused_units ?? 0} reused units).`,
                'success'
            );

            docUploadFile.value = '';
            if (docUploadTags) docUploadTags.value = '';
            handleFileSelect();

            loadStats();
            loadMemories();
            loadIngestedDocuments();
        } catch (err) {
            showToast('Upload error: ' + err.message, 'error');
        } finally {
            if (btnUploadDoc) {
                btnUploadDoc.disabled = false;
                btnUploadDoc.innerHTML = '<i class="fa-solid fa-upload" aria-hidden="true"></i> Upload &amp; Parse';
            }
        }
    }

    async function loadIngestedDocuments() {
        if (!ingestedDocsList) return;
        const { baseProject, rawBranch } = getProjectContext();
        ingestedDocsList.innerHTML = '<div class="loader"><i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i> Loading documents...</div>';

        try {
            const params = new URLSearchParams({ project_id: baseProject, status: 'all' });
            if (rawBranch && rawBranch !== '%') {
                params.set('branch', rawBranch);
            }
            const res = await fetch(`/sources/documents?${params.toString()}`);
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'Failed to load ingested documents'));
            const data = await res.json();
            const docs = data.documents || [];

            ingestedDocsList.innerHTML = '';
            if (docs.length === 0) {
                ingestedDocsList.innerHTML = '<div class="placeholder-item">No versioned documents ingested for this project yet.</div>';
                return;
            }

            docs.forEach(doc => {
                const row = document.createElement('div');
                row.className = 'account-item';
                row.style.flexDirection = 'column';
                row.style.alignItems = 'stretch';

                const ts = formatEpochTimestamp(doc.created_at, 'N/A');
                const unitCount = Array.isArray(doc.units) ? doc.units.length : (doc.total_units ?? doc.units ?? 0);
                const metaSummary = (doc.metadata && typeof doc.metadata === 'object' && Object.keys(doc.metadata).length > 0)
                    ? `<span>Meta: ${escapeHTML(formatDetailsSummary(doc.metadata))}</span>`
                    : '';
                row.innerHTML = `
                    <div class="card-header-action" style="margin-bottom: 0.25rem;">
                        <div class="account-title-row">
                            <span class="account-username code-font">${escapeHTML(doc.filename || doc.doc_key)}</span>
                            <span class="status-tag info">${escapeHTML(doc.version || 'v1')}</span>
                            <span class="status-tag ${doc.status === 'active' ? 'success' : 'warning'}">${escapeHTML(doc.status || 'active')}</span>
                            <span class="nav-badge mono-num">${escapeHTML(doc.branch || 'main')}</span>
                        </div>
                        <button type="button" class="btn btn-secondary btn-sm btn-doc-versions">
                            <i class="fa-solid fa-clock-rotate-left" aria-hidden="true"></i> Version History
                        </button>
                    </div>
                    <div class="memory-meta">
                        <span>Key: ${escapeHTML(doc.doc_key)}</span>
                        <span>Units: ${escapeHTML(unitCount)} (${escapeHTML(doc.reused_units ?? 0)} reused, ${escapeHTML(doc.new_units ?? 0)} new)</span>
                        <span>Updated: ${escapeHTML(ts)}</span>
                        ${metaSummary}
                    </div>
                    <div class="doc-versions-drawer" hidden style="margin-top: 0.5rem;"></div>
                `;

                const verBtn = row.querySelector('.btn-doc-versions');
                const drawer = row.querySelector('.doc-versions-drawer');
                if (verBtn && drawer) {
                    verBtn.addEventListener('click', async () => {
                        if (!drawer.hidden) {
                            drawer.hidden = true;
                            return;
                        }
                        drawer.hidden = false;
                        drawer.innerHTML = '<div class="placeholder-item">Loading version history...</div>';
                        try {
                            const vParams = new URLSearchParams({ project_id: baseProject });
                            if (doc.branch) vParams.set('branch', doc.branch);
                            const vRes = await fetch(`/sources/documents/${encodeURIComponent(doc.doc_key)}/versions?${vParams.toString()}`);
                            if (!vRes.ok) throw new Error(await extractErrorMessage(vRes, 'Failed to load versions'));
                            const vData = await vRes.json();
                            const versions = vData.versions || [];
                            if (versions.length === 0) {
                                drawer.innerHTML = '<div class="placeholder-item">No version rows found.</div>';
                                return;
                            }
                            drawer.innerHTML = versions.map(v => {
                                const vTime = formatEpochTimestamp(v.created_at, 'N/A');
                                const changedSummary = Array.isArray(v.changed_units) && v.changed_units.length > 0
                                    ? ` · Changed: ${escapeHTML(toDisplayText(v.changed_units))}`
                                    : '';
                                return `
                                    <div class="telemetry-kv" style="margin-top: 0.35rem;">
                                        <span class="telemetry-k">${escapeHTML(v.version)} · ${escapeHTML(v.status || '')} · ${escapeHTML(vTime)}</span>
                                        <span class="telemetry-v mono-num">SHA: ${escapeHTML(String(v.file_sha256 || '').slice(0, 16))}... · Total: ${escapeHTML(v.total_units ?? 0)} · Reused: ${escapeHTML(v.reused_units ?? 0)} · New: ${escapeHTML(v.new_units ?? 0)} · Deleted: ${escapeHTML(v.deleted_units ?? 0)}${changedSummary}</span>
                                    </div>
                                `;
                            }).join('');
                        } catch (err) {
                            drawer.innerHTML = `<div class="error-msg" style="display:block;">${escapeHTML(err.message)}</div>`;
                        }
                    });
                }

                ingestedDocsList.appendChild(row);
            });
        } catch (err) {
            ingestedDocsList.innerHTML = `<div class="error-msg" style="display:block;">Failed to load documents: ${escapeHTML(err.message)}</div>`;
        }
    }

    // =========================================================================
    // 12B. AI & LLM Provider Settings Modal
    // =========================================================================
    function getShortProviderLabel(provider) {
        const key = String(provider || '').toLowerCase();
        if (key === 'local_rag') return 'Local RAG';
        if (key === 'gemini') return 'Gemini';
        if (key === 'openai') return 'OpenAI';
        if (key === 'ollama') return 'Ollama';
        if (key === 'minimax') return 'MiniMax';
        if (key === 'local_ai') return 'Local AI';
        return toDisplayText(provider, 'Auto');
    }

    function updateAiEngineBadges(settings) {
        if (!settings) return;
        const resolved = settings.resolved_provider || 'local_rag';
        const shortResolved = getShortProviderLabel(resolved);
        const mode = settings.provider || 'auto';
        const fullLabel = toDisplayText(settings.resolved_label, shortResolved);
        const searchText = mode === 'auto' ? `Engine: Auto (${shortResolved})` : `Engine: ${shortResolved}`;
        const copilotText = mode === 'auto' ? `Engine: Auto (${shortResolved})` : `Engine: ${shortResolved}`;
        const tagClass = resolved === 'local_rag' ? 'status-tag info' : 'status-tag success';

        if (searchActiveAiBadge) {
            searchActiveAiBadge.textContent = searchText;
            searchActiveAiBadge.className = tagClass;
            searchActiveAiBadge.title = fullLabel;
        }
        if (copilotActiveAiBadge) {
            copilotActiveAiBadge.textContent = copilotText;
            copilotActiveAiBadge.className = tagClass;
            copilotActiveAiBadge.title = fullLabel;
        }
    }

    function renderAiStatusSummaryHtml(data) {
        const resolvedProvider = data.resolved_provider || 'local_rag';
        const resolvedLabel = toDisplayText(data.resolved_label, getShortProviderLabel(resolvedProvider));
        const engineTagClass = resolvedProvider === 'local_rag' ? 'info' : 'success';

        const geminiText = data.has_gemini_key
            ? `Gemini: ${toDisplayText(data.gemini_key_masked, 'Configured')}`
            : 'Gemini: No key';
        const openaiText = data.has_openai_key
            ? `OpenAI: ${toDisplayText(data.openai_key_masked, 'Configured')}`
            : 'OpenAI: No key';

        const ollamaModels = Array.isArray(data.ollama_models)
            ? data.ollama_models.map(m => toDisplayText(m)).filter(Boolean)
            : [];
        const ollamaText = data.ollama_reachable
            ? `Ollama: Ready (${ollamaModels.length > 0 ? ollamaModels.slice(0, 3).join(', ') + (ollamaModels.length > 3 ? ` +${ollamaModels.length - 3}` : '') : '0 models'})`
            : 'Ollama: Offline';

        return `
            <span class="status-tag ${engineTagClass}">Resolved: ${escapeHTML(resolvedLabel)}</span>
            <span class="status-tag ${data.has_gemini_key ? 'success' : 'warning'}">${escapeHTML(geminiText)}</span>
            <span class="status-tag ${data.has_openai_key ? 'success' : 'warning'}">${escapeHTML(openaiText)}</span>
            <span class="status-tag ${data.ollama_reachable ? 'success' : 'info'}">${escapeHTML(ollamaText)}</span>
        `;
    }

    function renderAiSettingsStatusBanner(data) {
        if (!data) return;
        const summaryHtml = renderAiStatusSummaryHtml(data);
        [aiSettingsStatusBanner, aiPageStatusBanner].forEach(bannerEl => {
            if (!bannerEl) return;
            const summaryEl = bannerEl.querySelector('.ai-status-summary');
            if (summaryEl) summaryEl.innerHTML = summaryHtml;
        });
    }

    function populateAiFormByPrefix(prefix, data) {
        if (!data) return;
        const el = (suffix) => document.getElementById(`${prefix}${suffix}`);

        const providerSel = el('ProviderSelect');
        const embedSel = el('EmbedSelect');
        const fallbackChk = el('FallbackLocalRag');
        if (providerSel) providerSel.value = data.provider || 'auto';
        if (embedSel) embedSel.value = data.emb_kind || 'synthetic';
        if (fallbackChk) fallbackChk.checked = data.fallback_to_local_rag !== false;

        const geminiKey = el('GeminiKey');
        const clearGemini = el('ClearGeminiKey');
        const geminiHint = el('GeminiKeyHint');
        const geminiModel = el('GeminiModel');
        const geminiBase = el('GeminiBaseUrl');
        if (geminiKey) geminiKey.value = '';
        if (clearGemini) clearGemini.checked = false;
        if (geminiHint) {
            geminiHint.textContent = data.has_gemini_key
                ? `Saved: ${toDisplayText(data.gemini_key_masked, 'Configured')}`
                : 'Not configured';
        }
        if (geminiModel) geminiModel.value = toDisplayText(data.gemini_model, 'gemini-2.5-flash');
        if (geminiBase) geminiBase.value = toDisplayText(data.gemini_base_url, '');

        const openaiKey = el('OpenaiKey');
        const clearOpenai = el('ClearOpenaiKey');
        const openaiHint = el('OpenaiKeyHint');
        const openaiBase = el('OpenaiBaseUrl');
        const openaiModel = el('OpenaiModel');
        if (openaiKey) openaiKey.value = '';
        if (clearOpenai) clearOpenai.checked = false;
        if (openaiHint) {
            openaiHint.textContent = data.has_openai_key
                ? `Saved: ${toDisplayText(data.openai_key_masked, 'Configured')}`
                : 'Not configured';
        }
        if (openaiBase) openaiBase.value = toDisplayText(data.openai_base_url, 'https://api.openai.com/v1');
        if (openaiModel) openaiModel.value = toDisplayText(data.openai_model, 'gpt-4o-mini');

        const ollamaUrl = el('OllamaUrl');
        const ollamaModel = el('OllamaModel');
        const ollamaVision = el('OllamaVisionModel');
        if (ollamaUrl) ollamaUrl.value = toDisplayText(data.ollama_url, 'http://localhost:11434');
        if (ollamaModel) ollamaModel.value = toDisplayText(data.ollama_model, 'llama3.2');
        if (ollamaVision) ollamaVision.value = toDisplayText(data.ollama_vision_model, 'qwen2.5vl:7b');

        const localBase = el('LocalAiBaseUrl');
        const localModel = el('LocalAiModel');
        const localKey = el('LocalAiKey');
        const clearLocal = el('ClearLocalAiKey');
        const localHint = el('LocalAiKeyHint');
        if (localBase) localBase.value = toDisplayText(data.local_ai_base_url, '');
        if (localModel) localModel.value = toDisplayText(data.local_ai_model, '');
        if (localKey) localKey.value = '';
        if (clearLocal) clearLocal.checked = false;
        if (localHint) {
            localHint.textContent = data.has_local_ai_key ? 'Saved (hidden)' : 'Not configured';
        }
    }

    function populateAiSettingsForm(data) {
        if (!data) return;
        if (ollamaModelSuggestions) {
            const models = Array.isArray(data.ollama_models)
                ? data.ollama_models.map(m => toDisplayText(m)).filter(Boolean)
                : [];
            ollamaModelSuggestions.innerHTML = models
                .map(m => `<option value="${escapeHTML(m)}"></option>`)
                .join('');
        }
        populateAiFormByPrefix('ai', data);
        populateAiFormByPrefix('aiPage', data);
    }

    function collectAiSettingsPayload(prefix = 'ai') {
        const el = (suffix) => document.getElementById(`${prefix}${suffix}`);
        const providerSel = el('ProviderSelect');
        const fallbackChk = el('FallbackLocalRag');
        const embedSel = el('EmbedSelect');
        const geminiKey = el('GeminiKey');
        const clearGemini = el('ClearGeminiKey');
        const geminiModel = el('GeminiModel');
        const geminiBase = el('GeminiBaseUrl');
        const openaiKey = el('OpenaiKey');
        const clearOpenai = el('ClearOpenaiKey');
        const openaiBase = el('OpenaiBaseUrl');
        const openaiModel = el('OpenaiModel');
        const ollamaUrl = el('OllamaUrl');
        const ollamaModel = el('OllamaModel');
        const ollamaVision = el('OllamaVisionModel');
        const localBase = el('LocalAiBaseUrl');
        const localModel = el('LocalAiModel');
        const localKey = el('LocalAiKey');
        const clearLocal = el('ClearLocalAiKey');

        return {
            provider: providerSel ? providerSel.value : 'auto',
            fallback_to_local_rag: fallbackChk ? Boolean(fallbackChk.checked) : true,
            emb_kind: embedSel ? embedSel.value : 'synthetic',
            gemini_key: geminiKey ? geminiKey.value.trim() : '',
            clear_gemini_key: clearGemini ? Boolean(clearGemini.checked) : false,
            gemini_model: (geminiModel?.value || '').trim() || 'gemini-2.5-flash',
            gemini_base_url: (geminiBase?.value || '').trim(),
            openai_key: openaiKey ? openaiKey.value.trim() : '',
            clear_openai_key: clearOpenai ? Boolean(clearOpenai.checked) : false,
            openai_base_url: (openaiBase?.value || '').trim() || 'https://api.openai.com/v1',
            openai_model: (openaiModel?.value || '').trim() || 'gpt-4o-mini',
            ollama_url: (ollamaUrl?.value || '').trim() || 'http://localhost:11434',
            ollama_model: (ollamaModel?.value || '').trim() || 'llama3.2',
            ollama_vision_model: (ollamaVision?.value || '').trim() || 'qwen2.5vl:7b',
            local_ai_base_url: (localBase?.value || '').trim(),
            local_ai_model: (localModel?.value || '').trim(),
            local_ai_api_key: localKey ? localKey.value.trim() : '',
            clear_local_ai_key: clearLocal ? Boolean(clearLocal.checked) : false
        };
    }

    async function loadAiSettings({ probeOllama = false } = {}) {
        try {
            const res = await fetch(`/pm/ai-settings?probe_ollama=${probeOllama ? 'true' : 'false'}`);
            if (!res.ok) return null;
            const data = await res.json();
            currentAiSettings = data;
            updateAiEngineBadges(data);
            renderAiSettingsStatusBanner(data);
            populateAiSettingsForm(data);
            return data;
        } catch (err) {
            console.warn('AI settings fetch failed:', err);
            return null;
        }
    }

    async function openAiSettingsModal() {
        if (!aiSettingsDialog) return;
        if (aiSettingsTestOutput) {
            aiSettingsTestOutput.hidden = true;
            aiSettingsTestOutput.className = 'ai-test-banner';
            aiSettingsTestOutput.innerHTML = '';
        }
        if (!aiSettingsDialog.open) {
            aiSettingsDialog.returnValue = '';
            aiSettingsDialog.showModal();
        }
        if (aiProviderSelect) {
            requestAnimationFrame(() => aiProviderSelect.focus());
        }
        await loadAiSettings({ probeOllama: true });
    }

    async function testAiSettingsConnection(prefix = 'ai') {
        const isPage = prefix === 'aiPage';
        const outputEl = isPage ? aiPageTestOutput : aiSettingsTestOutput;
        const btnEl = isPage ? btnPageTestAi : btnTestAiSettings;
        if (!outputEl) return;

        const origHtml = btnEl ? btnEl.innerHTML : '';
        if (btnEl) {
            btnEl.disabled = true;
            btnEl.innerHTML = '<i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i> Testing...';
        }
        outputEl.hidden = false;
        outputEl.className = isPage ? 'ai-test-banner section-spacer-sm' : 'ai-test-banner';
        outputEl.innerHTML = '<i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i> Probing AI provider connection...';

        try {
            const res = await fetch('/pm/ai-settings/test', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(collectAiSettingsPayload(prefix))
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok || !data.ok) {
                const errText = toDisplayText(data?.error || data?.detail, `Test failed (HTTP ${res.status})`);
                outputEl.className = isPage ? 'ai-test-banner section-spacer-sm is-error' : 'ai-test-banner is-error';
                outputEl.innerHTML = `
                    <div class="ai-test-meta">
                        <i class="fa-solid fa-circle-xmark" aria-hidden="true"></i>
                        <span>Connection Test Failed</span>
                        ${data?.provider_used ? `<span class="status-tag danger">${escapeHTML(toDisplayText(data.provider_used))}</span>` : ''}
                    </div>
                    <div class="ai-test-reply">${escapeHTML(errText)}</div>
                `;
                return;
            }

            const providerUsed = toDisplayText(data.provider_used, 'local_rag');
            const modelUsed = toDisplayText(data.model_used, '');
            const latency = data.latency_ms !== undefined && data.latency_ms !== null ? `${round(data.latency_ms, 1)} ms` : '';
            const replyPreview = toDisplayText(data.reply, 'OK');
            outputEl.className = isPage ? 'ai-test-banner section-spacer-sm is-success' : 'ai-test-banner is-success';
            outputEl.innerHTML = `
                <div class="ai-test-meta">
                    <i class="fa-solid fa-circle-check" aria-hidden="true"></i>
                    <span>Connection Verified</span>
                    <span class="status-tag success">${escapeHTML(providerUsed)}</span>
                    ${modelUsed ? `<span class="status-tag info">${escapeHTML(modelUsed)}</span>` : ''}
                    ${latency ? `<span class="status-tag info mono-num">${escapeHTML(latency)}</span>` : ''}
                </div>
                <div class="ai-test-reply">${escapeHTML(replyPreview)}</div>
            `;
        } catch (err) {
            outputEl.className = isPage ? 'ai-test-banner section-spacer-sm is-error' : 'ai-test-banner is-error';
            outputEl.innerHTML = `
                <div class="ai-test-meta">
                    <i class="fa-solid fa-circle-xmark" aria-hidden="true"></i>
                    <span>Connection Test Failed</span>
                </div>
                <div class="ai-test-reply">${escapeHTML(toDisplayText(err?.message, 'Network error'))}</div>
            `;
        } finally {
            if (btnEl) {
                btnEl.disabled = false;
                btnEl.innerHTML = origHtml || '<i class="fa-solid fa-plug-circle-check" aria-hidden="true"></i> Test Connection';
            }
        }
    }

    async function saveAiSettings(e, prefix = 'ai') {
        e.preventDefault();
        const isPage = prefix === 'aiPage';
        const btnEl = isPage ? btnPageSaveAi : btnSaveAiSettings;
        const origHtml = btnEl ? btnEl.innerHTML : '';
        if (btnEl) {
            btnEl.disabled = true;
            btnEl.innerHTML = '<i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i> Saving...';
        }

        try {
            const res = await fetch('/pm/ai-settings', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(collectAiSettingsPayload(prefix))
            });
            if (!res.ok) {
                throw new Error(await extractErrorMessage(res, 'Failed to save AI & LLM settings.'));
            }
            const updated = await res.json();
            currentAiSettings = updated;
            updateAiEngineBadges(updated);
            renderAiSettingsStatusBanner(updated);
            populateAiSettingsForm(updated);
            if (!isPage && aiSettingsDialog?.open) {
                aiSettingsDialog.close('saved');
            }
            const label = toDisplayText(updated.resolved_label || updated.provider, 'updated');
            showToast(`AI & LLM settings saved (${label}).`, 'success');
        } catch (err) {
            showToast('Failed to save AI settings: ' + err.message, 'error');
        } finally {
            if (btnEl) {
                btnEl.disabled = false;
                btnEl.innerHTML = origHtml || (isPage
                    ? '<i class="fa-solid fa-floppy-disk" aria-hidden="true"></i> Save AI Settings'
                    : '<i class="fa-solid fa-floppy-disk" aria-hidden="true"></i> Save Settings');
            }
        }
    }

    // =========================================================================
    // 13. Semantic Search & PM Copilot
    // =========================================================================
    async function performSearch({ skipUrlSync = false } = {}) {
        const query = (searchQuery?.value || '').trim();
        if (!query || !searchResults) return;
        if (!skipUrlSync) {
            syncCurrentRouteUrl({ replace: true });
        }

        searchResults.innerHTML = '<div class="loader"><i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i> Searching ProjectBrain...</div>';
        const modeEl = document.querySelector('input[name="searchMode"]:checked');
        const mode = modeEl ? modeEl.value : 'rag';

        try {
            if (mode === 'rag') {
                const res = await fetch('/pm/search', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        query,
                        user_id: activeUser,
                        author: (currentUser?.value || '').trim()
                    })
                });
                if (!res.ok) {
                    const detail = await extractErrorMessage(res, 'Failed to query AI semantic search.');
                    throw new Error(detail);
                }
                const data = await res.json();

                searchResults.innerHTML = '';
                const providerLabel = toDisplayText(data.provider_label, 'AI RAG Synthesis');
                const modelUsed = toDisplayText(data.model_used, '');
                const fallbackReason = toDisplayText(data.fallback_reason, '').trim();

                const ragCard = document.createElement('div');
                ragCard.className = 'card rag-response-card';
                ragCard.innerHTML = `
                    <div class="rag-header">
                        <div class="rag-header-title">
                            <i class="fa-solid fa-wand-magic-sparkles" aria-hidden="true"></i>
                            <span>AI Answer</span>
                            <span class="status-tag info">${escapeHTML(providerLabel)}</span>
                            ${modelUsed ? `<span class="status-tag info">${escapeHTML(modelUsed)}</span>` : ''}
                        </div>
                        <button type="button" class="btn btn-secondary btn-sm" data-open-ai-settings="true">
                            <i class="fa-solid fa-sliders" aria-hidden="true"></i> Configure AI
                        </button>
                    </div>
                    ${fallbackReason ? `
                        <div class="ai-fallback-banner" role="status">
                            <i class="fa-solid fa-circle-info" aria-hidden="true"></i>
                            <span>Fell back cleanly to Local RAG: ${escapeHTML(fallbackReason)}</span>
                        </div>
                    ` : ''}
                    <div class="rag-body">${formatMarkdownLike(data.results || '')}</div>
                `;
                searchResults.appendChild(ragCard);

                const graphMatches = Array.isArray(data.graph_matches) ? data.graph_matches : [];
                if (graphMatches.length > 0) {
                    const graphHeader = document.createElement('h3');
                    graphHeader.className = 'subsection-heading';
                    graphHeader.innerHTML = '<i class="fa-solid fa-circle-nodes" aria-hidden="true"></i> Matching Code &amp; Graph Symbols';
                    searchResults.appendChild(graphHeader);

                    const graphGrid = document.createElement('div');
                    graphGrid.className = 'graph-matches-grid';
                    graphMatches.forEach(sym => {
                        const card = document.createElement('div');
                        card.className = 'card graph-match-card';
                        const symLabel = toDisplayText(sym?.label || sym?.qualified_name || sym?.name, 'Symbol');
                        const symKind = toDisplayText(sym?.sym_kind || sym?.file_type || sym?.kind, 'symbol');
                        const sourceFile = toDisplayText(sym?.source_file, '');
                        const sourceLoc = toDisplayText(sym?.source_location, '');
                        card.innerHTML = `
                            <div class="graph-match-header">
                                <span class="graph-match-label">${escapeHTML(symLabel)}</span>
                                <span class="status-tag info">${escapeHTML(symKind)}</span>
                            </div>
                            <div class="graph-match-meta code-font">
                                ${sourceFile ? `<span><i class="fa-regular fa-file-code" aria-hidden="true"></i> ${escapeHTML(sourceFile)}</span>` : ''}
                                ${sourceLoc ? `<span><i class="fa-solid fa-location-dot" aria-hidden="true"></i> ${escapeHTML(sourceLoc)}</span>` : ''}
                            </div>
                        `;
                        graphGrid.appendChild(card);
                    });
                    searchResults.appendChild(graphGrid);
                }

                const sourceHeader = document.createElement('h3');
                sourceHeader.className = 'subsection-heading';
                sourceHeader.innerHTML = '<i class="fa-solid fa-link" aria-hidden="true"></i> Supporting Memories';
                searchResults.appendChild(sourceHeader);

                renderMemoriesList(data.source_memories || [], searchResults);
            } else {
                const res = await fetch('/memory/search', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        query,
                        user_id: activeUser,
                        limit: 10,
                        author: (currentUser?.value || '').trim()
                    })
                });
                if (!res.ok) throw new Error(await extractErrorMessage(res, 'Failed to perform raw search.'));
                const data = await res.json();

                searchResults.innerHTML = '';
                const countHeader = document.createElement('h3');
                countHeader.style.marginBottom = '0.5rem';
                const rows = data.results || [];
                countHeader.textContent = `Found ${rows.length} matching memory nodes`;
                searchResults.appendChild(countHeader);

                renderMemoriesList(rows, searchResults);
            }
        } catch (err) {
            const msgLower = String(err.message || '').toLowerCase();
            const isAiHint = msgLower.includes('gemini') || msgLower.includes('openai') || msgLower.includes('ollama') || msgLower.includes('api_key');
            searchResults.innerHTML = `
                <div class="card danger-zone">
                    <h3><i class="fa-solid fa-circle-exclamation" aria-hidden="true"></i> Search Unavailable</h3>
                    <p class="info-text">${escapeHTML(err.message)}${isAiHint ? ' Switch to "Raw Context Retrieval" or configure your AI provider below.' : ''}</p>
                    <div class="action-buttons">
                        <button type="button" class="btn btn-secondary btn-sm" data-open-ai-settings="true">
                            <i class="fa-solid fa-sliders" aria-hidden="true"></i> Open AI &amp; LLM Settings
                        </button>
                    </div>
                </div>`;
        }
    }

    function renderMemoriesList(memories, container) {
        if (!memories || memories.length === 0) {
            const empty = document.createElement('div');
            empty.className = 'empty-state';
            empty.innerHTML = '<p>No memories found matching the query.</p>';
            container.appendChild(empty);
            return;
        }

        memories.forEach(node => {
            const nodeCard = document.createElement('div');
            nodeCard.className = `memory-node-card card ${escapeHTML(node.primary_sector || '')}`;

            const dateStr = node.created_at ? new Date(node.created_at).toLocaleString() : 'Recent';
            let tagsList = [];
            try {
                tagsList = node.tags ? (typeof node.tags === 'string' ? JSON.parse(node.tags) : node.tags) : [];
            } catch (_) {
                tagsList = [];
            }
            const tagsHtml = tagsList && tagsList.length > 0
                ? `<div class="memory-tags-row">${tagsList.map(t => `<span class="tag">${escapeHTML(t)}</span>`).join('')}</div>`
                : '';

            nodeCard.innerHTML = `
                <div class="memory-node-header">
                    <span class="memory-sector-tag">${escapeHTML(node.primary_sector || 'semantic')}</span>
                    <div class="memory-meta">
                        <span><i class="fa-regular fa-clock" aria-hidden="true"></i> ${escapeHTML(dateStr)}</span>
                        <span class="status-tag info">Score: ${round(node.score ?? node.salience, 3)}</span>
                    </div>
                </div>
                <div class="memory-content">${escapeHTML(node.content)}</div>
                ${tagsHtml}
            `;
            container.appendChild(nodeCard);
        });
    }

    async function generateDoc() {
        const prompt = (copilotPrompt?.value || '').trim();
        if (!prompt) {
            showToast('Please specify what documentation you want to generate.', 'warning');
            return;
        }

        if (btnGenerateDoc) {
            btnGenerateDoc.disabled = true;
            btnGenerateDoc.innerHTML = '<i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i> Generating Document...';
        }
        if (copilotResults) copilotResults.style.display = 'none';

        try {
            const res = await fetch('/pm/generate-doc', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    prompt,
                    doc_type: docType ? docType.value : 'status_report',
                    user_id: activeUser,
                    author: (currentUser?.value || '').trim()
                })
            });
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'Failed to generate document.'));
            const data = await res.json();

            if (copilotResults) copilotResults.style.display = 'grid';
            const docViewer = generatedDocContent?.closest('.doc-viewer');
            if (docViewer) {
                const existingFallback = docViewer.querySelector('.ai-fallback-banner');
                if (existingFallback) existingFallback.remove();
                const existingProviderBadge = docViewer.querySelector('.doc-provider-tag');
                if (existingProviderBadge) existingProviderBadge.remove();

                const providerLabel = toDisplayText(data.provider_label, '');
                const docHeader = docViewer.querySelector('.doc-header');
                if (docHeader && providerLabel) {
                    const badge = document.createElement('span');
                    badge.className = 'status-tag info doc-provider-tag';
                    badge.textContent = providerLabel;
                    docHeader.insertBefore(badge, btnCopyDoc || null);
                }

                const fallbackReason = toDisplayText(data.fallback_reason, '').trim();
                if (fallbackReason && generatedDocContent) {
                    const banner = document.createElement('div');
                    banner.className = 'ai-fallback-banner';
                    banner.setAttribute('role', 'status');
                    banner.innerHTML = `<i class="fa-solid fa-circle-info" aria-hidden="true"></i><span>Fell back cleanly to Local RAG: ${escapeHTML(fallbackReason)}</span>`;
                    docViewer.insertBefore(banner, generatedDocContent);
                }
            }
            if (generatedDocContent) generatedDocContent.innerHTML = formatMarkdownLike(data.document || '');

            if (docSourcesFeed) {
                docSourcesFeed.innerHTML = '';
                const sources = data.source_memories || [];
                if (sources.length === 0) {
                    docSourcesFeed.innerHTML = '<div class="placeholder-item">No sources used.</div>';
                } else {
                    sources.forEach(src => {
                        const srcItem = document.createElement('div');
                        srcItem.className = 'sector-item';
                        srcItem.style.marginBottom = '0.5rem';
                        srcItem.style.fontSize = '0.8rem';
                        srcItem.style.display = 'block';
                        srcItem.innerHTML = `
                            <div style="font-weight:600; font-size:0.7rem; color:var(--brand); text-transform:uppercase;">${escapeHTML(src.primary_sector || 'memory')}</div>
                            <div>${escapeHTML(String(src.content || '').substring(0, 100))}${String(src.content || '').length > 100 ? '...' : ''}</div>
                        `;
                        docSourcesFeed.appendChild(srcItem);
                    });
                }
            }
        } catch (err) {
            showToast(err.message, 'error');
        } finally {
            if (btnGenerateDoc) {
                btnGenerateDoc.disabled = false;
                btnGenerateDoc.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles" aria-hidden="true"></i> Generate Document';
            }
        }
    }

    async function copyDocToClipboard() {
        const text = generatedDocContent ? generatedDocContent.innerText : '';
        try {
            await navigator.clipboard.writeText(text);
            if (btnCopyDoc) {
                const orig = btnCopyDoc.innerHTML;
                btnCopyDoc.innerHTML = '<i class="fa-solid fa-check" aria-hidden="true"></i> Copied!';
                setTimeout(() => {
                    btnCopyDoc.innerHTML = orig;
                }, 2000);
            }
            showToast('Generated Markdown copied to clipboard.', 'success', 2500);
        } catch (err) {
            showToast('Copy failed: ' + err.message, 'error');
        }
    }

    // =========================================================================
    // 14. MCP Integration & Local Config Generator
    // =========================================================================
    function updateLocalProjectConfig() {
        const copilotConfigPre = document.getElementById('mcpCopilotConfig');
        const claudeConfigPre = document.getElementById('mcpClaudeConfig');
        const stdioLocalConfigPre = document.getElementById('mcpStdioLocalConfig');
        const stdioRemoteConfigPre = document.getElementById('mcpStdioRemoteConfig');
        const projectDisplay = document.getElementById('activeProjectDisplay');
        if (!copilotConfigPre && !claudeConfigPre && !stdioLocalConfigPre && !stdioRemoteConfigPre) return;

        const token = getStoredToken() || 'YOUR_AUTH_TOKEN';
        const project = activeUser || 'default';
        if (projectDisplay) projectDisplay.textContent = project;

        const serverOrigin = window.location.origin;
        const sseUrl = `${serverOrigin}/mcp/sse?token=${token}&project_id=${project}`;

        if (copilotConfigPre) {
            copilotConfigPre.textContent = JSON.stringify({
                servers: {
                    projectbrain: {
                        type: 'http',
                        url: sseUrl
                    }
                }
            }, null, 2);
        }

        if (claudeConfigPre) {
            claudeConfigPre.textContent = JSON.stringify({
                mcpServers: {
                    projectbrain: {
                        command: 'npx',
                        args: ['-y', 'mcp-remote', sseUrl]
                    }
                }
            }, null, 2);
        }

        if (stdioLocalConfigPre) {
            stdioLocalConfigPre.textContent = JSON.stringify({
                mcpServers: {
                    projectbrain: {
                        command: 'python3',
                        args: ['-m', 'projectbrain.main', 'mcp'],
                        env: {
                            PB_DEFAULT_USER_ID: project,
                            PB_AUTH_TOKEN: token
                        }
                    }
                }
            }, null, 2);
        }

        if (stdioRemoteConfigPre) {
            stdioRemoteConfigPre.textContent = JSON.stringify({
                mcpServers: {
                    projectbrain: {
                        command: 'python3',
                        args: ['-m', 'projectbrain.main', 'mcp'],
                        env: {
                            PB_DEFAULT_USER_ID: project,
                            PB_AUTH_TOKEN: token,
                            PB_DB_PATH: 'postgresql://username:password@remote_ip:5432/projectbrain'
                        }
                    }
                }
            }, null, 2);
        }
    }

    async function loadMcpStatus() {
        try {
            const res = await fetch('/memory/mcp/status');
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'Failed to load MCP status'));
            const data = await res.json();

            if (mcpConfigPath) mcpConfigPath.value = data.config_file_path || '';

            if (mcpFileStatus) {
                mcpFileStatus.textContent = data.config_file_exists ? 'Found' : 'Not Found';
                mcpFileStatus.className = `status-tag ${data.config_file_exists ? 'success' : 'danger'}`;
            }

            if (data.registered) {
                if (mcpRegisteredStatus) {
                    mcpRegisteredStatus.textContent = 'Registered';
                    mcpRegisteredStatus.className = 'status-tag success';
                }
                const config = data.server_config || {};
                if (mcpActiveConfig) mcpActiveConfig.textContent = JSON.stringify(config, null, 2);

                if (config.command === 'npx' || (config.args && config.args.includes('mcp-remote'))) {
                    const sseRadio = document.querySelector('input[name="mcpTransport"][value="sse"]');
                    if (sseRadio) sseRadio.checked = true;
                    if (mcpStdioConfig) mcpStdioConfig.style.display = 'none';
                    if (mcpSseConfig) mcpSseConfig.style.display = 'flex';
                    if (mcpSseUrl && config.args && config.args.length >= 3) {
                        mcpSseUrl.value = config.args[2];
                    }
                } else {
                    const stdioRadio = document.querySelector('input[name="mcpTransport"][value="stdio"]');
                    if (stdioRadio) stdioRadio.checked = true;
                    if (mcpStdioConfig) mcpStdioConfig.style.display = 'flex';
                    if (mcpSseConfig) mcpSseConfig.style.display = 'none';
                    if (config.env) {
                        if (mcpUserId) mcpUserId.value = config.env.PB_DEFAULT_USER_ID || config.env.OM_DEFAULT_USER_ID || 'default';
                        if (mcpTags) mcpTags.value = config.env.PB_DEFAULT_TAGS || config.env.OM_DEFAULT_TAGS || 'source:mcp';
                    }
                }
            } else {
                if (mcpRegisteredStatus) {
                    mcpRegisteredStatus.textContent = 'Not Registered';
                    mcpRegisteredStatus.className = 'status-tag danger';
                }
                if (mcpActiveConfig) mcpActiveConfig.textContent = '{}';
            }
        } catch (err) {
            console.warn('MCP status error:', err);
            if (mcpFileStatus) {
                mcpFileStatus.textContent = 'Unavailable';
                mcpFileStatus.className = 'status-tag warning';
            }
            if (mcpRegisteredStatus) {
                mcpRegisteredStatus.textContent = 'Unavailable';
                mcpRegisteredStatus.className = 'status-tag warning';
            }
        }
        await loadExternalMcpServers();
    }

    async function registerMcp() {
        const checkedTransport = document.querySelector('input[name="mcpTransport"]:checked');
        const transport = checkedTransport ? checkedTransport.value : 'stdio';
        const payload = {
            transport_type: transport,
            user_id: (mcpUserId?.value || '').trim() || 'default',
            tags: (mcpTags?.value || '').trim() || 'source:mcp',
            sse_url: (mcpSseUrl?.value || '').trim() || 'http://localhost:8080/mcp/sse'
        };

        if (btnRegisterMcp) {
            btnRegisterMcp.disabled = true;
            btnRegisterMcp.innerHTML = '<i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i> Registering...';
        }

        try {
            const res = await fetch('/memory/mcp/register', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'Failed to register MCP server.'));
            showToast('Applied Claude Desktop MCP configuration. Restart Claude Desktop to take effect.', 'success');
            await loadMcpStatus();
        } catch (err) {
            showToast('Registration failed: ' + err.message, 'error');
        } finally {
            if (btnRegisterMcp) {
                btnRegisterMcp.disabled = false;
                btnRegisterMcp.innerHTML = '<i class="fa-solid fa-circle-check" aria-hidden="true"></i> Install &amp; Apply Configuration';
            }
        }
    }

    async function unregisterMcp() {
        const ok = await confirmAction({
            title: 'Remove MCP Integration',
            message: 'Remove ProjectBrain integration from your local Claude Desktop configuration?',
            confirmLabel: 'Remove Integration',
            danger: true
        });
        if (!ok) return;

        if (btnUnregisterMcp) {
            btnUnregisterMcp.disabled = true;
            btnUnregisterMcp.innerHTML = '<i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i> Removing...';
        }

        try {
            const res = await fetch('/memory/mcp/unregister', { method: 'POST' });
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'Failed to remove integration.'));
            showToast('Removed ProjectBrain integration from Claude Desktop config.', 'success');
            await loadMcpStatus();
        } catch (err) {
            showToast('Removal failed: ' + err.message, 'error');
        } finally {
            if (btnUnregisterMcp) {
                btnUnregisterMcp.disabled = false;
                btnUnregisterMcp.innerHTML = '<i class="fa-solid fa-circle-xmark" aria-hidden="true"></i> Remove Integration';
            }
        }
    }

    async function loadExternalMcpServers() {
        const externalMcpList = document.getElementById('externalMcpList');
        if (!externalMcpList) return;
        try {
            const res = await fetch('/memory/mcp/external');
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'Failed to load external MCP servers'));
            const data = await res.json();
            const servers = data.servers || {};

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
                        <div class="ext-mcp-sub">Args: ${escapeHTML(argsStr)}</div>
                        ${Object.keys(s.env || {}).length > 0 ? `<div class="ext-mcp-sub">Env: ${escapeHTML(envStr)}</div>` : ''}
                    </div>
                    <button type="button" class="btn btn-danger btn-sm btn-delete-ext" data-name="${escapeHTML(name)}">
                        <i class="fa-solid fa-trash" aria-hidden="true"></i> Delete
                    </button>
                `;

                const delBtn = item.querySelector('.btn-delete-ext');
                if (delBtn) delBtn.addEventListener('click', () => deleteExternalMcp(name));
                externalMcpList.appendChild(item);
            });
        } catch (err) {
            console.warn('External MCP load skipped:', err);
        }
    }

    async function saveExternalMcp() {
        const nameEl = document.getElementById('extMcpName');
        const cmdEl = document.getElementById('extMcpCommand');
        const argsEl = document.getElementById('extMcpArgs');
        const envEl = document.getElementById('extMcpEnv');

        const name = (nameEl?.value || '').trim();
        const command = (cmdEl?.value || '').trim();
        const argsRaw = (argsEl?.value || '[]').trim();
        const envRaw = (envEl?.value || '{}').trim();

        if (!name || !command) {
            showToast('Server name and command are required.', 'warning');
            return;
        }

        let args = [];
        try {
            args = JSON.parse(argsRaw);
            if (!Array.isArray(args)) throw new Error('Must be a JSON array');
        } catch (_) {
            showToast('Arguments must be a valid JSON array, e.g. ["--arg1"].', 'warning');
            return;
        }

        let env = {};
        try {
            env = JSON.parse(envRaw);
            if (!env || typeof env !== 'object' || Array.isArray(env)) throw new Error('Must be a JSON object');
        } catch (_) {
            showToast('Environment variables must be a valid JSON object.', 'warning');
            return;
        }

        if (btnSaveExtMcp) {
            btnSaveExtMcp.disabled = true;
            btnSaveExtMcp.innerHTML = '<i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i> Saving...';
        }

        try {
            const res = await fetch('/memory/mcp/external', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, command, args, env })
            });
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'Failed to save external MCP server.'));

            if (nameEl) nameEl.value = '';
            if (cmdEl) cmdEl.value = '';
            if (argsEl) argsEl.value = '[]';
            if (envEl) envEl.value = '{}';

            showToast(`Saved external MCP server '${name}'.`, 'success');
            await loadExternalMcpServers();
        } catch (err) {
            showToast(err.message, 'error');
        } finally {
            if (btnSaveExtMcp) {
                btnSaveExtMcp.disabled = false;
                btnSaveExtMcp.innerHTML = '<i class="fa-solid fa-floppy-disk" aria-hidden="true"></i> Save MCP Server';
            }
        }
    }

    async function deleteExternalMcp(name) {
        const ok = await confirmAction({
            title: 'Delete External MCP Server',
            message: `Remove external MCP server '${name}' from configuration?`,
            confirmLabel: 'Delete Server',
            danger: true
        });
        if (!ok) return;

        try {
            const res = await fetch(`/memory/mcp/external/${encodeURIComponent(name)}`, { method: 'DELETE' });
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'Failed to delete external MCP server.'));
            showToast(`Deleted external MCP server '${name}'.`, 'success');
            await loadExternalMcpServers();
        } catch (err) {
            showToast(err.message, 'error');
        }
    }

    // =========================================================================
    // 15. Graph Explorer (Vis.js)
    // =========================================================================
    function updateGraphHudStats(nodeCount, edgeCount) {
        if (!graphHudCount) return;
        graphHudCount.textContent = `${nodeCount} nodes · ${edgeCount} edges`;
    }

    function syncGraphPhysicsButton() {
        if (!btnGraphPhysics) return;
        btnGraphPhysics.classList.toggle('is-active', cgPhysicsEnabled);
        btnGraphPhysics.setAttribute('aria-pressed', cgPhysicsEnabled ? 'true' : 'false');
    }

    function resizeAndFitCodeGraph() {
        if (!cgNetwork) return;
        cgNetwork.setSize('100%', '100%');
        cgNetwork.redraw();
        const nodeIds = cgNodesList.map(n => n.id);
        cgNetwork.fit({
            animation: { duration: 250, easingFunction: 'easeInOutQuad' },
            nodes: nodeIds.length ? nodeIds : undefined
        });
    }

    function setGraphFullscreen(active) {
        if (!graphLayout) return;
        graphLayout.classList.toggle('is-fullscreen', active);
        if (btnGraphFullscreen) {
            btnGraphFullscreen.setAttribute('aria-pressed', active ? 'true' : 'false');
            const icon = btnGraphFullscreen.querySelector('i');
            const label = btnGraphFullscreen.querySelector('span');
            if (icon) icon.className = active ? 'fa-solid fa-compress' : 'fa-solid fa-expand';
            if (label) label.textContent = active ? 'Exit' : 'Fullscreen';
        }
        requestAnimationFrame(() => {
            resizeAndFitCodeGraph();
        });
    }

    function toggleGraphFullscreen() {
        setGraphFullscreen(!graphLayout?.classList.contains('is-fullscreen'));
    }

    function setupGraphResizeObserver(networkContainer) {
        const targetEl = graphContainer || networkContainer?.parentElement;
        if (typeof ResizeObserver === 'undefined' || !targetEl) return;
        if (cgResizeObserver) cgResizeObserver.disconnect();
        cgResizeObserver = new ResizeObserver(() => {
            if (!cgNetwork) return;
            const cgTab = document.getElementById('codeGraphTab');
            const isFullscreen = graphLayout?.classList.contains('is-fullscreen');
            if (cgTab && !cgTab.classList.contains('active') && !isFullscreen) return;
            if (targetEl.clientWidth === 0 || targetEl.clientHeight === 0) return;
            cgNetwork.setSize('100%', '100%');
            cgNetwork.redraw();
            if (cgResizeTimer) clearTimeout(cgResizeTimer);
            cgResizeTimer = setTimeout(() => {
                if (!cgNetwork) return;
                const nodeIds = cgNodesList.map(n => n.id);
                cgNetwork.fit({
                    animation: { duration: 250, easingFunction: 'easeInOutQuad' },
                    nodes: nodeIds.length ? nodeIds : undefined
                });
            }, 60);
        });
        cgResizeObserver.observe(targetEl);
    }

    async function loadCodeGraph({ skipUrlSync = false } = {}) {
        if (!graphLoader || !graphEmptyState || !nodeDetailsPanel) return;
        if (!skipUrlSync) {
            syncCurrentRouteUrl({ replace: true });
        }
        graphLoader.style.display = 'flex';
        graphEmptyState.style.display = 'none';
        nodeDetailsPanel.style.display = 'none';

        const q = (cgSearchInput?.value || '').trim();
        const k = cgKindSelect ? cgKindSelect.value : 'class,function,method,file';
        const pId = cgProjectSelect ? cgProjectSelect.value : '';

        try {
            const url = `/codegraph/data?project_id=${encodeURIComponent(pId)}&query=${encodeURIComponent(q)}&kinds=${encodeURIComponent(k)}`;
            const res = await fetch(url);
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'Failed to load codegraph data.'));

            const data = await res.json();
            cgNodesList = data.nodes || [];
            cgEdgesList = data.edges || [];

            if (cgNodesList.length === 0) {
                updateGraphHudStats(0, 0);
                graphEmptyState.style.display = 'flex';
                const p = graphEmptyState.querySelector('p');
                if (p) p.textContent = 'No nodes match the filter or search. Try loading again.';
                if (cgNetwork) {
                    cgNetwork.destroy();
                    cgNetwork = null;
                }
                return;
            }

            renderCodeGraph(cgNodesList, cgEdgesList);
        } catch (err) {
            console.error('CodeGraph Error:', err);
            updateGraphHudStats(0, 0);
            graphEmptyState.style.display = 'flex';
            const p = graphEmptyState.querySelector('p');
            if (p) p.textContent = 'Error loading graph: ' + err.message;
        } finally {
            graphLoader.style.display = 'none';
        }
    }

    function renderCodeGraph(nodes, edges) {
        const networkContainer = document.getElementById('codegraphNetwork');
        if (!networkContainer || typeof vis === 'undefined') return;

        updateGraphHudStats(nodes.length, edges.length);
        syncGraphPhysicsButton();

        const isDark = getRenderedScheme() === 'dark';
        const nodeFontColor = isDark ? '#f1f5f9' : '#0f172a';
        const nodeStrokeColor = isDark ? '#0b1120' : '#ffffff';
        const nodeFontBg = isDark ? 'rgba(15, 23, 42, 0.72)' : 'rgba(255, 255, 255, 0.78)';
        const edgeColor = isDark ? 'rgba(148, 163, 184, 0.45)' : 'rgba(71, 85, 105, 0.35)';
        const edgeFontColor = isDark ? '#94a3b8' : '#475569';
        const brandAccent = isDark ? '#2dd4bf' : '#0f766e';

        const kindColors = {
            class: {
                background: isDark ? '#0f766e' : '#14b8a6',
                border: '#14b8a6',
                highlight: { background: '#14b8a6', border: '#5eead4' },
                hover: { background: '#0d9488', border: '#5eead4' }
            },
            function: {
                background: isDark ? '#0284c7' : '#38bdf8',
                border: '#38bdf8',
                highlight: { background: '#38bdf8', border: '#bae6fd' },
                hover: { background: '#0ea5e9', border: '#bae6fd' }
            },
            method: {
                background: isDark ? '#0284c7' : '#38bdf8',
                border: '#38bdf8',
                highlight: { background: '#38bdf8', border: '#bae6fd' },
                hover: { background: '#0ea5e9', border: '#bae6fd' }
            },
            file: {
                background: isDark ? '#d97706' : '#f59e0b',
                border: '#f59e0b',
                highlight: { background: '#f59e0b', border: '#fcd34d' },
                hover: { background: '#b45309', border: '#fcd34d' }
            },
            section: {
                background: isDark ? '#059669' : '#34d399',
                border: '#34d399',
                highlight: { background: '#34d399', border: '#a7f3d0' },
                hover: { background: '#10b981', border: '#a7f3d0' }
            },
            doc: {
                background: isDark ? '#059669' : '#34d399',
                border: '#34d399',
                highlight: { background: '#34d399', border: '#a7f3d0' },
                hover: { background: '#10b981', border: '#a7f3d0' }
            },
            document: {
                background: isDark ? '#059669' : '#34d399',
                border: '#34d399',
                highlight: { background: '#34d399', border: '#a7f3d0' },
                hover: { background: '#10b981', border: '#a7f3d0' }
            },
            import: { background: '#374151', border: '#9ca3af', highlight: { background: '#4b5563', border: '#d1d5db' } },
            variable: { background: '#78350f', border: '#d97706', highlight: { background: '#92400e', border: '#fbbf24' } },
            server: { background: '#581c87', border: '#a855f7', highlight: { background: '#701a75', border: '#c084fc' } },
            client: { background: '#0369a1', border: '#38bdf8', highlight: { background: '#075985', border: '#7dd3fc' } }
        };
        const defaultColor = { background: '#1f2937', border: '#64748b', highlight: { background: '#334155', border: '#94a3b8' } };

        const kindSizes = {
            class: 24,
            file: 22,
            section: 18,
            doc: 18,
            document: 18,
            function: 16,
            method: 16
        };

        const visNodes = nodes.map(n => {
            const nodeKind = (n.kind || n.group || 'code').toLowerCase();
            const nodeName = n.name || n.label || n.id;
            const colors = kindColors[nodeKind] || defaultColor;
            const isFile = nodeKind === 'file';
            return {
                id: n.id,
                label: nodeName,
                title: `${n.qualified_name || nodeName} (${nodeKind})`,
                shape: isFile ? 'box' : 'dot',
                size: kindSizes[nodeKind] || 16,
                borderWidth: 2,
                margin: isFile ? { top: 8, right: 12, bottom: 8, left: 12 } : undefined,
                color: colors,
                font: isFile
                    ? {
                        color: '#ffffff',
                        size: 13.5,
                        face: 'IBM Plex Sans',
                        strokeWidth: 2,
                        strokeColor: '#78350f'
                    }
                    : {
                        color: nodeFontColor,
                        size: 13.5,
                        face: 'IBM Plex Sans',
                        strokeWidth: 3,
                        strokeColor: nodeStrokeColor,
                        background: nodeFontBg
                    }
            };
        });

        const MAX_VIS_EDGES = 600;
        const cappedEdges = Array.isArray(edges) && edges.length > MAX_VIS_EDGES ? edges.slice(0, MAX_VIS_EDGES) : (edges || []);
        const visEdges = cappedEdges.map(e => ({
            id: e.id,
            from: e.source || e.from,
            to: e.target || e.to,
            label: e.kind || e.type || e.label || e.relation || '',
            arrows: 'to',
            color: { color: edgeColor, highlight: brandAccent, hover: '#10b981' },
            font: { size: 9, color: edgeFontColor, strokeWidth: 0, face: 'JetBrains Mono' },
            smooth: { type: 'continuous' }
        }));

        const visData = {
            nodes: new vis.DataSet(visNodes),
            edges: new vis.DataSet(visEdges)
        };

        const options = {
            autoResize: false,
            physics: {
                enabled: cgPhysicsEnabled,
                stabilization: { enabled: true, iterations: 150, updateInterval: 25 },
                barnesHut: {
                    gravitationalConstant: -5500,
                    centralGravity: 0.16,
                    springLength: 170,
                    springConstant: 0.03,
                    damping: 0.14,
                    avoidOverlap: 0.9
                }
            },
            interaction: {
                hover: true,
                tooltipDelay: 150,
                selectConnectedEdges: true,
                navigationButtons: false
            }
        };

        if (cgNetwork) {
            cgNetwork.destroy();
        }

        cgNetwork = new vis.Network(networkContainer, visData, options);
        const visNodeIds = visNodes.map(n => n.id);
        cgNetwork.once('stabilizationIterationsDone', () => {
            if (!cgNetwork) return;
            cgNetwork.setSize('100%', '100%');
            cgNetwork.redraw();
            cgNetwork.fit({
                animation: { duration: 250, easingFunction: 'easeInOutQuad' },
                nodes: visNodeIds
            });
        });

        cgNetwork.on('click', (params) => {
            if (params.nodes.length > 0) {
                const nodeId = params.nodes[0];
                const node = cgNodesList.find(n => n.id === nodeId);
                if (node) showNodeDetails(node);
            } else {
                nodeDetailsPanel.style.display = 'none';
            }
        });

        setupGraphResizeObserver(networkContainer);
    }

    function showNodeDetails(node) {
        if (!nodeDetailsPanel) return;
        if (ndName) ndName.textContent = toDisplayText(node.name || node.label, '');
        if (ndKind) {
            ndKind.textContent = toDisplayText(node.kind, '').toUpperCase();
            ndKind.className = 'status-tag info';
        }
        if (ndFilePath) ndFilePath.textContent = toDisplayText(node.file_path || node.source_file, '');

        if (node.start_line && node.end_line) {
            if (ndLineRow) ndLineRow.style.display = 'flex';
            if (ndLines) ndLines.textContent = `Lines ${node.start_line} - ${node.end_line}`;
        } else if (ndLineRow) {
            ndLineRow.style.display = 'none';
        }

        if (node.signature) {
            if (ndSignatureRow) ndSignatureRow.style.display = 'flex';
            if (ndSignature) ndSignature.textContent = toDisplayText(node.signature, '');
        } else if (ndSignatureRow) {
            ndSignatureRow.style.display = 'none';
        }

        if (node.docstring) {
            if (ndDocstringRow) ndDocstringRow.style.display = 'flex';
            if (ndDocstring) ndDocstring.textContent = toDisplayText(node.docstring, '');
        } else if (ndDocstringRow) {
            ndDocstringRow.style.display = 'none';
        }

        nodeDetailsPanel.style.display = 'flex';
    }

    // =========================================================================
    // 16. Projects, Databases, Topology Explorer & Branch Diff
    // =========================================================================
    async function fetchMergedTopology() {
        const [topoRes, cgRes] = await Promise.allSettled([
            fetch('/master/topology'),
            fetch('/codegraph/projects')
        ]);

        let legacyProjects = [];
        if (cgRes.status === 'fulfilled' && cgRes.value.ok) {
            try {
                const cgData = await cgRes.value.json();
                legacyProjects = Array.isArray(cgData.projects) ? cgData.projects : [];
            } catch (_) {
                legacyProjects = [];
            }
        } else if (topoRes.status !== 'fulfilled' || !topoRes.value.ok) {
            const failedRes = cgRes.status === 'fulfilled' ? cgRes.value : null;
            throw new Error(failedRes ? await extractErrorMessage(failedRes, 'Failed to fetch projects.') : 'Failed to fetch projects.');
        }

        const legacyByBase = {};
        legacyProjects.forEach(p => {
            const rawId = toDisplayText(p?.id, '').trim();
            if (!rawId) return;
            const colonIdx = rawId.indexOf(':');
            const baseId = colonIdx >= 0 ? rawId.slice(0, colonIdx) : rawId;
            const branchName = colonIdx >= 0 ? rawId.slice(colonIdx + 1) : '';
            if (!legacyByBase[baseId]) {
                legacyByBase[baseId] = {
                    project_id: baseId,
                    name: toDisplayText(p?.name, baseId),
                    description: toDisplayText(p?.description, ''),
                    sync_ip: p?.sync_ip || '',
                    sync_author: p?.sync_author || '',
                    project_path: p?.project_path || '',
                    created_at: p?.created_at || null,
                    updated_at: p?.updated_at || null,
                    branches: new Set()
                };
            }
            if (branchName) {
                legacyByBase[baseId].branches.add(branchName);
            }
            if (!legacyByBase[baseId].sync_ip && p?.sync_ip) legacyByBase[baseId].sync_ip = p.sync_ip;
            if (!legacyByBase[baseId].sync_author && p?.sync_author) legacyByBase[baseId].sync_author = p.sync_author;
            if (!legacyByBase[baseId].project_path && p?.project_path) legacyByBase[baseId].project_path = p.project_path;
            if (!legacyByBase[baseId].description && p?.description) legacyByBase[baseId].description = toDisplayText(p.description, '');
        });

        let topoData = null;
        if (topoRes.status === 'fulfilled' && topoRes.value.ok) {
            try {
                topoData = await topoRes.value.json();
            } catch (_) {
                topoData = null;
            }
        }

        const mergedProjects = [];
        const seenIds = new Set();

        if (topoData && Array.isArray(topoData.projects)) {
            topoData.projects.forEach(tp => {
                const pid = toDisplayText(tp?.project_id || tp?.id, '').trim();
                if (!pid) return;
                seenIds.add(pid);
                const legacy = legacyByBase[pid] || {};
                const rootBranch = toDisplayText(tp?.root_branch, 'main') || 'main';
                const rootSchema = toDisplayText(tp?.root_schema, 'pb_root') || 'pb_root';
                const rawBranches = Array.isArray(tp?.branches) ? tp.branches : [];
                const branchMap = new Map();

                rawBranches.forEach(b => {
                    const bName = toDisplayText(b?.branch_name, '').trim();
                    if (!bName) return;
                    const isRoot = Boolean(b?.is_root || bName === rootBranch);
                    branchMap.set(bName, {
                        branch_name: bName,
                        schema_name: toDisplayText(b?.schema_name, isRoot ? rootSchema : sanitizeBranchSchemaName(bName, rootBranch)),
                        base_branch: toDisplayText(b?.base_branch, rootBranch),
                        is_root: isRoot,
                        has_graph_schema: b?.has_graph_schema !== false,
                        has_doc_schema: b?.has_doc_schema !== false,
                        node_count: Number(b?.node_count ?? 0) || 0,
                        doc_count: Number(b?.doc_count ?? 0) || 0,
                        memory_count: Number(b?.memory_count ?? 0) || 0,
                        updated_at: b?.updated_at ?? null
                    });
                });

                if (legacy.branches) {
                    legacy.branches.forEach(bName => {
                        if (!branchMap.has(bName)) {
                            const isRoot = bName === rootBranch;
                            branchMap.set(bName, {
                                branch_name: bName,
                                schema_name: isRoot ? rootSchema : sanitizeBranchSchemaName(bName, rootBranch),
                                base_branch: rootBranch,
                                is_root: isRoot,
                                has_graph_schema: true,
                                has_doc_schema: true,
                                node_count: 0,
                                doc_count: 0,
                                memory_count: 0,
                                updated_at: legacy.updated_at || null
                            });
                        }
                    });
                }

                if (branchMap.size === 0) {
                    branchMap.set(rootBranch, {
                        branch_name: rootBranch,
                        schema_name: rootSchema,
                        base_branch: rootBranch,
                        is_root: true,
                        has_graph_schema: true,
                        has_doc_schema: true,
                        node_count: 0,
                        doc_count: 0,
                        memory_count: 0,
                        updated_at: legacy.updated_at || null
                    });
                }

                const sortedBranches = Array.from(branchMap.values()).sort((a, b) => {
                    if (a.is_root && !b.is_root) return -1;
                    if (!a.is_root && b.is_root) return 1;
                    return a.branch_name.localeCompare(b.branch_name);
                });

                mergedProjects.push({
                    project_id: pid,
                    name: toDisplayText(tp?.name || legacy.name, pid),
                    description: toDisplayText(tp?.description || legacy.description, ''),
                    db_name: toDisplayText(tp?.db_name, sanitizeProjectDbName(pid)),
                    backend_type: toDisplayText(tp?.backend_type || topoData.backend_type, 'sqlite').toLowerCase(),
                    root_branch: rootBranch,
                    root_schema: rootSchema,
                    branches: sortedBranches,
                    sync_ip: toDisplayText(tp?.sync_ip || legacy.sync_ip, ''),
                    sync_author: toDisplayText(tp?.sync_author || legacy.sync_author, ''),
                    project_path: toDisplayText(tp?.project_path || legacy.project_path, ''),
                    created_at: tp?.created_at || legacy.created_at || null,
                    updated_at: tp?.updated_at || legacy.updated_at || null
                });
            });
        }

        // Include any legacy /codegraph/projects entries not returned by /master/topology
        Object.values(legacyByBase).forEach(legacy => {
            if (seenIds.has(legacy.project_id)) return;
            const rootBranch = 'main';
            const branches = [];
            const branchNames = legacy.branches.size > 0 ? Array.from(legacy.branches) : [rootBranch];
            if (!branchNames.includes(rootBranch)) branchNames.unshift(rootBranch);
            branchNames.forEach(bName => {
                const isRoot = bName === rootBranch;
                branches.push({
                    branch_name: bName,
                    schema_name: isRoot ? 'pb_root' : sanitizeBranchSchemaName(bName, rootBranch),
                    base_branch: rootBranch,
                    is_root: isRoot,
                    has_graph_schema: true,
                    has_doc_schema: true,
                    node_count: 0,
                    doc_count: 0,
                    memory_count: 0,
                    updated_at: legacy.updated_at || null
                });
            });
            mergedProjects.push({
                project_id: legacy.project_id,
                name: legacy.name || legacy.project_id,
                description: legacy.description || '',
                db_name: sanitizeProjectDbName(legacy.project_id),
                backend_type: toDisplayText(topoData?.backend_type, 'sqlite').toLowerCase(),
                root_branch: rootBranch,
                root_schema: 'pb_root',
                branches,
                sync_ip: legacy.sync_ip || '',
                sync_author: legacy.sync_author || '',
                project_path: legacy.project_path || '',
                created_at: legacy.created_at || null,
                updated_at: legacy.updated_at || null
            });
        });

        const backendType = toDisplayText(topoData?.backend_type, mergedProjects[0]?.backend_type || 'sqlite').toLowerCase();
        const totalBranches = mergedProjects.reduce((acc, proj) => acc + proj.branches.length, 0);
        const masterDb = topoData?.master_database || {};
        const mergedTopology = {
            status: toDisplayText(topoData?.status, 'ok'),
            multi_db_enabled: topoData?.multi_db_enabled !== false,
            backend_type: backendType,
            master_database: {
                descriptor: toDisplayText(masterDb.descriptor, backendType === 'postgres' ? 'PostgreSQL Master Control Plane' : 'SQLite Master Control Plane'),
                project_count: Number(masterDb.project_count ?? mergedProjects.length) || 0,
                branch_schema_count: Number(masterDb.branch_schema_count ?? totalBranches) || 0
            },
            projects: mergedProjects,
            legacyProjects
        };

        lastTopologyData = mergedTopology;
        topologyProjectsMap = {};
        mergedProjects.forEach(proj => {
            topologyProjectsMap[proj.project_id] = proj;
        });

        return mergedTopology;
    }

    async function loadProjects() {
        try {
            const topology = await fetchMergedTopology();
            const projects = topology.legacyProjects || [];

            projectGroups = {};
            topology.projects.forEach(proj => {
                const branchSet = new Set();
                proj.branches.forEach(b => {
                    if (b.branch_name) branchSet.add(b.branch_name);
                });
                projectGroups[proj.project_id] = {
                    id: proj.project_id,
                    name: proj.name || proj.project_id,
                    branches: branchSet,
                    sync_ip: proj.sync_ip,
                    sync_author: proj.sync_author,
                    project_path: proj.project_path,
                    db_name: proj.db_name,
                    backend_type: proj.backend_type
                };
            });

            if (cgProjectSelect) {
                const currentVal = cgProjectSelect.value;
                cgProjectSelect.innerHTML = '<option value="">Local Codegraph (Default)</option>';
                Object.values(projectGroups).forEach(group => {
                    const authorStr = group.sync_author ? ` by ${group.sync_author}` : '';
                    const ipSuffix = (group.sync_ip || group.sync_author) ? ` (${group.sync_ip || ''}${authorStr})` : '';
                    if (group.branches.size > 0) {
                        const optAll = document.createElement('option');
                        optAll.value = `${group.id}:%`;
                        optAll.textContent = `${group.name} (All Branches)${ipSuffix} (Server)`;
                        cgProjectSelect.appendChild(optAll);

                        group.branches.forEach(branch => {
                            const opt = document.createElement('option');
                            opt.value = `${group.id}:${branch}`;
                            opt.textContent = `${group.name}:${branch}${ipSuffix} (Server)`;
                            cgProjectSelect.appendChild(opt);
                        });
                    } else {
                        const opt = document.createElement('option');
                        opt.value = group.id;
                        opt.textContent = `${group.name}${ipSuffix} (Server)`;
                        cgProjectSelect.appendChild(opt);
                    }
                });
                if (currentVal && Array.from(cgProjectSelect.options).some(o => o.value === currentVal)) {
                    cgProjectSelect.value = currentVal;
                }
            }

            if (currentProjectSelect) {
                const currentGlobalVal = currentProjectSelect.value;
                currentProjectSelect.innerHTML = '<option value="default">default</option>';
                Object.values(projectGroups).forEach(group => {
                    const optGlobal = document.createElement('option');
                    optGlobal.value = group.id;
                    const authorStr = group.sync_author ? ` by ${group.sync_author}` : '';
                    const ipSuffix = (group.sync_ip || group.sync_author) ? ` (${group.sync_ip || ''}${authorStr})` : '';
                    optGlobal.textContent = `${group.id}${ipSuffix}`;
                    currentProjectSelect.appendChild(optGlobal);
                });
                const urlParams = new URLSearchParams(window.location.search);
                const urlProject = (urlParams.get('project') || '').trim();
                const rawUrlBranch = (urlParams.get('branch') || '').trim();
                const urlBranch = rawUrlBranch === 'all' ? '%' : rawUrlBranch;
                const savedProject = localStorage.getItem('activeProject');
                const projectToRestore = urlProject || savedProject || currentGlobalVal || 'default';
                if (urlProject && !Array.from(currentProjectSelect.options).some(o => o.value === urlProject)) {
                    const optUrlProj = document.createElement('option');
                    optUrlProj.value = urlProject;
                    optUrlProj.textContent = urlProject;
                    currentProjectSelect.appendChild(optUrlProj);
                }
                if (projectToRestore && Array.from(currentProjectSelect.options).some(o => o.value === projectToRestore)) {
                    currentProjectSelect.value = projectToRestore;
                } else {
                    currentProjectSelect.value = 'default';
                }
                updateBranchDropdown(currentProjectSelect.value);
                if (urlBranch && currentBranchSelect) {
                    if (!Array.from(currentBranchSelect.options).some(o => o.value === urlBranch)) {
                        if (branchSelectorContainer) branchSelectorContainer.style.display = 'flex';
                        const optUrlBranch = document.createElement('option');
                        optUrlBranch.value = urlBranch;
                        optUrlBranch.textContent = urlBranch;
                        currentBranchSelect.appendChild(optUrlBranch);
                    }
                    currentBranchSelect.value = urlBranch;
                    localStorage.setItem(`branch_${currentProjectSelect.value}`, urlBranch);
                }
                recalculateActiveUser();
                if (cgProjectSelect) {
                    const desiredCg = activeUser === 'default' ? '' : activeUser;
                    if (desiredCg && Array.from(cgProjectSelect.options).some(o => o.value === desiredCg)) {
                        cgProjectSelect.value = desiredCg;
                    }
                }
                refreshAllRouteLinks();
            } else {
                updateActiveTenantStorageHint();
            }

            if (diffBaseProject && diffTargetProject) {
                const currentBaseVal = diffBaseProject.value;
                const currentTargetVal = diffTargetProject.value;
                diffBaseProject.innerHTML = '<option value="">Select Base Project...</option>';
                diffTargetProject.innerHTML = '<option value="">Select Target Project...</option>';
                const diffCandidates = [];
                const seenDiffIds = new Set();
                projects.forEach(p => {
                    if (!p?.id || seenDiffIds.has(p.id)) return;
                    seenDiffIds.add(p.id);
                    diffCandidates.push(p);
                });
                topology.projects.forEach(proj => {
                    proj.branches.forEach(b => {
                        const scopedId = `${proj.project_id}:${b.branch_name}`;
                        if (!seenDiffIds.has(scopedId)) {
                            seenDiffIds.add(scopedId);
                            diffCandidates.push({ id: scopedId, sync_author: proj.sync_author, sync_ip: proj.sync_ip });
                        }
                    });
                });
                diffCandidates.forEach(p => {
                    const authorStr = p.sync_author ? ` by ${p.sync_author}` : '';
                    const ipSuffix = (p.sync_ip || p.sync_author) ? ` (${p.sync_ip || ''}${authorStr})` : '';

                    const optBase = document.createElement('option');
                    optBase.value = p.id;
                    optBase.textContent = `${p.id}${ipSuffix}`;
                    diffBaseProject.appendChild(optBase);

                    const optTarget = document.createElement('option');
                    optTarget.value = p.id;
                    optTarget.textContent = `${p.id}${ipSuffix}`;
                    diffTargetProject.appendChild(optTarget);
                });
                if (currentBaseVal && Array.from(diffBaseProject.options).some(o => o.value === currentBaseVal)) {
                    diffBaseProject.value = currentBaseVal;
                }
                if (currentTargetVal && Array.from(diffTargetProject.options).some(o => o.value === currentTargetVal)) {
                    diffTargetProject.value = currentTargetVal;
                }
            }

            syncActiveProjectOnServer();
            loadStats();
            loadMemories();
        } catch (err) {
            console.error('Failed to load projects:', err);
        }
    }

    function formatCodeNodeInfo(rawNode) {
        const node = rawNode || {};
        const meta = safeParseJSON(node.metadata);
        const title = node.qualified_name || node.name || node.label || meta.qualified_name || meta.name || node.id || 'Unknown symbol';
        const kind = node.kind || meta.kind || node.file_type || 'symbol';
        const filePath = node.file_path || node.source_file || meta.file_path || '';
        const location = node.source_location || (
            node.start_line != null
                ? (node.end_line != null ? `L${node.start_line}-${node.end_line}` : `L${node.start_line}`)
                : ''
        );
        const sub = [String(kind).toUpperCase(), filePath, location].filter(Boolean).join(' • ');
        return { title, kind, filePath, location, sub, meta };
    }

    function buildCodeDiffDetailHtml(mod) {
        const afterNode = mod?.after || mod?.node || mod || {};
        const beforeNode = mod?.before || mod?.base || null;
        const fields = ['label', 'file_type', 'source_file', 'source_location', 'community', 'signature', 'docstring', 'start_line', 'end_line', 'metadata'];

        const diffs = [];
        if (beforeNode) {
            for (const field of fields) {
                const oldVal = beforeNode[field];
                const newVal = afterNode[field];
                const oldStr = oldVal === undefined || oldVal === null ? '' : (typeof oldVal === 'object' ? JSON.stringify(oldVal) : String(oldVal));
                const newStr = newVal === undefined || newVal === null ? '' : (typeof newVal === 'object' ? JSON.stringify(newVal) : String(newVal));
                if (oldStr !== newStr) {
                    diffs.push({ field, oldStr, newStr });
                }
            }
        }

        const changes = (Array.isArray(mod?.changes) && mod.changes.length > 0)
            ? mod.changes
            : diffs.map(d => d.field);

        const parts = [];
        if (changes.length > 0) {
            parts.push(`<div class="diff-item-detail">Changed: ${escapeHTML(changes.join(', '))}</div>`);
        }
        if (diffs.length > 0) {
            const diffLines = diffs.map(d => `
                <div style="margin-top: 4px;">
                    <span style="font-weight: 600; color: var(--text-muted);">${escapeHTML(d.field)}:</span>
                    <div style="font-size: 11px; color: var(--danger); text-decoration: line-through; white-space: pre-wrap; font-family: var(--font-mono);">${escapeHTML(d.oldStr || '(empty)')}</div>
                    <div style="font-size: 11px; color: var(--success); white-space: pre-wrap; font-family: var(--font-mono);">${escapeHTML(d.newStr || '(empty)')}</div>
                </div>
            `).join('');
            parts.push(`<div class="diff-item-detail">${diffLines}</div>`);
        }
        return parts.join('');
    }

    function getMemoryDiffTitle(memObj, fallbackMeta) {
        const meta = safeParseJSON(memObj?.meta ?? fallbackMeta);
        const filePath = memObj?.file_path || meta.file_path;
        const secIdx = memObj?.section_index ?? meta.section_index ?? 0;
        return filePath ? `${filePath} (sec ${secIdx})` : (memObj?.title || memObj?.primary_sector || memObj?.id || 'Memory');
    }

    const DIFF_PAGE_SIZE = 80;

    function renderPaginatedDiffList(containerEl, items, renderItemFn, emptyMessage, pageSize = DIFF_PAGE_SIZE) {
        if (!containerEl) return;
        containerEl.innerHTML = '';
        if (!Array.isArray(items) || items.length === 0) {
            containerEl.innerHTML = `<div class="empty-placeholder">${escapeHTML(emptyMessage)}</div>`;
            return;
        }

        let renderedCount = 0;

        const appendNextBatch = (existingBtnWrap) => {
            if (existingBtnWrap && existingBtnWrap.parentNode === containerEl) {
                existingBtnWrap.remove();
            }
            const nextEnd = Math.min(renderedCount + pageSize, items.length);
            const frag = document.createDocumentFragment();
            for (let i = renderedCount; i < nextEnd; i++) {
                const el = renderItemFn(items[i]);
                if (el) frag.appendChild(el);
            }
            renderedCount = nextEnd;
            containerEl.appendChild(frag);

            if (renderedCount < items.length) {
                const btnWrap = document.createElement('div');
                btnWrap.style.cssText = 'display:flex; justify-content:center; padding:12px 0;';
                const btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'btn btn-secondary btn-sm';
                btn.textContent = `Load more changes (showing ${renderedCount} of ${items.length})`;
                btn.addEventListener('click', () => appendNextBatch(btnWrap));
                btnWrap.appendChild(btn);
                containerEl.appendChild(btnWrap);
            }
        };

        appendNextBatch(null);
    }

    function createCodeDiffItemElement(entry) {
        const { changeType, payload } = entry;
        const el = document.createElement('div');
        el.className = 'diff-item';
        if (changeType === 'modified') {
            const targetNode = payload?.after || payload?.node || payload || {};
            const info = formatCodeNodeInfo(targetNode);
            const detailHtml = buildCodeDiffDetailHtml(payload);
            el.innerHTML = `
                <div class="diff-item-header">
                    <span class="diff-item-title">${escapeHTML(info.title)}</span>
                    <span class="diff-change-badge modified">Modified</span>
                </div>
                <div class="diff-item-sub">${escapeHTML(info.sub)}</div>
                ${detailHtml}
            `;
        } else {
            const info = formatCodeNodeInfo(payload);
            const badgeLabel = changeType === 'added' ? 'Added' : 'Deleted';
            el.innerHTML = `
                <div class="diff-item-header">
                    <span class="diff-item-title">${escapeHTML(info.title)}</span>
                    <span class="diff-change-badge ${changeType}">${badgeLabel}</span>
                </div>
                <div class="diff-item-sub">${escapeHTML(info.sub)}</div>
            `;
        }
        return el;
    }

    function createMemoryDiffItemElement(entry) {
        const { changeType, payload } = entry;
        const el = document.createElement('div');
        el.className = 'diff-item';
        if (changeType === 'modified') {
            const baseMem = payload?.base || payload?.before || {};
            const targetMem = payload?.target || payload?.after || payload?.node || payload || {};
            const title = getMemoryDiffTitle(
                {
                    ...targetMem,
                    file_path: payload?.file_path || targetMem.file_path,
                    section_index: payload?.section_index ?? targetMem.section_index
                },
                baseMem.meta
            );
            const oldContent = baseMem.content ?? payload?.old_content ?? payload?.before_content ?? '';
            const newContent = targetMem.content ?? payload?.new_content ?? payload?.after_content ?? payload?.content ?? '';
            el.innerHTML = `
                <div class="diff-item-header">
                    <span class="diff-item-title">${escapeHTML(title)}</span>
                    <span class="diff-change-badge modified">Modified</span>
                </div>
                <div class="diff-item-sub">
                    <div style="font-size:11px; color:var(--danger); text-decoration:line-through; margin-bottom:4px; max-height:100px; overflow-y:auto;">
                        <pre style="margin:0; font-family:var(--font-mono); white-space:pre-wrap;">${escapeHTML(oldContent)}</pre>
                    </div>
                    <div style="font-size:11px; color:var(--success); max-height:100px; overflow-y:auto;">
                        <pre style="margin:0; font-family:var(--font-mono); white-space:pre-wrap;">${escapeHTML(newContent)}</pre>
                    </div>
                </div>
            `;
        } else {
            const title = getMemoryDiffTitle(payload);
            const content = changeType === 'added'
                ? (payload?.content ?? payload?.text ?? payload?.new_content ?? '')
                : (payload?.content ?? payload?.text ?? payload?.old_content ?? payload?.before_content ?? '');
            const badgeLabel = changeType === 'added' ? 'Added' : 'Deleted';
            el.innerHTML = `
                <div class="diff-item-header">
                    <span class="diff-item-title">${escapeHTML(title)}</span>
                    <span class="diff-change-badge ${changeType}">${badgeLabel}</span>
                </div>
                <div class="diff-item-sub"><pre style="margin:0; font-family:var(--font-mono); white-space:pre-wrap; font-size:11px; max-height:150px; overflow-y:auto;">${escapeHTML(content)}</pre></div>
            `;
        }
        return el;
    }

    async function compareVersions({ skipUrlSync = false } = {}) {
        const base = diffBaseProject ? diffBaseProject.value : '';
        const target = diffTargetProject ? diffTargetProject.value : '';
        if (!base || !target) {
            showToast('Please select both base and target versions.', 'warning');
            return;
        }
        if (!skipUrlSync) {
            syncCurrentRouteUrl({ replace: true });
        }

        if (btnCompareVersions) {
            btnCompareVersions.disabled = true;
            btnCompareVersions.innerHTML = '<i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i> Comparing...';
        }
        if (diffResultsArea) diffResultsArea.style.display = 'none';

        try {
            const res = await fetch(`/codegraph/diff?base_project_id=${encodeURIComponent(base)}&target_project_id=${encodeURIComponent(target)}`);
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'Failed to fetch project diff data'));
            const diffData = await res.json();

            const addedCg = Array.isArray(diffData.added) ? diffData.added : [];
            const deletedCg = Array.isArray(diffData.deleted) ? diffData.deleted : [];
            const modifiedCg = Array.isArray(diffData.modified) ? diffData.modified : [];

            if (diffCountAdded) diffCountAdded.textContent = addedCg.length;
            if (diffCountDeleted) diffCountDeleted.textContent = deletedCg.length;
            if (diffCountModified) diffCountModified.textContent = modifiedCg.length;

            if (diffCgList) {
                const cgEntries = [
                    ...addedCg.map(payload => ({ changeType: 'added', payload })),
                    ...deletedCg.map(payload => ({ changeType: 'deleted', payload })),
                    ...modifiedCg.map(payload => ({ changeType: 'modified', payload }))
                ];
                renderPaginatedDiffList(diffCgList, cgEntries, createCodeDiffItemElement, 'No codebase changes.');
            }

            const memsDiffRes = await fetch(`/memory/diff?base_project_id=${encodeURIComponent(base)}&target_project_id=${encodeURIComponent(target)}`);
            if (!memsDiffRes.ok) throw new Error(await extractErrorMessage(memsDiffRes, 'Failed to fetch memory diff data'));
            const memsDiffData = await memsDiffRes.json();

            const addedMems = Array.isArray(memsDiffData.added) ? memsDiffData.added : [];
            const deletedMems = Array.isArray(memsDiffData.deleted) ? memsDiffData.deleted : [];
            const modifiedMems = Array.isArray(memsDiffData.modified) ? memsDiffData.modified : [];

            const memCountAdded = document.getElementById('memCountAdded');
            const memCountDeleted = document.getElementById('memCountDeleted');
            const memCountModified = document.getElementById('memCountModified');
            if (memCountAdded) memCountAdded.textContent = addedMems.length;
            if (memCountDeleted) memCountDeleted.textContent = deletedMems.length;
            if (memCountModified) memCountModified.textContent = modifiedMems.length;

            if (diffOmList) {
                const memEntries = [
                    ...addedMems.map(payload => ({ changeType: 'added', payload })),
                    ...deletedMems.map(payload => ({ changeType: 'deleted', payload })),
                    ...modifiedMems.map(payload => ({ changeType: 'modified', payload }))
                ];
                renderPaginatedDiffList(diffOmList, memEntries, createMemoryDiffItemElement, 'No memory changes.');
            }

            if (diffResultsArea) diffResultsArea.style.display = 'block';
        } catch (err) {
            showToast('Error running comparison: ' + err.message, 'error');
        } finally {
            if (btnCompareVersions) {
                btnCompareVersions.disabled = false;
                btnCompareVersions.innerHTML = '<i class="fa-solid fa-arrow-right-arrow-left" aria-hidden="true"></i> Run Comparison';
            }
        }
    }

    function renderTopologySummary(topology) {
        const backendType = toDisplayText(topology?.backend_type, 'sqlite').toLowerCase();
        const masterDb = topology?.master_database || {};
        const projects = Array.isArray(topology?.projects) ? topology.projects : [];
        const totalBranches = projects.reduce((sum, p) => sum + (Array.isArray(p.branches) ? p.branches.length : 0), 0);

        if (topoMasterDb) {
            topoMasterDb.textContent = backendType === 'postgres'
                ? 'PostgreSQL Control Plane'
                : 'SQLite Control Plane';
        }
        if (topoMasterMeta) {
            const descriptor = toDisplayText(masterDb.descriptor, '');
            const modeLabel = topology?.multi_db_enabled === false ? 'Single-DB Mode' : '3-Tier Multi-DB Active';
            topoMasterMeta.textContent = descriptor ? `${descriptor} · ${modeLabel}` : modeLabel;
        }
        if (topoProjectDbCount) {
            topoProjectDbCount.textContent = String(masterDb.project_count ?? projects.length);
        }
        if (topoBranchSchemaCount) {
            topoBranchSchemaCount.textContent = String(masterDb.branch_schema_count ?? totalBranches);
        }
    }

    function switchWorkspaceContext(projectId, branchName) {
        const targetProj = (projectId || '').trim() || 'default';
        const targetBranch = (branchName || '').trim();
        localStorage.setItem('activeProject', targetProj);
        if (targetBranch) {
            localStorage.setItem(`branch_${targetProj}`, targetBranch);
        }
        if (currentProjectSelect) {
            if (!Array.from(currentProjectSelect.options).some(o => o.value === targetProj)) {
                const opt = document.createElement('option');
                opt.value = targetProj;
                opt.textContent = targetProj;
                currentProjectSelect.appendChild(opt);
            }
            currentProjectSelect.value = targetProj;
            updateBranchDropdown(targetProj);
            if (currentBranchSelect && targetBranch) {
                if (!Array.from(currentBranchSelect.options).some(o => o.value === targetBranch)) {
                    const optB = document.createElement('option');
                    optB.value = targetBranch;
                    optB.textContent = targetBranch;
                    currentBranchSelect.appendChild(optB);
                }
                currentBranchSelect.value = targetBranch;
            }
        }
        recalculateActiveUser();
        onWorkspaceContextChanged();
        showToast(
            targetBranch
                ? `Switched workspace to ${targetProj} (${targetBranch}).`
                : `Switched workspace to ${targetProj}.`,
            'success',
            2800
        );
    }

    async function dropBranchSchema(projectId, branchName, schemaName) {
        const ok = await confirmAction({
            title: 'Drop Branch Schema',
            message: `Are you sure you want to drop branch '${branchName}' (schema '${schemaName}') in project '${projectId}'? This removes all Graph, Document, and Memory data in that schema.`,
            confirmLabel: 'Drop Schema',
            danger: true
        });
        if (!ok) return;

        try {
            const res = await fetch(
                `/master/projects/${encodeURIComponent(projectId)}/branches/${encodeURIComponent(branchName)}`,
                { method: 'DELETE' }
            );
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'Failed to drop branch schema'));
            showToast(`Dropped branch schema '${branchName}' (${schemaName}).`, 'success');
            if (localStorage.getItem(`branch_${projectId}`) === branchName) {
                localStorage.setItem(`branch_${projectId}`, '%');
            }
            await loadProjects();
            await loadProjectsList();
        } catch (err) {
            showToast('Error dropping branch schema: ' + err.message, 'error');
        }
    }

    async function loadProjectsList() {
        const listContainer = document.getElementById('projectRegistryList');
        if (!listContainer) return;

        listContainer.innerHTML = '<div class="loader"><i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i> Loading topology...</div>';
        try {
            const topology = await fetchMergedTopology();
            renderTopologySummary(topology);
            updateActiveTenantStorageHint();

            const projects = topology.projects || [];
            const isReader = isReaderRole();

            listContainer.innerHTML = '';
            if (projects.length === 0) {
                listContainer.innerHTML = '<div class="placeholder-item">No project databases provisioned yet.</div>';
                return;
            }

            const serverOrigin = window.location.origin;

            projects.forEach(p => {
                const card = document.createElement('article');
                card.className = 'project-topo-card';

                const backend = toDisplayText(p.backend_type, 'sqlite').toLowerCase();
                const backendPillClass = backend === 'postgres' ? 'postgres' : 'sqlite';
                const backendPillLabel = backend === 'postgres' ? 'POSTGRES DB' : 'SQLITE DB';
                const rootBranch = toDisplayText(p.root_branch, 'main') || 'main';
                const branches = Array.isArray(p.branches) ? p.branches : [];
                const activeBranchForCli = branches[0]?.branch_name || rootBranch;
                const graphifySyncCmd = `projectbrain graphify-sync ${p.project_id} ${serverOrigin} . ${activeBranchForCli}`;
                const lastSync = formatEpochTimestamp(p.updated_at, 'N/A');

                const branchRowsHtml = branches.map(b => {
                    const isRoot = Boolean(b.is_root || b.branch_name === rootBranch);
                    const rootTagHtml = isRoot ? '<span class="root-tag">ROOT</span>' : '';
                    const graphCapHtml = `<span class="cap-badge ${b.has_graph_schema !== false ? 'active' : 'inactive'}">Graph</span>`;
                    const docCapHtml = `<span class="cap-badge ${b.has_doc_schema !== false ? 'active' : 'inactive'}">Docs &amp; Memory</span>`;
                    const dropBtnHtml = (!isRoot && !isReader)
                        ? `<button type="button" class="btn btn-danger btn-sm btn-drop-branch-schema" data-project="${escapeHTML(p.project_id)}" data-branch="${escapeHTML(b.branch_name)}" data-schema="${escapeHTML(b.schema_name)}">
                                <i class="fa-solid fa-trash-can" aria-hidden="true"></i> Drop Schema
                           </button>`
                        : '';
                    return `
                        <tr>
                            <td>
                                <div class="branch-cell">
                                    <code class="code-font">${escapeHTML(b.branch_name)}</code>
                                    ${rootTagHtml}
                                </div>
                            </td>
                            <td><code class="code-font">SCHEMA: ${escapeHTML(b.schema_name)}</code></td>
                            <td>
                                <div class="cap-badges">
                                    ${graphCapHtml}
                                    ${docCapHtml}
                                </div>
                            </td>
                            <td>
                                <div class="schema-counts mono-num">
                                    <span>Nodes: <strong>${escapeHTML(b.node_count ?? 0)}</strong></span>
                                    <span>Docs: <strong>${escapeHTML(b.doc_count ?? 0)}</strong></span>
                                    <span>Memories: <strong>${escapeHTML(b.memory_count ?? 0)}</strong></span>
                                </div>
                            </td>
                            <td>
                                <div class="branch-actions-cell">
                                    <button type="button" class="btn btn-secondary btn-sm btn-switch-branch-schema" data-project="${escapeHTML(p.project_id)}" data-branch="${escapeHTML(b.branch_name)}">
                                        <i class="fa-solid fa-arrow-right-to-bracket" aria-hidden="true"></i> Switch Here
                                    </button>
                                    ${dropBtnHtml}
                                </div>
                            </td>
                        </tr>
                    `;
                }).join('');

                const newBranchBtnHtml = !isReader
                    ? `<button type="button" class="btn btn-secondary btn-sm btn-new-branch-schema">
                            <i class="fa-solid fa-code-branch" aria-hidden="true"></i> + New Branch Schema
                       </button>`
                    : '';
                const dropProjectBtnHtml = !isReader
                    ? `<button type="button" class="btn btn-danger btn-sm btn-drop-project-db">
                            <i class="fa-solid fa-trash-can" aria-hidden="true"></i> Drop Project DB
                       </button>`
                    : '';

                const metaGridHtml = (p.project_path || p.sync_author || p.sync_ip || p.updated_at)
                    ? `
                        <div class="hardware-summary-grid project-topo-meta">
                            <div class="telemetry-kv">
                                <span class="telemetry-k">Workspace Path</span>
                                <span class="telemetry-v mono-num">${escapeHTML(p.project_path || 'Not synced yet')}</span>
                            </div>
                            <div class="telemetry-kv">
                                <span class="telemetry-k">Last Sync</span>
                                <span class="telemetry-v mono-num">${escapeHTML(p.sync_author || 'N/A')} (${escapeHTML(p.sync_ip || 'N/A')}) · ${escapeHTML(lastSync)}</span>
                            </div>
                        </div>
                    `
                    : '';

                card.innerHTML = `
                    <div class="project-topo-header">
                        <div class="project-topo-title-group">
                            <span class="account-username">${escapeHTML(p.name || p.project_id)}</span>
                            <span class="account-role-badge collaborator">${escapeHTML(p.project_id)}</span>
                            <span class="backend-pill ${escapeHTML(backendPillClass)}">${escapeHTML(backendPillLabel)}</span>
                            <span class="db-badge">DB: ${escapeHTML(p.db_name)}</span>
                        </div>
                    </div>
                    <div class="info-text">${escapeHTML(p.description || 'No description provided.')}</div>
                    ${metaGridHtml}
                    <div class="branch-schema-section">
                        <span class="branch-schema-heading">Branch Schemas (${escapeHTML(branches.length)})</span>
                        <div class="branch-schema-table-wrap">
                            <table class="branch-schema-table">
                                <thead>
                                    <tr>
                                        <th>Branch</th>
                                        <th>Physical Schema</th>
                                        <th>Capabilities</th>
                                        <th>Live Counts</th>
                                        <th>Actions</th>
                                    </tr>
                                </thead>
                                <tbody>${branchRowsHtml}</tbody>
                            </table>
                        </div>
                    </div>
                    <div class="telemetry-kv">
                        <span class="telemetry-k">Graphify Sync CLI</span>
                        <div class="project-cli-snippet">
                            <code class="code-font project-cli-code">${escapeHTML(graphifySyncCmd)}</code>
                            <button type="button" class="btn btn-secondary btn-sm btn-copy-cmd" data-cmd="${escapeHTML(graphifySyncCmd)}" title="Copy graphify-sync command">
                                <i class="fa-solid fa-copy" aria-hidden="true"></i> Copy
                            </button>
                        </div>
                    </div>
                    ${(newBranchBtnHtml || dropProjectBtnHtml) ? `<div class="project-card-actions">${newBranchBtnHtml}${dropProjectBtnHtml}</div>` : ''}
                `;

                card.querySelectorAll('.btn-switch-branch-schema').forEach(btn => {
                    btn.addEventListener('click', () => {
                        const projId = btn.getAttribute('data-project') || p.project_id;
                        const brName = btn.getAttribute('data-branch') || rootBranch;
                        switchWorkspaceContext(projId, brName);
                    });
                });

                card.querySelectorAll('.btn-drop-branch-schema').forEach(btn => {
                    btn.addEventListener('click', () => {
                        const projId = btn.getAttribute('data-project') || p.project_id;
                        const brName = btn.getAttribute('data-branch') || '';
                        const scName = btn.getAttribute('data-schema') || '';
                        if (brName) dropBranchSchema(projId, brName, scName);
                    });
                });

                const newBranchBtn = card.querySelector('.btn-new-branch-schema');
                if (newBranchBtn) {
                    newBranchBtn.addEventListener('click', () => openNewBranchSchemaDialog(p));
                }

                const dropProjectBtn = card.querySelector('.btn-drop-project-db');
                if (dropProjectBtn) {
                    dropProjectBtn.addEventListener('click', () => deleteRegProject(p.project_id, p.db_name));
                }

                card.querySelectorAll('.btn-copy-cmd').forEach(copyBtn => {
                    copyBtn.addEventListener('click', async () => {
                        const cmd = copyBtn.getAttribute('data-cmd') || '';
                        try {
                            await navigator.clipboard.writeText(cmd);
                            showToast('Sync command copied to clipboard.', 'success', 2500);
                        } catch (err) {
                            showToast('Copy failed: ' + err.message, 'error');
                        }
                    });
                });

                listContainer.appendChild(card);
            });
        } catch (err) {
            listContainer.innerHTML = `<div class="error-msg is-visible">Error: ${escapeHTML(err.message)}</div>`;
        }
    }

    async function saveProject(e) {
        e.preventDefault();
        const id = (document.getElementById('regProjectId')?.value || '').trim();
        const name = (document.getElementById('regProjectName')?.value || '').trim();
        const description = (document.getElementById('regProjectDesc')?.value || '').trim();
        if (!id || !name) {
            showToast('Project ID and Display Name are required.', 'warning');
            return;
        }

        try {
            const res = await fetch('/codegraph/projects', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id, name, description })
            });
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'Failed to register project'));

            showToast(`Project database for '${name}' provisioned successfully.`, 'success');
            document.getElementById('projectRegistryForm')?.reset();
            updateRegDbPreview();
            await loadProjects();
            await loadProjectsList();
        } catch (err) {
            showToast('Error provisioning project database: ' + err.message, 'error');
        }
    }

    async function deleteRegProject(projectId, dbName = '') {
        const dbLabel = dbName || sanitizeProjectDbName(projectId);
        const ok = await confirmAction({
            title: 'Drop Project Database',
            message: `Are you sure you want to drop project '${projectId}' (database '${dbLabel}')? This permanently removes all branch schemas, graph nodes, documents, and memories.`,
            confirmLabel: 'Drop Project DB',
            danger: true
        });
        if (!ok) return;

        try {
            const res = await fetch(`/codegraph/projects/${encodeURIComponent(projectId)}`, { method: 'DELETE' });
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'Failed to drop project database'));
            showToast(`Project database '${projectId}' dropped.`, 'success');
            if (localStorage.getItem('activeProject') === projectId) {
                localStorage.setItem('activeProject', 'default');
            }
            await loadProjects();
            await loadProjectsList();
        } catch (err) {
            showToast('Error dropping project database: ' + err.message, 'error');
        }
    }

    // =========================================================================
    // 17. Unified KB & Traceability (/kb/*)
    // =========================================================================
    async function buildCompleteKb() {
        const { baseProject, branch } = getProjectContext();
        const workspacePath = (kbWorkspacePath?.value || '').trim() || '.';
        const targetBranch = (kbBranchInput?.value || '').trim() || branch || 'main';
        const device = kbDeviceSelect ? kbDeviceSelect.value : 'auto';
        const workers = Math.max(1, parseInt(kbWorkersInput?.value || '4', 10) || 4);

        if (btnRunBuildKb) {
            btnRunBuildKb.disabled = true;
            btnRunBuildKb.innerHTML = '<i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i> Building KB...';
        }
        if (kbBuildResult) {
            kbBuildResult.textContent = 'Building unified Knowledge Base (AST + Docs + Cross-Edges)...';
        }

        try {
            const res = await fetch('/kb/build', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    project_id: baseProject,
                    branch: targetBranch,
                    workspace_path: workspacePath,
                    device,
                    workers
                })
            });
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'KB build failed.'));
            const data = await res.json();

            if (kbBuildResult) {
                const cov = data.coverage || {};
                const summaryLines = [
                    `Status: ${data.status || 'success'} (${data.project_id}:${data.branch})`,
                    `Doc Chunks: ${data.total_chunks ?? 0} | Cross-Edges: ${data.total_cross_edges ?? 0} | Drift Count: ${data.drift_count ?? 0}`,
                    `Documented Symbols: ${cov.documented_pct ?? 0}% | Manifest: ${data.manifest_path || 'N/A'}`,
                    '---',
                    JSON.stringify(data, null, 2)
                ];
                kbBuildResult.textContent = summaryLines.join('\n');
            }
            showToast('Unified KB build completed.', 'success');
            loadKbCoverage();
        } catch (err) {
            if (kbBuildResult) kbBuildResult.textContent = `Error: ${err.message}`;
            showToast('KB build failed: ' + err.message, 'error');
        } finally {
            if (btnRunBuildKb) {
                btnRunBuildKb.disabled = false;
                btnRunBuildKb.innerHTML = '<i class="fa-solid fa-play" aria-hidden="true"></i> Build Complete KB';
            }
        }
    }

    async function loadKbCoverage() {
        if (!kbCoveragePanel) return;
        const { baseProject, branch } = getProjectContext();
        const targetBranch = (kbBranchInput?.value || '').trim() || branch || 'main';
        const workspacePath = (kbWorkspacePath?.value || '').trim() || '.';

        kbCoveragePanel.innerHTML = '<div class="loader"><i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i> Computing KB coverage...</div>';

        try {
            const params = new URLSearchParams({
                project_id: baseProject,
                branch: targetBranch,
                workspace_path: workspacePath
            });
            const res = await fetch(`/kb/coverage?${params.toString()}`);
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'Failed to fetch KB coverage.'));
            const cov = await res.json();

            const undocumented = cov.undocumented_key_symbols || cov.undocumented_symbols_list || cov.key_symbols || [];
            const driftItems = cov.drift_items || [];

            kbCoveragePanel.innerHTML = `
                <div class="hardware-summary-grid">
                    <div class="telemetry-kv">
                        <span class="telemetry-k">Documented Symbols</span>
                        <span class="telemetry-v mono-num">${cov.documented_symbols ?? 0} / ${cov.total_symbols ?? 0} (${round(cov.documented_pct, 1)}%)</span>
                    </div>
                    <div class="telemetry-kv">
                        <span class="telemetry-k">Inferred / Undocumented</span>
                        <span class="telemetry-v mono-num">${round(cov.inferred_pct, 1)}% inferred · ${round(cov.undocumented_pct, 1)}% undocumented</span>
                    </div>
                    <div class="telemetry-kv">
                        <span class="telemetry-k">API &amp; MCP Coverage</span>
                        <span class="telemetry-v mono-num">API: ${round(cov.api_coverage_pct, 1)}% · MCP: ${round(cov.mcp_coverage_pct, 1)}%</span>
                    </div>
                    <div class="telemetry-kv">
                        <span class="telemetry-k">Test Links &amp; Cross Edges</span>
                        <span class="telemetry-v mono-num">${round(cov.test_linked_pct, 1)}% tested · ${cov.cross_domain_edges ?? 0} edges · ${cov.total_doc_chunks ?? 0} chunks</span>
                    </div>
                </div>
                ${Array.isArray(undocumented) && undocumented.length > 0 ? `
                    <div class="section-spacer-sm">
                        <div class="field-group-label">Top Undocumented Key Symbols (${undocumented.length})</div>
                        <div class="tags-cloud" style="margin-top:0.35rem;">
                            ${undocumented.slice(0, 12).map(s => {
                                const symLabel = (s && typeof s === 'object')
                                    ? (s.label || s.name || s.qualified_name || s.id || toDisplayText(s))
                                    : toDisplayText(s);
                                const symFile = (s && typeof s === 'object' && s.source_file) ? ` (${s.source_file})` : '';
                                return `<span class="tag" title="${escapeHTML(symFile.trim())}">${escapeHTML(symLabel)}</span>`;
                            }).join('')}
                        </div>
                    </div>
                ` : ''}
                ${driftItems.length > 0 ? `
                    <div class="section-spacer-sm">
                        <div class="field-group-label">Stale Drift Warnings (${driftItems.length})</div>
                        ${driftItems.slice(0, 6).map(d => `
                            <div class="telemetry-kv" style="margin-top:0.35rem;">
                                <span class="telemetry-k">${escapeHTML(d.drift_type || 'drift')} (${escapeHTML(d.severity || 'warn')})</span>
                                <span class="telemetry-v">${escapeHTML(d.detail || d.symbol_id || '')}</span>
                            </div>
                        `).join('')}
                    </div>
                ` : '<div class="placeholder-item" style="margin-top:0.5rem;">No stale doc-to-code drift detected.</div>'}
            `;
        } catch (err) {
            kbCoveragePanel.innerHTML = `<div class="error-msg" style="display:block;">${escapeHTML(err.message)}</div>`;
        }
    }

    async function runKbTrace() {
        const target = (kbTraceTarget?.value || '').trim();
        if (!target) {
            showToast('Enter a symbol name, file path, or document title to trace.', 'warning');
            return;
        }
        if (!kbTraceResults) return;

        const { baseProject, branch } = getProjectContext();
        const targetBranch = (kbBranchInput?.value || '').trim() || branch || 'main';
        const workspacePath = (kbWorkspacePath?.value || '').trim() || '.';

        kbTraceResults.innerHTML = '<div class="loader"><i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i> Tracing cross-layer links...</div>';

        try {
            const params = new URLSearchParams({
                target,
                project_id: baseProject,
                branch: targetBranch,
                workspace_path: workspacePath
            });
            const res = await fetch(`/kb/trace?${params.toString()}`);
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'Traceability lookup failed.'));
            const data = await res.json();

            const statusBadge = data.coverage_status === 'documented'
                ? 'success'
                : (data.coverage_status === 'inferred' ? 'warning' : 'danger');

            const codeNodes = data.code_nodes || data.linked_code || [];
            const docChunks = data.doc_chunks || data.linked_docs || [];
            const tests = data.tests || data.linked_tests || [];
            const endpoints = data.linked_endpoints || [];
            const callers = data.callers || [];
            const callees = data.callees || [];

            kbTraceResults.innerHTML = `
                <div class="card-header-action" style="margin-bottom:0.75rem;">
                    <strong>Query: <code class="code-font">${escapeHTML(data.query || target)}</code></strong>
                    <span class="status-tag ${statusBadge}">${escapeHTML(data.coverage_status || 'unknown')}</span>
                </div>
                <div class="two-col-grid">
                    <div class="telemetry-kv">
                        <span class="telemetry-k">Code Symbols (${codeNodes.length})</span>
                        <div class="telemetry-v">${codeNodes.length ? codeNodes.map(n => `<div><code class="code-font">${escapeHTML((n && typeof n === 'object') ? (n.label || n.name || n.qualified_name || n.id || toDisplayText(n)) : n)}</code> <span class="hint">${escapeHTML((n && typeof n === 'object') ? (n.source_file || n.file_path || '') : '')}</span></div>`).join('') : '<span class="placeholder-item">No matching code symbols</span>'}</div>
                    </div>
                    <div class="telemetry-kv">
                        <span class="telemetry-k">Linked Docs (${docChunks.length})</span>
                        <div class="telemetry-v">${docChunks.length ? docChunks.map(c => `<div><strong>${escapeHTML((c && typeof c === 'object') ? (c.heading_breadcrumb || c.title || c.doc_path || c.chunk_id || c.id || toDisplayText(c)) : c)}</strong><div class="hint">${escapeHTML((c && typeof c === 'object') ? String(c.snippet || c.content || '').slice(0, 140) : '')}</div></div>`).join('') : '<span class="placeholder-item">No linked document chunks</span>'}</div>
                    </div>
                    <div class="telemetry-kv">
                        <span class="telemetry-k">Linked Tests (${tests.length})</span>
                        <div class="telemetry-v">${tests.length ? tests.map(t => `<div><code class="code-font">${escapeHTML((t && typeof t === 'object') ? (t.label || t.name || t.title || t.id || toDisplayText(t)) : t)}</code></div>`).join('') : '<span class="placeholder-item">No linked tests</span>'}</div>
                    </div>
                    <div class="telemetry-kv">
                        <span class="telemetry-k">Callers (${callers.length}) &amp; Callees (${callees.length})${endpoints.length ? ` · Endpoints (${endpoints.length})` : ''}</span>
                        <div class="telemetry-v">
                            ${callers.length ? `<div>Callers: ${callers.map(c => `<code class="code-font">${escapeHTML((c && typeof c === 'object') ? (c.label || c.name || c.qualified_name || c.id || toDisplayText(c)) : c)}</code>`).join(', ')}</div>` : ''}
                            ${callees.length ? `<div>Callees: ${callees.map(c => `<code class="code-font">${escapeHTML((c && typeof c === 'object') ? (c.label || c.name || c.qualified_name || c.id || toDisplayText(c)) : c)}</code>`).join(', ')}</div>` : ''}
                            ${endpoints.length ? `<div>Endpoints: ${endpoints.map(ep => `<code class="code-font">${escapeHTML((ep && typeof ep === 'object') ? (ep.label || ep.path || ep.name || ep.id || toDisplayText(ep)) : ep)}</code>`).join(', ')}</div>` : ''}
                            ${!callers.length && !callees.length && !endpoints.length ? '<span class="placeholder-item">No call-graph edges resolved</span>' : ''}
                        </div>
                    </div>
                </div>
            `;
        } catch (err) {
            kbTraceResults.innerHTML = `<div class="error-msg" style="display:block;">${escapeHTML(err.message)}</div>`;
        }
    }

    // =========================================================================
    // 18. Microkernel Plugins Directory (/plugins*)
    // =========================================================================
    async function loadPlugins() {
        if (!pluginsGrid) return;
        pluginsGrid.innerHTML = '<div class="loader"><i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i> Loading microkernel plugins...</div>';

        try {
            const { baseProject } = getProjectContext();
            const res = await fetch(`/plugins?project_id=${encodeURIComponent(baseProject)}&include_disabled=true`);
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'Failed to fetch plugins'));
            const data = await res.json();

            allPlugins = data.plugins || [];
            if (statActivePlugins) {
                statActivePlugins.textContent = `${data.enabled_plugins ?? allPlugins.filter(p => p.enabled).length}/${data.total_plugins ?? allPlugins.length}`;
            }
            const navBadge = document.querySelector('#btnTabPlugins .nav-badge');
            if (navBadge) navBadge.textContent = String(data.total_plugins ?? allPlugins.length);

            renderPluginsGrid();
        } catch (err) {
            pluginsGrid.innerHTML = `<div class="error-msg" style="display:block;">Error loading plugins: ${escapeHTML(err.message)}</div>`;
        }
    }

    function renderPluginsGrid() {
        if (!pluginsGrid) return;
        const q = (pluginSearchInput?.value || '').trim().toLowerCase();

        const filtered = allPlugins.filter(p => {
            if (activePluginCategory !== 'all' && p.category !== activePluginCategory) return false;
            if (!q) return true;
            const hay = [
                p.plugin_id,
                p.name,
                p.description,
                p.category,
                ...(p.capabilities || []).map(c => toDisplayText(c))
            ].join(' ').toLowerCase();
            return hay.includes(q);
        });

        pluginsGrid.innerHTML = '';
        if (filtered.length === 0) {
            pluginsGrid.innerHTML = '<div class="placeholder-item">No plugins match the selected category or search filter.</div>';
            return;
        }

        const isAdmin = userRole === 'admin';

        filtered.forEach(p => {
            const card = document.createElement('div');
            card.className = 'card stack-form';
            card.style.marginTop = '0';

            const caps = (Array.isArray(p.capabilities) ? p.capabilities : []).slice(0, 6);
            const capsHtml = caps.length > 0
                ? `<div class="memory-tags-row">${caps.map(c => `<span class="tag">${escapeHTML(toDisplayText(c))}</span>`).join('')}</div>`
                : '';

            const configSummary = (p.config && typeof p.config === 'object' && Object.keys(p.config).length > 0)
                ? `<div class="hint mono-num">Config: ${escapeHTML(formatDetailsSummary(p.config))}</div>`
                : '';
            const metaSummary = (p.metadata && typeof p.metadata === 'object' && Object.keys(p.metadata).length > 0)
                ? `<div class="hint mono-num">Meta: ${escapeHTML(formatDetailsSummary(p.metadata))}</div>`
                : '';

            const toggleHtml = isAdmin
                ? `<button type="button" class="btn ${p.enabled ? 'btn-secondary' : 'btn-primary'} btn-sm btn-toggle-plugin">
                        ${p.enabled ? '<i class="fa-solid fa-toggle-on" aria-hidden="true"></i> Disable' : '<i class="fa-solid fa-toggle-off" aria-hidden="true"></i> Enable'}
                   </button>`
                : '';

            card.innerHTML = `
                <div class="card-header-action" style="margin-bottom:0.25rem;">
                    <div>
                        <strong style="font-size:0.95rem;">${escapeHTML(p.name || p.plugin_id)}</strong>
                        <div class="hint mono-num">${escapeHTML(p.plugin_id)} · v${escapeHTML(p.version || '1.0.0')}</div>
                    </div>
                    <span class="status-tag ${p.enabled ? 'success' : 'danger'}">${p.enabled ? 'Enabled' : 'Disabled'}</span>
                </div>
                <div style="display:flex; gap:0.4rem; align-items:center; flex-wrap:wrap;">
                    <span class="status-tag info">${escapeHTML(p.category || 'plugin')}</span>
                    <span class="nav-badge mono-num">Priority ${escapeHTML(p.priority ?? 0)}</span>
                </div>
                <p class="info-text">${escapeHTML(p.description || 'Built-in ProjectBrain microkernel extension.')}</p>
                ${capsHtml}
                ${configSummary}
                ${metaSummary}
                ${toggleHtml ? `<div class="form-actions-end">${toggleHtml}</div>` : ''}
            `;

            const toggleBtn = card.querySelector('.btn-toggle-plugin');
            if (toggleBtn) {
                toggleBtn.addEventListener('click', () => togglePluginState(p));
            }
            pluginsGrid.appendChild(card);
        });
    }

    async function togglePluginState(plugin) {
        const { baseProject } = getProjectContext();
        const action = plugin.enabled ? 'disable' : 'enable';
        try {
            const res = await fetch(`/plugins/${encodeURIComponent(plugin.plugin_id)}/${action}?project_id=${encodeURIComponent(baseProject)}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ project_id: baseProject })
            });
            if (!res.ok) throw new Error(await extractErrorMessage(res, `Failed to ${action} plugin.`));
            showToast(`Plugin '${plugin.plugin_id}' ${action}d.`, 'success', 2500);
            await loadPlugins();
        } catch (err) {
            showToast(err.message, 'error');
        }
    }

    async function reloadPlugins() {
        if (btnReloadPlugins) {
            btnReloadPlugins.disabled = true;
            btnReloadPlugins.innerHTML = '<i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i> Reloading...';
        }
        try {
            const res = await fetch('/plugins/reload', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({})
            });
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'Failed to reload plugins.'));
            const data = await res.json();
            const reloadedCount = (data.reloaded_plugins || []).length;
            showToast(`Reloaded plugins (${reloadedCount} updated).`, 'success');
            await loadPlugins();
        } catch (err) {
            showToast(err.message, 'error');
        } finally {
            if (btnReloadPlugins) {
                btnReloadPlugins.disabled = false;
                btnReloadPlugins.innerHTML = '<i class="fa-solid fa-arrows-rotate" aria-hidden="true"></i> Reload Plugins';
            }
        }
    }

    // =========================================================================
    // 19. v3.0 Testing Layer 1 Accessible Sub-Tabs & Endpoints (/v3/*)
    // =========================================================================
    function setupV3SubTabs() {
        const tabList = document.querySelector('#v3TestingTab [role="tablist"]');
        if (!tabList) return;
        const tabs = Array.from(tabList.querySelectorAll('[role="tab"]'));
        const panels = Array.from(document.querySelectorAll('#v3TestingTab [role="tabpanel"]'));

        function activateSubTab(tabEl) {
            const targetPanel = panels.find(p => p.getAttribute('aria-labelledby') === tabEl.id);
            tabs.forEach(t => {
                const active = t === tabEl;
                t.setAttribute('aria-selected', active ? 'true' : 'false');
                t.setAttribute('tabindex', active ? '0' : '-1');
            });
            panels.forEach(p => {
                if (p === targetPanel) {
                    p.removeAttribute('hidden');
                } else {
                    p.setAttribute('hidden', 'until-found');
                }
            });
        }

        tabs.forEach((tab, index) => {
            tab.setAttribute('tabindex', tab.getAttribute('aria-selected') === 'true' ? '0' : '-1');
            tab.addEventListener('click', () => activateSubTab(tab));
            tab.addEventListener('keydown', (e) => {
                let next = index;
                if (e.key === 'ArrowRight') next = (index + 1) % tabs.length;
                else if (e.key === 'ArrowLeft') next = (index - 1 + tabs.length) % tabs.length;
                else if (e.key === 'Home') next = 0;
                else if (e.key === 'End') next = tabs.length - 1;
                else return;
                e.preventDefault();
                tabs[next].focus();
                activateSubTab(tabs[next]);
            });
        });

        panels.forEach(panel => {
            panel.addEventListener('beforematch', () => {
                const ownerId = panel.getAttribute('aria-labelledby');
                const ownerTab = tabs.find(t => t.id === ownerId);
                if (ownerTab) activateSubTab(ownerTab);
            });
        });
    }

    function renderV3StructuredSummary(preEl, summaryHtml) {
        if (!preEl || !preEl.parentElement) return;
        let box = preEl.parentElement.querySelector(`.v3-summary-for-${preEl.id}`);
        if (!box) {
            box = document.createElement('div');
            box.className = `telemetry-panel v3-summary-for-${preEl.id}`;
            preEl.parentElement.insertBefore(box, preEl);
        }
        box.innerHTML = summaryHtml;
    }

    async function runV3TestPlan() {
        const rawFiles = (v3PlanFiles?.value || '').trim();
        const code_diff_files = rawFiles
            ? rawFiles.split(/[\n,]+/).map(s => s.trim()).filter(Boolean)
            : [];
        const version_tag = (v3PlanVersionTag?.value || '').trim() || undefined;
        const { baseProject, branch } = getProjectContext();

        if (btnRunV3Plan) {
            btnRunV3Plan.disabled = true;
            btnRunV3Plan.innerHTML = '<i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i> Planning...';
        }

        try {
            const res = await fetch('/v3/planner/test-dag', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    project_id: baseProject,
                    branch,
                    version_tag,
                    code_diff_files
                })
            });
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'Failed to generate test plan.'));
            const data = await res.json();

            const blast = data.blast_radius || {};
            const stats = data.stats || {};
            const waves = data.dag?.execution_waves || [];
            const impactedList = Array.isArray(blast.impacted_nodes) ? blast.impacted_nodes : [];
            const dagSteps = Array.isArray(data.dag_steps) ? data.dag_steps : (Array.isArray(data.dag?.nodes) ? data.dag.nodes : []);
            renderV3StructuredSummary(v3PlanOutput, `
                <div class="hardware-summary-grid">
                    <div class="telemetry-kv">
                        <span class="telemetry-k">DAG Topology</span>
                        <span class="telemetry-v mono-num">${data.acyclic ? 'Acyclic' : 'Cycle Detected'} · ${stats.total_nodes ?? dagSteps.length ?? 0} nodes · ${stats.total_edges ?? 0} edges</span>
                    </div>
                    <div class="telemetry-kv">
                        <span class="telemetry-k">Blast Radius</span>
                        <span class="telemetry-v mono-num">${impactedList.length} impacted · Depth ${blast.max_depth ?? 4} · Hub Touched: ${blast.core_hub_touched ? 'Yes' : 'No'}</span>
                    </div>
                    <div class="telemetry-kv">
                        <span class="telemetry-k">Execution Waves</span>
                        <span class="telemetry-v mono-num">${waves.length} wave(s) · Cache Hit Ratio: ${round((stats.cache_hit_ratio || 0) * 100, 1)}%</span>
                    </div>
                    <div class="telemetry-kv">
                        <span class="telemetry-k">Intents &amp; Preconditions</span>
                        <span class="telemetry-v mono-num">${stats.total_intents ?? 0} intents · ${stats.precondition_nodes ?? 0} preconditions</span>
                    </div>
                    ${impactedList.length > 0 ? `
                        <div class="telemetry-kv" style="grid-column: span 2;">
                            <span class="telemetry-k">Impacted Nodes</span>
                            <span class="telemetry-v">${escapeHTML(toDisplayText(impactedList.slice(0, 10)))}</span>
                        </div>
                    ` : ''}
                </div>
            `);
            if (v3PlanOutput) v3PlanOutput.textContent = JSON.stringify(data, null, 2);
        } catch (err) {
            if (v3PlanOutput) v3PlanOutput.textContent = `Error: ${err.message}`;
            showToast(err.message, 'error');
        } finally {
            if (btnRunV3Plan) {
                btnRunV3Plan.disabled = false;
                btnRunV3Plan.innerHTML = '<i class="fa-solid fa-play" aria-hidden="true"></i> Generate Test Plan';
            }
        }
    }

    async function lookupV3ScriptCache() {
        const intent_id = (v3CacheIntent?.value || '').trim();
        const platform = v3CachePlatform ? v3CachePlatform.value : 'web';
        const build_version = (v3CacheVersion?.value || '').trim() || '3.0.0';
        const { baseProject } = getProjectContext();

        if (!intent_id) {
            showToast('Enter a Test Intent / Action Signature.', 'warning');
            return;
        }

        if (btnLookupV3Cache) {
            btnLookupV3Cache.disabled = true;
            btnLookupV3Cache.innerHTML = '<i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i> Looking up...';
        }

        try {
            const res = await fetch('/v3/script-cache/lookup', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    project_id: baseProject,
                    intent_id,
                    platform,
                    build_version
                })
            });
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'Script cache lookup failed.'));
            const data = await res.json();
            const entry = data.entry;

            const udcpCmds = Array.isArray(entry?.udcp_commands) ? entry.udcp_commands : [];
            renderV3StructuredSummary(v3CacheOutput, `
                <div class="hardware-summary-grid">
                    <div class="telemetry-kv">
                        <span class="telemetry-k">Cache Status</span>
                        <span class="telemetry-v"><span class="status-tag ${data.cache_hit ? 'success' : 'warning'}">${data.cache_hit ? 'CACHE HIT' : 'CACHE MISS'}</span></span>
                    </div>
                    <div class="telemetry-kv">
                        <span class="telemetry-k">Platform &amp; Build</span>
                        <span class="telemetry-v mono-num">${escapeHTML(platform)} · ${escapeHTML(build_version)}</span>
                    </div>
                    ${entry ? `
                        <div class="telemetry-kv">
                            <span class="telemetry-k">Hits &amp; Fallbacks</span>
                            <span class="telemetry-v mono-num">Hits: ${entry.hit_count ?? 0} · Fallbacks: ${entry.fallback_count ?? 0} (${round((entry.fallback_rate || 0) * 100, 1)}%)${udcpCmds.length ? ` · ${udcpCmds.length} UDCP step(s)` : ''}</span>
                        </div>
                        <div class="telemetry-kv">
                            <span class="telemetry-k">Cache ID</span>
                            <span class="telemetry-v mono-num">${escapeHTML(entry.cache_id || '')}</span>
                        </div>
                    ` : ''}
                </div>
            `);
            if (v3CacheOutput) v3CacheOutput.textContent = JSON.stringify(data, null, 2);
        } catch (err) {
            if (v3CacheOutput) v3CacheOutput.textContent = `Error: ${err.message}`;
            showToast(err.message, 'error');
        } finally {
            if (btnLookupV3Cache) {
                btnLookupV3Cache.disabled = false;
                btnLookupV3Cache.innerHTML = '<i class="fa-solid fa-magnifying-glass" aria-hidden="true"></i> Lookup Cached Script';
            }
        }
    }

    async function queryV3EvidenceWindow() {
        const run_id = (v3EvidenceRunId?.value || '').trim();
        const rawTs = (v3EvidenceCenterTs?.value || '').trim();
        const window_ms = Math.max(0, parseInt(v3EvidenceWindowMs?.value || '5000', 10) || 5000);
        const { baseProject } = getProjectContext();

        if (!run_id || !rawTs) {
            showToast('Execution Run ID and Center Timestamp are required.', 'warning');
            return;
        }

        let center_ts_ms = /^\d+$/.test(rawTs) ? parseInt(rawTs, 10) : Date.parse(rawTs);
        if (Number.isNaN(center_ts_ms)) {
            showToast('Center Timestamp must be epoch milliseconds or a valid ISO timestamp.', 'warning');
            return;
        }

        if (btnQueryV3Evidence) {
            btnQueryV3Evidence.disabled = true;
            btnQueryV3Evidence.innerHTML = '<i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i> Slicing...';
        }

        try {
            const params = new URLSearchParams({
                run_id,
                center_ts_ms: String(center_ts_ms),
                window_ms: String(window_ms),
                project_id: baseProject
            });
            const res = await fetch(`/v3/artifacts/window?${params.toString()}`);
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'Telemetry window query failed.'));
            const data = await res.json();
            const sum = data.summary || {};
            const artifactsList = Array.isArray(data.timeline) ? data.timeline : (Array.isArray(data.artifacts) ? data.artifacts : []);

            renderV3StructuredSummary(v3EvidenceOutput, `
                <div class="hardware-summary-grid">
                    <div class="telemetry-kv">
                        <span class="telemetry-k">Bounded Window</span>
                        <span class="telemetry-v mono-num">${data.window_start_ms ?? ''} .. ${data.window_end_ms ?? ''} (${data.window_ms ?? window_ms} ms)</span>
                    </div>
                    <div class="telemetry-kv">
                        <span class="telemetry-k">Total Artifacts</span>
                        <span class="telemetry-v mono-num">${sum.total_artifacts ?? artifactsList.length} · Video: ${sum.has_video ? 'Yes' : 'No'}</span>
                    </div>
                </div>
            `);
            if (v3EvidenceOutput) v3EvidenceOutput.textContent = JSON.stringify(data, null, 2);
        } catch (err) {
            if (v3EvidenceOutput) v3EvidenceOutput.textContent = `Error: ${err.message}`;
            showToast(err.message, 'error');
        } finally {
            if (btnQueryV3Evidence) {
                btnQueryV3Evidence.disabled = false;
                btnQueryV3Evidence.innerHTML = '<i class="fa-solid fa-wave-square" aria-hidden="true"></i> Slice Evidence Window';
            }
        }
    }

    async function runV3RcaTriage() {
        const stacktrace_or_log = (v3RcaLogInput?.value || '').trim();
        if (!stacktrace_or_log) {
            showToast('Paste a failure log, stacktrace, or assertion diff first.', 'warning');
            return;
        }
        const { baseProject } = getProjectContext();

        if (btnRunV3Rca) {
            btnRunV3Rca.disabled = true;
            btnRunV3Rca.innerHTML = '<i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i> Triaging...';
        }

        try {
            const res = await fetch('/v3/rca/triage', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    project_id: baseProject,
                    stacktrace_or_log
                })
            });
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'RCA triage failed.'));
            const data = await res.json();

            const suspectedCommits = Array.isArray(data.suspected_commits) ? data.suspected_commits : [];
            renderV3StructuredSummary(v3RcaOutput, `
                <div class="hardware-summary-grid">
                    <div class="telemetry-kv">
                        <span class="telemetry-k">Fault Domain &amp; Incident</span>
                        <span class="telemetry-v"><span class="status-tag warning">${escapeHTML(data.fault_domain || 'Unknown')}</span> <code class="code-font">${escapeHTML(data.incident_id || '')}</code></span>
                    </div>
                    <div class="telemetry-kv">
                        <span class="telemetry-k">Deduplication</span>
                        <span class="telemetry-v mono-num">${data.is_duplicate ? 'Duplicate' : 'New Signature'} · Occurrences: ${data.occurrence_count ?? 1}</span>
                    </div>
                    ${data.self_heal_hint ? `
                        <div class="telemetry-kv" style="grid-column: span 2;">
                            <span class="telemetry-k">Self-Heal Hint</span>
                            <span class="telemetry-v">${escapeHTML(data.self_heal_hint)}</span>
                        </div>
                    ` : ''}
                    ${suspectedCommits.length > 0 ? `
                        <div class="telemetry-kv" style="grid-column: span 2;">
                            <span class="telemetry-k">Suspected Commits</span>
                            <span class="telemetry-v">${escapeHTML(toDisplayText(suspectedCommits.slice(0, 6)))}</span>
                        </div>
                    ` : ''}
                </div>
            `);
            if (v3RcaOutput) v3RcaOutput.textContent = JSON.stringify(data, null, 2);
        } catch (err) {
            if (v3RcaOutput) v3RcaOutput.textContent = `Error: ${err.message}`;
            showToast(err.message, 'error');
        } finally {
            if (btnRunV3Rca) {
                btnRunV3Rca.disabled = false;
                btnRunV3Rca.innerHTML = '<i class="fa-solid fa-magnifying-glass-chart" aria-hidden="true"></i> Run Root-Cause Triage';
            }
        }
    }

    // =========================================================================
    // 20. My Profile, Personal API Keys & Active Sessions (All Roles)
    // =========================================================================
    async function loadProfileView() {
        await Promise.all([loadMyProfile(), loadPersonalApiKeys(), loadActiveSessions()]);
    }

    async function loadMyProfile() {
        try {
            const res = await fetch('/auth/me');
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'Failed to load profile.'));
            const data = await res.json();
            const acc = data.account || {};
            currentAccount = acc;
            updateSeedSecurityBanner(acc);

            if (profileUsername) profileUsername.value = acc.username || '';
            if (profileDisplayName) profileDisplayName.value = acc.display_name || '';
            if (profileEmail) profileEmail.value = acc.email || '';
            if (profileAllowedProjects) profileAllowedProjects.value = acc.allowed_projects || '*';
            if (profileRoleBadge) {
                const roleStr = toDisplayText(acc.role, 'reader');
                profileRoleBadge.textContent = roleStr;
                profileRoleBadge.className = `account-role-badge ${escapeHTML(roleStr)}`;
            }

            if (apiKeyRoleScope) {
                const roleRank = { reader: 1, collaborator: 2, admin: 3 };
                const maxRank = roleRank[acc.role] || 1;
                Array.from(apiKeyRoleScope.options).forEach(opt => {
                    if (!opt.value) return;
                    opt.disabled = (roleRank[opt.value] || 1) > maxRank;
                });
            }
        } catch (err) {
            showToast(err.message, 'error');
        }
    }

    async function saveMyProfile(e) {
        e.preventDefault();
        if (btnSaveProfile) btnSaveProfile.disabled = true;
        try {
            const res = await fetch('/auth/me', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    display_name: (profileDisplayName?.value || '').trim(),
                    email: (profileEmail?.value || '').trim()
                })
            });
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'Failed to update profile.'));
            const data = await res.json();
            const acc = data.account || {};
            currentAccount = acc;
            if (userProfileName) userProfileName.textContent = toDisplayText(acc.display_name || acc.username, '');
            if (userAvatarInitials) userAvatarInitials.textContent = computeInitials(acc);
            showToast('Profile updated.', 'success');
        } catch (err) {
            showToast(err.message, 'error');
        } finally {
            if (btnSaveProfile) btnSaveProfile.disabled = false;
        }
    }

    async function submitChangePassword(e) {
        e.preventDefault();
        if (!syncFieldValidationState(newPasswordInput, true)) return;

        const current_password = currentPasswordInput ? currentPasswordInput.value : '';
        const new_password = newPasswordInput ? newPasswordInput.value : '';
        const revoke_other_sessions = Boolean(revokeOtherSessionsCheck?.checked);

        if (btnChangePassword) btnChangePassword.disabled = true;
        try {
            const res = await fetch('/auth/change-password', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    current_password: current_password || null,
                    new_password,
                    revoke_other_sessions
                })
            });
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'Failed to update password.'));

            if (currentPasswordInput) currentPasswordInput.value = '';
            if (newPasswordInput) {
                newPasswordInput.value = '';
                newPasswordInput.dataset.interacted = 'false';
                newPasswordInput.classList.remove('user-invalid-fallback');
                newPasswordInput.setAttribute('aria-invalid', 'false');
            }
            if (currentAccount) {
                currentAccount.has_password = true;
                updateSeedSecurityBanner(currentAccount);
            }
            showToast('Password updated successfully.', 'success');
            await loadActiveSessions();
        } catch (err) {
            showToast(err.message, 'error');
        } finally {
            if (btnChangePassword) btnChangePassword.disabled = false;
        }
    }

    async function loadPersonalApiKeys() {
        if (!personalApiKeysList) return;
        try {
            const res = await fetch('/auth/api-keys');
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'Failed to load API keys.'));
            const data = await res.json();
            const keys = data.api_keys || [];

            personalApiKeysList.innerHTML = '';
            if (keys.length === 0) {
                personalApiKeysList.innerHTML = '<tr><td colspan="7" class="placeholder-item">No personal API keys created yet.</td></tr>';
                return;
            }

            keys.forEach(k => {
                const tr = document.createElement('tr');
                const expStr = formatEpochTimestamp(k.expires_at, 'Never', true);
                const lastUsedStr = formatEpochTimestamp(k.last_used_at, 'Never');
                const revoked = Boolean(k.revoked_at);

                tr.innerHTML = `
                    <th>${escapeHTML(k.name)} ${revoked ? '<span class="status-tag danger">Revoked</span>' : ''}</th>
                    <td class="code-font">${escapeHTML(k.key_prefix || 'pb_pat_...')}</td>
                    <td><span class="account-role-badge ${escapeHTML(k.role_scope || 'reader')}">${escapeHTML(k.role_scope || 'inherit')}</span></td>
                    <td class="code-font">${escapeHTML(k.allowed_projects || '*')}</td>
                    <td class="mono-num">${escapeHTML(expStr)}</td>
                    <td class="mono-num">${escapeHTML(lastUsedStr)}</td>
                    <td>
                        ${revoked ? '<span class="hint">Revoked</span>' : `<button type="button" class="btn btn-danger btn-sm btn-revoke-pat"><i class="fa-solid fa-ban" aria-hidden="true"></i> Revoke</button>`}
                    </td>
                `;

                const revokeBtn = tr.querySelector('.btn-revoke-pat');
                if (revokeBtn) {
                    revokeBtn.addEventListener('click', () => revokePersonalApiKey(k.key_id, k.name));
                }
                personalApiKeysList.appendChild(tr);
            });
        } catch (err) {
            personalApiKeysList.innerHTML = `<tr><td colspan="7" class="error-msg" style="display:table-cell;">${escapeHTML(err.message)}</td></tr>`;
        }
    }

    async function submitCreateApiKey(e) {
        e.preventDefault();
        const name = (apiKeyName?.value || '').trim();
        if (!name) {
            showToast('API key label is required.', 'warning');
            return;
        }
        const rawDays = parseInt(apiKeyExpiryDays?.value || '', 10);
        const expires_in_days = Number.isFinite(rawDays) && rawDays > 0 ? rawDays : null;
        const role_scope = (apiKeyRoleScope?.value || '').trim() || null;
        const allowed_projects = (apiKeyAllowedProjects?.value || '*').trim() || '*';
        const allowed_tools = (apiKeyAllowedTools?.value || '*').trim() || '*';

        if (btnCreateApiKey) btnCreateApiKey.disabled = true;
        try {
            const res = await fetch('/auth/api-keys', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    name,
                    expires_in_days,
                    role_scope,
                    allowed_projects,
                    allowed_tools
                })
            });
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'Failed to mint API key.'));
            const data = await res.json();

            createApiKeyForm?.reset();
            if (apiKeyExpiryDays) apiKeyExpiryDays.value = '90';
            if (apiKeyAllowedProjects) apiKeyAllowedProjects.value = '*';
            if (apiKeyAllowedTools) apiKeyAllowedTools.value = '*';

            await loadPersonalApiKeys();
            showSecretReveal({
                title: `Personal API Key: ${name}`,
                subtitle: 'Copy this Personal Access Token (pb_pat_...) now. For your security, the raw token is never displayed again.',
                secret: data.api_key || ''
            });
        } catch (err) {
            showToast(err.message, 'error');
        } finally {
            if (btnCreateApiKey) btnCreateApiKey.disabled = false;
        }
    }

    async function revokePersonalApiKey(keyId, keyName) {
        const ok = await confirmAction({
            title: 'Revoke Personal API Key',
            message: `Revoke key '${keyName}'? Any CI pipeline or IDE MCP client using this key will immediately lose access.`,
            confirmLabel: 'Revoke Key',
            danger: true
        });
        if (!ok) return;

        try {
            const res = await fetch(`/auth/api-keys/${encodeURIComponent(keyId)}`, { method: 'DELETE' });
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'Failed to revoke API key.'));
            showToast(`Revoked API key '${keyName}'.`, 'success');
            await loadPersonalApiKeys();
        } catch (err) {
            showToast(err.message, 'error');
        }
    }

    async function loadActiveSessions() {
        if (!activeSessionsList) return;
        activeSessionsList.innerHTML = '<div class="loader"><i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i> Loading sessions...</div>';

        try {
            const res = await fetch('/auth/sessions');
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'Failed to load sessions.'));
            const data = await res.json();
            const sessions = data.sessions || [];

            activeSessionsList.innerHTML = '';
            if (sessions.length === 0) {
                activeSessionsList.innerHTML = '<div class="placeholder-item">No active login sessions found.</div>';
                return;
            }

            sessions.forEach(s => {
                const item = document.createElement('div');
                item.className = 'account-item';
                const createdStr = formatEpochTimestamp(s.created_at, 'N/A');
                const expiresStr = formatEpochTimestamp(s.expires_at, 'N/A');

                item.innerHTML = `
                    <div class="account-details">
                        <div class="account-title-row">
                            <span class="account-username mono-num">${escapeHTML(s.ip_address || 'Unknown IP')}</span>
                            ${s.is_current ? '<span class="status-tag success">CURRENT</span>' : ''}
                        </div>
                        <div class="hint">${escapeHTML(s.user_agent || 'Unknown User-Agent')}</div>
                        <div class="memory-meta">
                            <span>Created: ${escapeHTML(createdStr)}</span>
                            <span>Expires: ${escapeHTML(expiresStr)}</span>
                        </div>
                    </div>
                    <div class="account-actions">
                        <button type="button" class="btn btn-danger btn-sm btn-revoke-session">
                            <i class="fa-solid fa-ban" aria-hidden="true"></i> Revoke
                        </button>
                    </div>
                `;

                const revokeBtn = item.querySelector('.btn-revoke-session');
                if (revokeBtn) {
                    revokeBtn.addEventListener('click', () => revokeSingleSession(s.session_id, s.is_current));
                }
                activeSessionsList.appendChild(item);
            });
        } catch (err) {
            activeSessionsList.innerHTML = `<div class="error-msg" style="display:block;">${escapeHTML(err.message)}</div>`;
        }
    }

    async function revokeSingleSession(sessionId, isCurrent) {
        const ok = await confirmAction({
            title: 'Revoke Login Session',
            message: isCurrent
                ? 'This is your current active session. Revoking it will sign you out immediately.'
                : 'Terminate this active login session?',
            confirmLabel: 'Revoke Session',
            danger: true
        });
        if (!ok) return;

        try {
            const res = await fetch(`/auth/sessions/${encodeURIComponent(sessionId)}`, { method: 'DELETE' });
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'Failed to revoke session.'));
            if (isCurrent) {
                setStoredToken('');
                currentAccount = null;
                userRole = 'reader';
                isInitialized = false;
                window.history.pushState({ auth: true }, '', '/login');
                showLoginGate({ preserveAuthUrl: true });
                showToast('Current session revoked.', 'info');
            } else {
                showToast('Session revoked.', 'success');
                await loadActiveSessions();
            }
        } catch (err) {
            showToast(err.message, 'error');
        }
    }

    async function revokeOtherSessions() {
        const ok = await confirmAction({
            title: 'Revoke All Other Sessions',
            message: 'Sign out all other devices and browser sessions except this current session?',
            confirmLabel: 'Revoke Other Sessions',
            danger: true
        });
        if (!ok) return;

        try {
            const res = await fetch('/auth/sessions/revoke-others', { method: 'POST' });
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'Failed to revoke other sessions.'));
            const data = await res.json();
            showToast(`Revoked ${data.revoked_count ?? 0} other session(s).`, 'success');
            await loadActiveSessions();
        } catch (err) {
            showToast(err.message, 'error');
        }
    }

    // =========================================================================
    // 21. Identity & RBAC Administration (Admin Only)
    // =========================================================================
    async function loadAuthAdminView() {
        await Promise.all([
            loadAuthSettings(),
            loadAccounts(),
            loadAvailableTools(),
            loadAuditLogs()
        ]);
    }

    async function loadAuthSettings() {
        try {
            const res = await fetch('/auth/settings');
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'Failed to load auth settings.'));
            const data = await res.json();
            const settings = data.settings || data;
            if (settingAllowRegistration) settingAllowRegistration.checked = Boolean(settings.allow_registration);
            if (settingDefaultRole) settingDefaultRole.value = settings.default_signup_role || 'collaborator';
            if (settingSessionTtl) settingSessionTtl.value = String(settings.session_ttl_hours || 24);
            if (settingRequireRestAuth) settingRequireRestAuth.checked = Boolean(settings.require_rest_auth);
        } catch (err) {
            showToast(err.message, 'error');
        }
    }

    async function saveAuthSettings(e) {
        e.preventDefault();
        if (btnSaveAuthSettings) btnSaveAuthSettings.disabled = true;
        try {
            const payload = {
                allow_registration: Boolean(settingAllowRegistration?.checked),
                default_signup_role: settingDefaultRole?.value || 'collaborator',
                session_ttl_hours: Math.max(1, parseInt(settingSessionTtl?.value || '24', 10) || 24),
                require_rest_auth: Boolean(settingRequireRestAuth?.checked)
            };
            const res = await fetch('/auth/settings', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'Failed to save auth policy.'));
            if (tabAuthRegister) {
                tabAuthRegister.hidden = !payload.allow_registration;
            }
            showToast('Global auth & security policy updated.', 'success');
        } catch (err) {
            showToast(err.message, 'error');
        } finally {
            if (btnSaveAuthSettings) btnSaveAuthSettings.disabled = false;
        }
    }

    async function loadAccounts() {
        if (!authAccountsList) return;
        authAccountsList.innerHTML = '<div class="loader"><i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i> Loading accounts...</div>';

        try {
            const res = await fetch('/auth/accounts');
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'Failed to fetch accounts'));
            const data = await res.json();
            const accounts = data.accounts || [];

            authAccountsList.innerHTML = '';
            if (accounts.length === 0) {
                authAccountsList.innerHTML = '<div class="placeholder-item">No accounts registered yet.</div>';
                return;
            }

            const table = document.createElement('table');
            table.className = 'data-table';
            table.innerHTML = `
                <thead>
                    <tr>
                        <th>Account</th>
                        <th>Role &amp; Status</th>
                        <th>Token</th>
                        <th>Scopes</th>
                        <th>Actions</th>
                    </tr>
                </thead>
                <tbody></tbody>
            `;
            const tbody = table.querySelector('tbody');

            accounts.forEach(acc => {
                const tr = document.createElement('tr');
                const status = (acc.status || 'active').toLowerCase();
                const isSuspended = status === 'suspended';
                const pwBadge = acc.has_password
                    ? '<span class="status-tag success">Password</span>'
                    : '<span class="status-tag warning">Token Only</span>';

                tr.innerHTML = `
                    <th>
                        <div>${escapeHTML(acc.username)}</div>
                        ${acc.display_name ? `<div class="hint">${escapeHTML(acc.display_name)}</div>` : ''}
                        ${acc.email ? `<div class="hint mono-num">${escapeHTML(acc.email)}</div>` : ''}
                    </th>
                    <td>
                        <div style="display:flex; flex-direction:column; gap:0.25rem; align-items:flex-start;">
                            <span class="account-role-badge ${escapeHTML(acc.role)}">${escapeHTML(acc.role)}</span>
                            <span class="status-tag ${isSuspended ? 'danger' : 'success'}">${escapeHTML(status)}</span>
                            ${pwBadge}
                        </div>
                    </td>
                    <td>
                        <div class="account-token-wrapper">
                            <span class="account-token">${escapeHTML(acc.token || '')}</span>
                            <button type="button" class="btn-copy-token" title="Copy Token" aria-label="Copy token for ${escapeHTML(acc.username)}">
                                <i class="fa-solid fa-copy" aria-hidden="true"></i>
                            </button>
                        </div>
                    </td>
                    <td class="code-font" style="font-size:0.75rem;">
                        <div>Projects: <strong>${escapeHTML(acc.allowed_projects || '*')}</strong></div>
                        <div>Tools: <strong>${escapeHTML(acc.allowed_tools || '*')}</strong></div>
                    </td>
                    <td>
                        <div class="action-buttons-group" style="flex-wrap:wrap;">
                            <button type="button" class="btn btn-secondary btn-sm btn-edit-account">Edit</button>
                            <button type="button" class="btn btn-secondary btn-sm btn-rotate-token">Rotate</button>
                            <button type="button" class="btn btn-secondary btn-sm btn-toggle-status" ${acc.username === 'admin' ? 'disabled' : ''}>${isSuspended ? 'Activate' : 'Suspend'}</button>
                            <button type="button" class="btn btn-danger btn-sm btn-delete-account" ${acc.username === 'admin' ? 'disabled' : ''}>Delete</button>
                        </div>
                    </td>
                `;

                const copyBtn = tr.querySelector('.btn-copy-token');
                if (copyBtn) {
                    copyBtn.addEventListener('click', async () => {
                        try {
                            await navigator.clipboard.writeText(acc.token || '');
                            showToast(`Copied token for '${acc.username}'.`, 'success', 2500);
                        } catch (err) {
                            showToast('Copy failed: ' + err.message, 'error');
                        }
                    });
                }

                const editBtn = tr.querySelector('.btn-edit-account');
                if (editBtn) {
                    editBtn.addEventListener('click', () => populateAccountForm(acc));
                }

                const rotateBtn = tr.querySelector('.btn-rotate-token');
                if (rotateBtn) {
                    rotateBtn.addEventListener('click', () => rotateAccountToken(acc.username));
                }

                const statusBtn = tr.querySelector('.btn-toggle-status');
                if (statusBtn && acc.username !== 'admin') {
                    statusBtn.addEventListener('click', () => toggleAccountStatus(acc.username, isSuspended ? 'active' : 'suspended'));
                }

                const deleteBtn = tr.querySelector('.btn-delete-account');
                if (deleteBtn && acc.username !== 'admin') {
                    deleteBtn.addEventListener('click', () => deleteAccount(acc.username));
                }

                tbody.appendChild(tr);
            });

            authAccountsList.appendChild(table);
        } catch (err) {
            authAccountsList.innerHTML = `<div class="error-msg" style="display:block;">Error: ${escapeHTML(err.message)}</div>`;
        }
    }

    function populateAccountForm(acc) {
        const setVal = (id, val) => {
            const el = document.getElementById(id);
            if (el) el.value = val ?? '';
        };
        setVal('authUsername', acc.username);
        setVal('authDisplayName', acc.display_name || '');
        setVal('authEmail', acc.email || '');
        setVal('authPassword', '');
        setVal('authToken', acc.token || '');
        setVal('authRole', acc.role || 'collaborator');
        const statusSel = document.getElementById('authStatus');
        if (statusSel) {
            const norm = (acc.status || 'active').toLowerCase();
            statusSel.value = norm === 'suspended' ? 'disabled' : 'active';
        }
        setVal('authAllowedProjects', acc.allowed_projects || '*');
        setVal('authAllowedTools', acc.allowed_tools || '*');
        updateActiveToolTags();
        showToast(`Loaded '${acc.username}' into editor.`, 'info', 2000);
    }

    async function saveAccount(e) {
        e.preventDefault();
        const username = (document.getElementById('authUsername')?.value || '').trim();
        const display_name = (document.getElementById('authDisplayName')?.value || '').trim() || null;
        const email = (document.getElementById('authEmail')?.value || '').trim() || null;
        const password = document.getElementById('authPassword')?.value || null;
        const token = (document.getElementById('authToken')?.value || '').trim() || null;
        const role = document.getElementById('authRole')?.value || 'collaborator';
        const rawStatus = document.getElementById('authStatus')?.value || 'active';
        const status = rawStatus === 'disabled' ? 'suspended' : rawStatus;
        const allowed_projects = (document.getElementById('authAllowedProjects')?.value || '*').trim() || '*';
        const allowed_tools = (document.getElementById('authAllowedTools')?.value || '*').trim() || '*';

        try {
            const res = await fetch('/auth/accounts', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    username,
                    display_name,
                    email,
                    password: password || null,
                    token,
                    role,
                    status,
                    allowed_projects,
                    allowed_tools
                })
            });
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'Failed to save account.'));
            const result = await res.json();

            document.getElementById('authAccountForm')?.reset();
            const allowedToolsEl = document.getElementById('authAllowedTools');
            const allowedProjectsEl = document.getElementById('authAllowedProjects');
            if (allowedToolsEl) allowedToolsEl.value = '*';
            if (allowedProjectsEl) allowedProjectsEl.value = '*';
            updateActiveToolTags();

            await loadAccounts();
            await loadAuditLogs();
            showToast(`Account '${username}' saved.`, 'success');

            if (!token && result.token) {
                showSecretReveal({
                    title: `Token for ${username}`,
                    subtitle: 'Account token provisioned. Copy or store this credential securely.',
                    secret: result.token
                });
            }
        } catch (err) {
            showToast('Error saving account: ' + err.message, 'error');
        }
    }

    async function rotateAccountToken(username) {
        const ok = await confirmAction({
            title: 'Rotate Account Token',
            message: `Generate a new primary token for '${username}'? The previous primary token will stop working immediately.`,
            confirmLabel: 'Rotate Token',
            danger: true
        });
        if (!ok) return;

        try {
            const res = await fetch(`/auth/accounts/${encodeURIComponent(username)}/rotate-token`, {
                method: 'POST'
            });
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'Failed to rotate token.'));
            const data = await res.json();
            await loadAccounts();
            await loadAuditLogs();
            if (currentAccount && currentAccount.username === username) {
                currentAccount.has_default_token = false;
                updateSeedSecurityBanner(currentAccount);
            }
            showSecretReveal({
                title: `Rotated Token: ${username}`,
                subtitle: 'Primary account token rotated. Copy this token now for MCP clients using account tokens.',
                secret: data.token || ''
            });
        } catch (err) {
            showToast(err.message, 'error');
        }
    }

    async function toggleAccountStatus(username, newStatus) {
        try {
            const res = await fetch(`/auth/accounts/${encodeURIComponent(username)}/status`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ status: newStatus })
            });
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'Failed to update account status.'));
            showToast(`Account '${username}' marked as ${newStatus}.`, 'success');
            await loadAccounts();
            await loadAuditLogs();
        } catch (err) {
            showToast(err.message, 'error');
        }
    }

    async function deleteAccount(username) {
        const ok = await confirmAction({
            title: 'Delete Account',
            message: `Permanently delete account '${username}' and revoke all its active sessions and API keys?`,
            confirmLabel: 'Delete Account',
            danger: true
        });
        if (!ok) return;

        try {
            const res = await fetch(`/auth/accounts/${encodeURIComponent(username)}`, { method: 'DELETE' });
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'Failed to delete account.'));
            showToast(`Account '${username}' deleted.`, 'success');
            await loadAccounts();
            await loadAuditLogs();
        } catch (err) {
            showToast('Error deleting account: ' + err.message, 'error');
        }
    }

    async function loadAvailableTools() {
        const toolsContainer = document.getElementById('authAvailableToolsList');
        if (!toolsContainer) return;

        try {
            const res = await fetch('/auth/tools');
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'Failed to fetch available tools'));
            const data = await res.json();
            const tools = data.tools || [];

            toolsContainer.innerHTML = '';
            if (tools.length === 0) {
                toolsContainer.innerHTML = '<span class="placeholder-item">No tools registered on server.</span>';
                return;
            }

            const makeChip = (toolName, labelText) => {
                const safeTool = toDisplayText(toolName, '');
                const safeLabel = toDisplayText(labelText, safeTool);
                const tag = document.createElement('button');
                tag.type = 'button';
                tag.className = 'tag';
                tag.textContent = safeLabel;
                tag.setAttribute('data-tool', safeTool);
                return tag;
            };

            toolsContainer.appendChild(makeChip('*', '* (All Tools)'));
            tools.forEach(tool => {
                const toolId = (tool && typeof tool === 'object') ? (tool.name || tool.id || toDisplayText(tool)) : toDisplayText(tool);
                toolsContainer.appendChild(makeChip(toolId, toolId));
            });

            updateActiveToolTags();

            toolsContainer.querySelectorAll('[data-tool]').forEach(tag => {
                tag.addEventListener('click', () => {
                    const toolName = tag.getAttribute('data-tool');
                    const input = document.getElementById('authAllowedTools');
                    if (!input) return;
                    const currentVal = input.value.trim();

                    if (toolName === '*') {
                        input.value = '*';
                    } else if (currentVal === '*') {
                        input.value = toolName;
                    } else {
                        let list = currentVal.split(',').map(s => s.trim()).filter(Boolean);
                        if (list.includes(toolName)) {
                            list = list.filter(s => s !== toolName);
                            input.value = list.length > 0 ? list.join(', ') : '*';
                        } else {
                            list.push(toolName);
                            input.value = list.join(', ');
                        }
                    }
                    updateActiveToolTags();
                });
            });
        } catch (err) {
            toolsContainer.innerHTML = `<span class="error-msg" style="display:block;">Error loading tools: ${escapeHTML(err.message)}</span>`;
        }
    }

    function updateActiveToolTags() {
        const input = document.getElementById('authAllowedTools');
        const toolsContainer = document.getElementById('authAvailableToolsList');
        if (!input || !toolsContainer) return;

        const currentVal = input.value.trim();
        const list = currentVal.split(',').map(s => s.trim()).filter(Boolean);

        toolsContainer.querySelectorAll('[data-tool]').forEach(tag => {
            const toolName = tag.getAttribute('data-tool');
            const isSelected = currentVal === '*' ? toolName === '*' : list.includes(toolName);
            tag.style.background = isSelected ? 'var(--brand)' : 'var(--brand-tint)';
            tag.style.color = isSelected ? 'var(--brand-on)' : 'var(--brand)';
        });
    }

    async function loadAuditLogs() {
        if (!authAuditLogsTable) return;
        const eventType = (auditLogEventFilter?.value || '').trim();
        const username = (auditLogUserFilter?.value || '').trim();

        const params = new URLSearchParams({ limit: '100' });
        if (eventType) params.set('event_type', eventType);
        if (username) params.set('username', username);

        try {
            const res = await fetch(`/auth/audit-logs?${params.toString()}`);
            if (!res.ok) throw new Error(await extractErrorMessage(res, 'Failed to fetch audit logs.'));
            const data = await res.json();
            const logs = data.logs || [];

            authAuditLogsTable.innerHTML = '';
            if (logs.length === 0) {
                authAuditLogsTable.innerHTML = '<tr><td colspan="6" class="placeholder-item">No matching security audit events found.</td></tr>';
                return;
            }

            logs.forEach(log => {
                const tr = document.createElement('tr');
                const ts = formatEpochTimestamp(log.created_at, 'N/A');
                const statusClass = log.status === 'success' ? 'success' : (log.status === 'warning' ? 'warning' : 'danger');
                const detailsText = formatDetailsSummary(log.details);
                tr.innerHTML = `
                    <td class="mono-num">${escapeHTML(ts)}</td>
                    <td><span class="status-tag ${statusClass}">${escapeHTML(log.event_type)}</span></td>
                    <td class="code-font">${escapeHTML(log.actor_username || '-')}</td>
                    <td class="code-font">${escapeHTML(log.target_username || '-')}</td>
                    <td class="mono-num">${escapeHTML(log.ip_address || '-')}</td>
                    <td class="code-font" style="font-size:0.75rem;">${escapeHTML(detailsText)}</td>
                `;
                authAuditLogsTable.appendChild(tr);
            });
        } catch (err) {
            authAuditLogsTable.innerHTML = `<tr><td colspan="6" class="error-msg" style="display:block;">${escapeHTML(err.message)}</td></tr>`;
        }
    }

    // =========================================================================
    // 22. Pure Formatting & Parsing Helpers
    // =========================================================================
    function escapeHTML(val) {
        return toDisplayText(val, '').replace(/[&<>'"]/g, tag => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            "'": '&#39;',
            '"': '&quot;'
        }[tag] || tag));
    }

    function safeParseJSON(val) {
        if (!val) return {};
        if (typeof val === 'object') return val;
        if (typeof val === 'string') {
            try {
                const parsed = JSON.parse(val);
                return (parsed && typeof parsed === 'object') ? parsed : {};
            } catch (_) {
                return {};
            }
        }
        return {};
    }

    function round(num, decimals) {
        if (num === null || num === undefined || Number.isNaN(Number(num))) return 0;
        return Number(Math.round(num + 'e' + decimals) + 'e-' + decimals);
    }

    function formatMarkdownLike(mdText) {
        if (!mdText) return '';
        let html = escapeHTML(mdText);
        html = html.replace(/^### (.*$)/gim, '<h3>$1</h3>');
        html = html.replace(/^## (.*$)/gim, '<h2>$1</h2>');
        html = html.replace(/^# (.*$)/gim, '<h1>$1</h1>');
        html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
        html = html.replace(/^\s*-\s+(.*$)/gim, '<li>$1</li>');
        html = html.replace(/(<li>.*<\/li>)/gim, '<ul>$1</ul>');
        html = html.replace(/<\/ul>\s*<ul>/g, '');
        return html;
    }

    // Boot Auth Gate
    checkAuth();
});
