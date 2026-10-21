/**
 * Create Project page — form to create a new labeling project.
 */

import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  createProject,
  fetchCatalogs,
  fetchSchemas,
  fetchVolumes,
  browseDirectory,
  browseImageCount,
  fetchInferenceDefaults,
  fetchTables,
  fetchTablePreview,
  fetchAppConfig,
  fetchWorkspaceUsers,
} from '../api/client';
import { humanizeApiError } from '../api/errors';
import FilterableSelect from '../components/FilterableSelect';
import UserMultiSelect from '../components/UserMultiSelect';

/** UC paths must be /Volumes/catalog/schema/volume[...]; local paths any non-empty string. */
function isValidSourceVolumePath(path) {
  const p = (path || '').trim();
  if (!p) return false;
  if (p.startsWith('/Volumes/')) {
    const segs = p.split('/').filter(Boolean);
    return segs.length >= 4;
  }
  return true;
}

export default function CreateProject() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const volumeFromBrowser = searchParams.get('volume') || '';
  const tableFromBrowser = searchParams.get('source') === 'table'
    ? (searchParams.get('table') || '')
    : '';
  const tablePrefill = useRef(tableFromBrowser);

  // Form fields
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [taskType, setTaskType] = useState('detection');
  const [classList, setClassList] = useState([]);
  const [classInput, setClassInput] = useState('');
  const [consensusEnabled, setConsensusEnabled] = useState(false);
  const [consensusPanelSize, setConsensusPanelSize] = useState(3);
  const [consensusCoverage, setConsensusCoverage] = useState(100);
  const [consensusQuorum, setConsensusQuorum] = useState(2);
  const [consensusIouThreshold, setConsensusIouThreshold] = useState(0.5);
  const [consensusAgreementThreshold, setConsensusAgreementThreshold] = useState(0.5);
  const [consensusReviewers, setConsensusReviewers] = useState([]);
  const [consensusLabellers, setConsensusLabellers] = useState([]);
  const [workspaceUsers, setWorkspaceUsers] = useState([]);
  const [userDirectoryInfo, setUserDirectoryInfo] = useState(null);
  const [userDirectoryError, setUserDirectoryError] = useState('');
  const [servingEndpoint, setServingEndpoint] = useState('');
  const [workspaceEndpointDefault, setWorkspaceEndpointDefault] = useState('');
  const [samPrompt, setSamPrompt] = useState('');
  const [sourceKind, setSourceKind] = useState(tableFromBrowser ? 'table' : 'volume'); // volume | table
  const [tablesList, setTablesList] = useState([]);
  const [tableName, setTableName] = useState('');
  const [pathColumn, setPathColumn] = useState(searchParams.get('path_column') || 'image_path');
  const [sourceFilter, setSourceFilter] = useState(searchParams.get('filter') || '');
  const [tablePreview, setTablePreview] = useState(null);
  const [warehouseConfigured, setWarehouseConfigured] = useState(true);

  // Volume browser — if arriving from Browse Volumes, split the path into
  // base volume (/Volumes/cat/sch/vol) and any nested subfolder portion.
  const [initialBase, initialSub] = useMemo(() => {
    if (!volumeFromBrowser) return ['/Volumes/', ''];
    const trimmed = volumeFromBrowser.replace(/\/+$/, '');
    if (trimmed.startsWith('/Volumes/')) {
      const segs = trimmed.split('/').filter(Boolean); // ['Volumes','cat','sch','vol', ...]
      if (segs.length > 4) {
        const base = '/' + segs.slice(0, 4).join('/');
        const sub = segs.slice(4).join('/');
        return [base, sub];
      }
    }
    return [trimmed, ''];
  }, [volumeFromBrowser]);

  const [volumeMode, setVolumeMode] = useState('direct');
  const [directPath, setDirectPath] = useState(initialBase);
  const [catalogs, setCatalogs] = useState([]);
  const [schemas, setSchemas] = useState([]);
  const [volumesList, setVolumesList] = useState([]);
  const [catalog, setCatalog] = useState('');
  const [schema, setSchema] = useState('');
  const [volume, setVolume] = useState('');
  const [nestedSubpath, setNestedSubpath] = useState(initialSub);
  const [includeSubfolders, setIncludeSubfolders] = useState(
    searchParams.get('include_subfolders') === 'true',
  );
  const [scanMaxDepth, setScanMaxDepth] = useState(() => {
    const raw = Number(searchParams.get('max_depth'));
    return Number.isFinite(raw) ? Math.max(0, Math.min(8, raw)) : 3;
  });
  const [volumeFolders, setVolumeFolders] = useState([]);
  const [volumeNavLoading, setVolumeNavLoading] = useState(false);
  const [browsing, setBrowsing] = useState(false);
  const [browseResult, setBrowseResult] = useState(null);

  // Submit
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  // Auto-browse when arriving from Browse Volumes with a pre-filled path
  const hasAutoScanned = useRef(false);
  useEffect(() => {
    if (volumeFromBrowser && !hasAutoScanned.current && isValidSourceVolumePath(volumeFromBrowser)) {
      hasAutoScanned.current = true;
      const parsedDepth = Number(scanMaxDepth);
      const depth = Number.isFinite(parsedDepth) ? Math.max(0, Math.min(8, parsedDepth)) : 3;
      browseImageCount(volumeFromBrowser, {
        include_subfolders: includeSubfolders,
        max_depth: depth,
      })
        .then((data) => {
          setBrowseResult({
            imageCount: data.image_count ?? 0,
            truncated: Boolean(data.truncated),
            warning: data.error || '',
          });
        })
        .catch(() => {});
    }
  }, [volumeFromBrowser, includeSubfolders, scanMaxDepth]);

  // Workspace default endpoint is available, but not applied unless the user opts in.
  useEffect(() => {
    let cancelled = false;
    fetchInferenceDefaults()
      .then((data) => {
        if (cancelled) return;
        const d = (data?.default_serving_endpoint || '').trim();
        if (d) setWorkspaceEndpointDefault(d);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  // Load catalogs for picker or table mode
  useEffect(() => {
    if (sourceKind !== 'table' && volumeMode !== 'picker') return;
    if (catalogs.length > 0) return;
    fetchCatalogs().then(setCatalogs).catch(() => {});
  }, [sourceKind, volumeMode, catalogs.length]);

  useEffect(() => {
    fetchAppConfig()
      .then((cfg) => setWarehouseConfigured(Boolean(cfg.sql_warehouse_configured)))
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!consensusEnabled || userDirectoryInfo || userDirectoryError) return;
    fetchWorkspaceUsers()
      .then((directory) => {
        setWorkspaceUsers(directory.users || []);
        setUserDirectoryInfo(directory);
      })
      .catch((err) => setUserDirectoryError(humanizeApiError(err)));
  }, [consensusEnabled, userDirectoryInfo, userDirectoryError]);

  useEffect(() => {
    if (tableFromBrowser) {
      const parts = tableFromBrowser.split('.');
      if (parts.length === 3) setCatalog(parts[0]);
    }
  }, [tableFromBrowser]);

  useEffect(() => {
    setVolume('');
    setVolumesList([]);
    setSchemas([]);
    const prefill = tablePrefill.current;
    const keepSchema = Boolean(prefill && prefill.split('.')[0] === catalog);
    if (!keepSchema) setSchema('');
    if (!catalog) {
      setSchema('');
      return;
    }
    fetchSchemas(catalog)
      .then((list) => {
        setSchemas(list);
        const prefill = tablePrefill.current;
        if (prefill) {
          const parts = prefill.split('.');
          if (parts.length === 3 && parts[0] === catalog) setSchema(parts[1]);
        }
      })
      .catch(() => {});
  }, [catalog]);

  useEffect(() => {
    setVolume('');
    setVolumesList([]);
    setTablesList([]);
    if (!catalog || !schema) {
      setTableName('');
      return;
    }
    if (sourceKind === 'table') {
      fetchTables(catalog, schema)
        .then((list) => {
          setTablesList(list);
          const prefill = tablePrefill.current;
          if (prefill) {
            const parts = prefill.split('.');
            if (parts.length === 3 && parts[0] === catalog && parts[1] === schema) {
              setTableName(parts[2]);
              tablePrefill.current = '';
            }
          }
        })
        .catch(() => setTablesList([]));
    } else {
      setTableName('');
      fetchVolumes(catalog, schema).then(setVolumesList).catch(() => {});
    }
  }, [catalog, schema, sourceKind]);

  const tableFqn = useMemo(() => {
    if (!catalog || !schema || !tableName) return '';
    return `${catalog}.${schema}.${tableName}`;
  }, [catalog, schema, tableName]);

  useEffect(() => {
    setTablePreview(null);
    if (sourceKind !== 'table' || !tableFqn) return;
    let cancelled = false;
    fetchTablePreview(tableFqn)
      .then((data) => {
        if (cancelled) return;
        setTablePreview(data);
        const cols = (data.columns || []).map((c) => c.name).filter(Boolean);
        const fromQuery = searchParams.get('path_column');
        if (fromQuery && cols.includes(fromQuery)) setPathColumn(fromQuery);
        else if (!cols.includes(pathColumn)) {
          if (cols.includes('image_path')) setPathColumn('image_path');
          else if (cols.includes('path')) setPathColumn('path');
          else if (cols.length) setPathColumn(cols[0]);
        }
      })
      .catch(() => {
        if (!cancelled) setTablePreview(null);
      });
    return () => { cancelled = true; };
    // pathColumn omitted: we only default when the table changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourceKind, tableFqn]);

  const volumeBasePath = useMemo(() => {
    if (volumeMode === 'picker') {
      if (!catalog || !schema || !volume) return '';
      return `/Volumes/${catalog}/${schema}/${volume}`;
    }
    return directPath.trim().replace(/\/+$/, '');
  }, [volumeMode, catalog, schema, volume, directPath]);

  const prevVolumeBaseRef = useRef(null);
  useEffect(() => {
    const b = volumeBasePath || '';
    if (prevVolumeBaseRef.current !== null && prevVolumeBaseRef.current !== b) {
      setNestedSubpath('');
    }
    prevVolumeBaseRef.current = b;
  }, [volumeBasePath]);

  const sourceVolume = useMemo(() => {
    if (!volumeBasePath) return '';
    if (!nestedSubpath) return volumeBasePath;
    return `${volumeBasePath.replace(/\/+$/, '')}/${nestedSubpath}`;
  }, [volumeBasePath, nestedSubpath]);

  useEffect(() => {
    setBrowseResult(null);
  }, [sourceVolume, includeSubfolders, scanMaxDepth]);

  const showVolumeFolderNav = useMemo(() => {
    if (sourceKind !== 'volume') return false;
    if (!volumeBasePath) return false;
    if (volumeMode === 'picker') return true;
    return isValidSourceVolumePath(volumeBasePath);
  }, [sourceKind, volumeMode, volumeBasePath]);

  useEffect(() => {
    if (!showVolumeFolderNav) {
      setVolumeFolders([]);
      return;
    }
    let cancelled = false;
    const listPath = nestedSubpath
      ? `${volumeBasePath.replace(/\/+$/, '')}/${nestedSubpath}`
      : volumeBasePath;
    setVolumeNavLoading(true);
    browseDirectory(listPath)
      .then((data) => {
        if (!cancelled) setVolumeFolders(data.folders || []);
      })
      .catch(() => {
        if (!cancelled) setVolumeFolders([]);
      })
      .finally(() => {
        if (!cancelled) setVolumeNavLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [showVolumeFolderNav, volumeBasePath, nestedSubpath]);

  // Browse the selected path
  const handleBrowse = useCallback(async () => {
    if (!sourceVolume) return;
    setBrowsing(true);
    setBrowseResult(null);
    try {
      const parsedDepth = Number(scanMaxDepth);
      const depth = Number.isFinite(parsedDepth) ? Math.max(0, Math.min(8, parsedDepth)) : 3;
      const data = await browseImageCount(sourceVolume, {
        include_subfolders: includeSubfolders,
        max_depth: depth,
      });
      const imageCount = data.image_count ?? 0;
      setBrowseResult({
        imageCount,
        truncated: Boolean(data.truncated),
        warning: data.error || '',
      });
    } catch (e) {
      setBrowseResult({ error: humanizeApiError(e) });
    } finally {
      setBrowsing(false);
    }
  }, [sourceVolume, includeSubfolders, scanMaxDepth]);

  // Add class to list
  const addClass = () => {
    const trimmed = classInput.trim();
    if (trimmed && !classList.includes(trimmed)) {
      setClassList([...classList, trimmed]);
    }
    setClassInput('');
  };

  const removeClass = (cls) => {
    setClassList(classList.filter((c) => c !== cls));
  };

  const submitBlockers = useMemo(() => {
    const parts = [];
    if (!name.trim()) parts.push('enter a project name');
    if (sourceKind === 'table') {
      if (!warehouseConfigured) {
        parts.push('configure SQL_WAREHOUSE_ID on the app to use a Delta table');
      }
      if (!tableFqn) parts.push('choose catalog, schema, and table');
    } else {
      if (!sourceVolume) parts.push('choose a source volume (catalog picker or full direct path)');
      else if (!isValidSourceVolumePath(sourceVolume)) {
        parts.push('use a full Unity Catalog path: /Volumes/catalog/schema/volume');
      }
    }
    if (classList.length === 0) parts.push('add at least one class (type a label and click Add or press Enter)');
    if (consensusEnabled) {
      const panel = Number(consensusPanelSize);
      const quorum = Number(consensusQuorum);
      const coverage = Number(consensusCoverage);
      if (!Number.isInteger(panel) || panel < 1 || panel > 15) parts.push('use a panel size from 1 to 15');
      if (!Number.isInteger(quorum) || quorum < 1 || quorum > panel) parts.push('use a quorum between 1 and the panel size');
      if (!Number.isFinite(coverage) || coverage < 0 || coverage > 100) parts.push('use consensus coverage from 0 to 100');
      if (taskType !== 'classification') {
        const iou = Number(consensusIouThreshold);
        const agreement = Number(consensusAgreementThreshold);
        if (!Number.isFinite(iou) || iou < 0 || iou > 1) parts.push('use an IoU threshold from 0 to 1');
        if (!Number.isFinite(agreement) || agreement < 0 || agreement > 1) parts.push('use an agreement threshold from 0 to 1');
      }
    }
    return parts;
  }, [
    name, sourceKind, warehouseConfigured, tableFqn, sourceVolume, classList.length,
    taskType, consensusEnabled, consensusPanelSize, consensusQuorum, consensusCoverage,
    consensusIouThreshold, consensusAgreementThreshold,
  ]);

  const canSubmit = submitBlockers.length === 0;

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!canSubmit) {
      setError(`Complete the form first: ${submitBlockers.join('; ')}.`);
      return;
    }
    setSubmitting(true);
    setError('');
    try {
      const payload = {
        name: name.trim(),
        description: description.trim(),
        task_type: taskType,
        class_list: classList,
      };
      if (consensusEnabled) {
        payload.consensus_enabled = true;
        payload.consensus_panel_size = Number(consensusPanelSize);
        payload.consensus_coverage_percent = Number(consensusCoverage);
        payload.consensus_quorum = Number(consensusQuorum);
        payload.consensus_iou_threshold = Number(consensusIouThreshold);
        payload.consensus_agreement_threshold = Number(consensusAgreementThreshold);
        payload.consensus_reviewer_emails = consensusReviewers;
        payload.consensus_labeller_emails = consensusLabellers;
      }
      if (sourceKind === 'table') {
        payload.source_type = 'table';
        payload.source_table = tableFqn;
        payload.image_path_column = pathColumn.trim() || 'image_path';
        if (sourceFilter.trim()) payload.source_filter = sourceFilter.trim();
      } else {
        payload.source_type = 'volume';
        payload.source_volume = sourceVolume;
        payload.scan_include_subfolders = includeSubfolders;
        const parsedDepth = Number(scanMaxDepth);
        payload.scan_max_depth = Number.isFinite(parsedDepth)
          ? Math.max(0, Math.min(8, parsedDepth))
          : 3;
      }
      if (servingEndpoint.trim()) {
        payload.serving_endpoint = servingEndpoint.trim();
      }
      const epConfig = {};
      if (taskType !== 'classification') {
        epConfig.adapter = 'sam31';
      }
      if (samPrompt.trim() && taskType !== 'classification') {
        epConfig.sam_text_prompt = samPrompt.trim();
      }
      if (Object.keys(epConfig).length > 0) {
        payload.endpoint_config = epConfig;
      }
      const project = await createProject(payload);
      navigate(`/projects/${project.id}`);
    } catch (err) {
      setError(humanizeApiError(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div style={{ maxWidth: 640, margin: '0 auto' }}>
      <h1 style={{ fontSize: '1.75rem', fontWeight: 700, marginBottom: '0.25rem' }}>
        New Project
      </h1>
      <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', marginBottom: '2rem' }}>
        Set up a labeling project from a UC Volume of images or a Delta table of image paths.
        Model-assisted pre-labeling is optional — you can label from scratch.
      </p>

      <form onSubmit={handleSubmit}>
        {/* Project name */}
        <div style={{ marginBottom: '1.25rem' }}>
          <label style={labelStyle}>Project Name *</label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Traffic Signs v1"
            style={inputStyle}
            required
          />
        </div>

        {/* Description */}
        <div style={{ marginBottom: '1.25rem' }}>
          <label style={labelStyle}>Description</label>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Optional project description..."
            rows={2}
            style={{ ...inputStyle, resize: 'vertical' }}
          />
        </div>

        {/* Task type */}
        <div style={{ marginBottom: '1.25rem' }}>
          <label style={labelStyle}>Task Type *</label>
          <div style={{ display: 'flex', gap: '0.75rem' }}>
            {['classification', 'detection', 'segmentation'].map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => setTaskType(t)}
                style={{
                  flex: 1,
                  padding: '0.6rem 1rem',
                  borderRadius: 8,
                  border: '1px solid var(--border-color)',
                  background: taskType === t ? 'rgba(66, 153, 224, 0.15)' : 'var(--bg-card)',
                  color: taskType === t ? 'var(--accent-blue)' : 'var(--text-secondary)',
                  fontWeight: taskType === t ? 600 : 400,
                  cursor: 'pointer',
                  fontSize: '0.85rem',
                  textTransform: 'capitalize',
                }}
              >
                {t}
              </button>
            ))}
          </div>
        </div>

        {/* Classes — required for labeling, independent of any model */}
        <div style={{ marginBottom: '1.25rem' }}>
          <label style={labelStyle}>Classes * ({classList.length})</label>
          <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', margin: '0 0 0.5rem' }}>
            Labels you will apply by hand. A model is not required.
          </p>
          <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.5rem' }}>
            <input
              type="text"
              value={classInput}
              onChange={(e) => setClassInput(e.target.value)}
              placeholder="Type a class name and press Enter"
              style={{ ...inputStyle, flex: 1 }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  addClass();
                }
              }}
            />
            <button
              type="button"
              onClick={addClass}
              className="btn-secondary"
              style={{ padding: '0.4rem 0.75rem', whiteSpace: 'nowrap' }}
            >
              Add
            </button>
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.35rem' }}>
            {classList.map((cls, i) => (
              <span
                key={cls}
                className="badge badge-blue"
                style={{ display: 'inline-flex', alignItems: 'center', gap: '0.35rem', padding: '0.25rem 0.6rem' }}
              >
                <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', fontWeight: 700 }}>{i + 1}</span>
                {cls}
                <button
                  type="button"
                  onClick={() => removeClass(cls)}
                  style={{ background: 'none', border: 'none', color: 'inherit', cursor: 'pointer', padding: 0, fontSize: '0.85rem', lineHeight: 1 }}
                >
                  &#x2715;
                </button>
              </span>
            ))}
          </div>
        </div>

        <div className="card" style={{ marginBottom: '1.25rem', padding: '1rem' }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontWeight: 600 }}>
              <input
                type="checkbox"
                checked={consensusEnabled}
                onChange={(e) => setConsensusEnabled(e.target.checked)}
              />
              Enable blind multi-labeller consensus
            </label>
            <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', margin: '0.4rem 0 0' }}>
              Each selected image is labelled independently; matching results are resolved by quorum.
            </p>
            {consensusEnabled && (
              <div style={{ marginTop: '0.75rem' }}>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '0.5rem' }}>
                  <label style={labelStyle}>
                    Panel size
                    <input
                      type="number" min="1" max="15"
                      value={consensusPanelSize}
                      onChange={(e) => setConsensusPanelSize(e.target.value)}
                      style={{ ...inputStyle, marginTop: '0.25rem' }}
                    />
                  </label>
                  <label style={labelStyle}>
                    Quorum
                    <input
                      type="number" min="1" max={consensusPanelSize}
                      value={consensusQuorum}
                      onChange={(e) => setConsensusQuorum(e.target.value)}
                      style={{ ...inputStyle, marginTop: '0.25rem' }}
                    />
                  </label>
                  <label style={labelStyle}>
                    Coverage %
                    <input
                      type="number" min="0" max="100"
                      value={consensusCoverage}
                      onChange={(e) => setConsensusCoverage(e.target.value)}
                      style={{ ...inputStyle, marginTop: '0.25rem' }}
                    />
                  </label>
                </div>
                {taskType !== 'classification' && (
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '0.5rem', marginBottom: '0.5rem' }}>
                    <label style={labelStyle}>
                      IoU threshold
                      <input
                        type="number" min="0" max="1" step="0.05"
                        value={consensusIouThreshold}
                        onChange={(e) => setConsensusIouThreshold(e.target.value)}
                        style={{ ...inputStyle, marginTop: '0.25rem' }}
                      />
                    </label>
                    <label style={labelStyle}>
                      Agreement threshold
                      <input
                        type="number" min="0" max="1" step="0.05"
                        value={consensusAgreementThreshold}
                        onChange={(e) => setConsensusAgreementThreshold(e.target.value)}
                        style={{ ...inputStyle, marginTop: '0.25rem' }}
                      />
                    </label>
                  </div>
                )}
                {Number(consensusPanelSize) % 2 === 0 && (
                  <div style={{ fontSize: '0.72rem', color: '#f59e0b', marginBottom: '0.5rem' }}>
                    Odd panel sizes reduce tied votes.
                  </div>
                )}
                <div style={{
                  marginTop: '0.75rem', padding: '0.75rem',
                  border: '1px solid var(--border-color)', borderRadius: 8,
                  background: 'var(--bg-secondary)',
                }}>
                  <div style={{ fontWeight: 600, fontSize: '0.85rem', marginBottom: '0.2rem' }}>
                    Project roles
                  </div>
                  <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginBottom: '0.75rem' }}>
                    Databricks App access remains controlled in the App Share dialog. Only active users returned by Databricks can be selected here.
                  </div>
                  {userDirectoryError && (
                    <div style={{ fontSize: '0.75rem', color: '#ef4444', marginBottom: '0.6rem' }}>
                      Could not load Databricks users: {userDirectoryError}
                    </div>
                  )}
                  {userDirectoryInfo?.warning && (
                    <div style={{ fontSize: '0.72rem', color: 'var(--status-warning)', marginBottom: '0.6rem' }}>
                      {userDirectoryInfo.warning}
                    </div>
                  )}
                  <label style={{ ...labelStyle, marginBottom: '0.75rem' }}>
                    Reviewers
                    <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', fontWeight: 400, margin: '0.15rem 0 0.35rem' }}>
                      Resolve disagreements and see quality analytics. The project owner is always a reviewer.
                    </div>
                    <UserMultiSelect
                      users={workspaceUsers}
                      selected={consensusReviewers}
                      onChange={setConsensusReviewers}
                      exclude={consensusLabellers}
                      placeholder="Search reviewers…"
                    />
                  </label>
                  <label style={labelStyle}>
                    Labellers
                    <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', fontWeight: 400, margin: '0.15rem 0 0.35rem' }}>
                      Submit independent blind votes. Leave empty to allow every App user who is not a reviewer.
                    </div>
                    <UserMultiSelect
                      users={workspaceUsers}
                      selected={consensusLabellers}
                      onChange={setConsensusLabellers}
                      exclude={consensusReviewers}
                      placeholder="Search labellers (optional)…"
                    />
                  </label>
                </div>
              </div>
            )}
        </div>

        {/* Image source */}
        <div style={{ marginBottom: '1.25rem' }}>
          <label style={labelStyle}>Image source *</label>
          <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.75rem' }}>
            {[{ id: 'volume', label: 'UC Volume' }, { id: 'table', label: 'Delta table' }].map((opt) => (
              <button
                key={opt.id}
                type="button"
                onClick={() => setSourceKind(opt.id)}
                style={{
                  padding: '0.35rem 0.75rem',
                  borderRadius: 6,
                  border: '1px solid var(--border-color)',
                  background: sourceKind === opt.id ? 'rgba(66, 153, 224, 0.15)' : 'var(--bg-card)',
                  color: sourceKind === opt.id ? 'var(--accent-blue)' : 'var(--text-secondary)',
                  fontWeight: sourceKind === opt.id ? 600 : 400,
                  cursor: 'pointer',
                  fontSize: '0.8rem',
                }}
              >
                {opt.label}
              </button>
            ))}
          </div>
          {sourceKind === 'table' && !warehouseConfigured && (
            <div style={{ fontSize: '0.8rem', color: '#e2a03f', marginBottom: '0.75rem' }}>
              Requires a SQL warehouse on the app (<code>SQL_WAREHOUSE_ID</code>). Volume source still works without it.
            </div>
          )}
        </div>

        {/* Source volume */}
        {sourceKind === 'volume' && (
        <div style={{ marginBottom: '1.25rem' }}>
          <label style={labelStyle}>Source Volume *</label>

          {/* Mode toggle */}
          <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.75rem' }}>
            {['direct', 'picker'].map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => setVolumeMode(m)}
                style={{
                  padding: '0.35rem 0.75rem',
                  borderRadius: 6,
                  border: '1px solid var(--border-color)',
                  background: volumeMode === m ? 'rgba(66, 153, 224, 0.15)' : 'var(--bg-card)',
                  color: volumeMode === m ? 'var(--accent-blue)' : 'var(--text-secondary)',
                  fontWeight: volumeMode === m ? 600 : 400,
                  cursor: 'pointer',
                  fontSize: '0.8rem',
                }}
              >
                {m === 'direct' ? 'Direct Path' : 'Catalog Picker'}
              </button>
            ))}
          </div>

          {volumeMode === 'direct' ? (
            <div>
              <input
                type="text"
                value={directPath}
                onChange={(e) => setDirectPath(e.target.value)}
                placeholder="/Volumes/catalog/schema/volume"
                style={inputStyle}
              />
              <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: '0.35rem' }}>
                Enter the volume mount path (no trailing subfolder required). When the path is valid, use the folder
                navigator below to nest into a subfolder—same as Catalog Picker.
              </div>
            </div>
          ) : (
            <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
              <div style={{ flex: 1, minWidth: 120 }}>
                <FilterableSelect
                  options={catalogs}
                  value={catalog}
                  onChange={setCatalog}
                  placeholder="Catalog..."
                />
              </div>
              <div style={{ flex: 1, minWidth: 120 }}>
                <FilterableSelect
                  options={schemas}
                  value={schema}
                  onChange={setSchema}
                  placeholder="Schema..."
                  disabled={!catalog}
                />
              </div>
              <div style={{ flex: 1, minWidth: 120 }}>
                <FilterableSelect
                  options={volumesList}
                  value={volume}
                  onChange={setVolume}
                  placeholder="Volume..."
                  disabled={!schema}
                />
              </div>
            </div>
          )}

          <div style={{ marginTop: '0.75rem', display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '0.75rem' }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.85rem', color: 'var(--text-primary)' }}>
              <input
                type="checkbox"
                checked={includeSubfolders}
                onChange={(e) => setIncludeSubfolders(e.target.checked)}
              />
              Include subfolders
            </label>
            {includeSubfolders && (
              <label style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.85rem', color: 'var(--text-primary)' }}>
                Max depth
                <input
                  type="number"
                  min={0}
                  max={8}
                  value={scanMaxDepth}
                  onChange={(e) => setScanMaxDepth(e.target.value)}
                  style={{ width: 56, padding: '0.2rem 0.35rem' }}
                />
              </label>
            )}
            <button
              type="button"
              onClick={handleBrowse}
              disabled={browsing || !sourceVolume}
              className="btn-secondary"
              style={{ fontSize: '0.8rem', padding: '0.35rem 0.75rem' }}
            >
              {browsing ? 'Scanning...' : 'Preview Volume'}
            </button>
            {browseResult && !browseResult.error && (
              <span style={{ fontSize: '0.8rem', color: 'var(--status-success)' }}>
                {browseResult.imageCount} images found
                {includeSubfolders ? ' (including nested folders)' : ''}
                {browseResult.truncated && ' (count capped)'}
              </span>
            )}
            {browseResult?.warning && (
              <span style={{ fontSize: '0.8rem', color: 'var(--status-error)' }}>
                {browseResult.warning}
              </span>
            )}
            {browseResult?.error && (
              <span style={{ fontSize: '0.8rem', color: 'var(--status-error)' }}>
                {browseResult.error}
              </span>
            )}
          </div>

          {showVolumeFolderNav && (
            <div
              style={{
                marginTop: '0.75rem',
                padding: '0.75rem',
                background: 'var(--bg-card)',
                border: '1px solid var(--border-color)',
                borderRadius: 8,
              }}
            >
              <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: '0.5rem' }}>
                Subfolder (optional) — click a folder to nest; project uses the resolved path shown below.
              </div>
              {volumeNavLoading ? (
                <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>Loading folders…</div>
              ) : (
                <>
                  <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '0.25rem', marginBottom: volumeFolders.length ? '0.65rem' : 0 }}>
                    {['Volume root', ...(nestedSubpath ? nestedSubpath.split('/') : [])].map((crumb, i) => (
                      <span key={`${crumb}-${i}`} style={{ display: 'flex', alignItems: 'center' }}>
                        {i > 0 && <span style={{ color: 'var(--text-muted)', margin: '0 0.2rem' }}>/</span>}
                        <button
                          type="button"
                          onClick={() => {
                            if (i === 0) setNestedSubpath('');
                            else {
                              const parts = nestedSubpath.split('/');
                              setNestedSubpath(parts.slice(0, i).join('/'));
                            }
                          }}
                          style={{
                            background: i === (nestedSubpath ? nestedSubpath.split('/').length : 0) ? 'rgba(66, 153, 224, 0.12)' : 'var(--bg-input)',
                            border: '1px solid var(--border-color)',
                            borderRadius: 6,
                            padding: '0.25rem 0.5rem',
                            fontSize: '0.75rem',
                            color: 'var(--accent-blue)',
                            cursor: 'pointer',
                          }}
                        >
                          {crumb}
                        </button>
                      </span>
                    ))}
                  </div>
                  {volumeFolders.length > 0 ? (
                    <div
                      style={{
                        display: 'grid',
                        gridTemplateColumns: 'repeat(auto-fill, minmax(100px, 1fr))',
                        gap: '0.5rem',
                      }}
                    >
                      {volumeFolders.map((folder) => (
                        <button
                          key={folder.name}
                          type="button"
                          onClick={() =>
                            setNestedSubpath(nestedSubpath ? `${nestedSubpath}/${folder.name}` : folder.name)
                          }
                          style={{
                            background: 'var(--bg-input)',
                            border: '1px solid var(--border-color)',
                            borderRadius: 8,
                            padding: '0.5rem 0.35rem',
                            cursor: 'pointer',
                            textAlign: 'center',
                            fontSize: '0.78rem',
                            color: 'var(--text-primary)',
                          }}
                        >
                          <span style={{ fontSize: '1.1rem', display: 'block', marginBottom: '0.2rem' }}>&#x1F4C2;</span>
                          {folder.name}
                        </button>
                      ))}
                    </div>
                  ) : (
                    <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                      {includeSubfolders
                        ? 'No subfolders in this location. Images in this folder will still be included.'
                        : 'No subfolders here. Enable Include subfolders to scan nested directories, or pick a folder that contains images directly.'}
                    </div>
                  )}
                </>
              )}
            </div>
          )}

          {sourceVolume && (
            <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.35rem' }}>
              {sourceVolume}
            </div>
          )}
        </div>
        )}

        {sourceKind === 'table' && (
          <div style={{ marginBottom: '1.25rem' }}>
            <label style={labelStyle}>Source table *</label>
            <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
              <div style={{ flex: 1, minWidth: 120 }}>
                <FilterableSelect
                  options={catalogs}
                  value={catalog}
                  onChange={setCatalog}
                  placeholder="Catalog..."
                />
              </div>
              <div style={{ flex: 1, minWidth: 120 }}>
                <FilterableSelect
                  options={schemas}
                  value={schema}
                  onChange={setSchema}
                  placeholder="Schema..."
                  disabled={!catalog}
                />
              </div>
              <div style={{ flex: 1, minWidth: 120 }}>
                <FilterableSelect
                  options={tablesList}
                  value={tableName}
                  onChange={setTableName}
                  placeholder="Table..."
                  disabled={!schema}
                />
              </div>
            </div>
            <div style={{ marginTop: '0.75rem' }}>
              <label style={labelStyle}>Image path column</label>
              {tablePreview?.columns?.length ? (
                <FilterableSelect
                  options={tablePreview.columns.map((c) => c.name).filter(Boolean)}
                  value={pathColumn}
                  onChange={setPathColumn}
                  placeholder="image_path"
                />
              ) : (
                <input
                  type="text"
                  value={pathColumn}
                  onChange={(e) => setPathColumn(e.target.value)}
                  placeholder="image_path"
                  style={inputStyle}
                />
              )}
            </div>
            <div style={{ marginTop: '0.75rem' }}>
              <label style={labelStyle}>Filter (optional WHERE)</label>
              <input
                type="text"
                value={sourceFilter}
                onChange={(e) => setSourceFilter(e.target.value)}
                placeholder="split = 'train'"
                style={inputStyle}
              />
              <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: '0.35rem' }}>
                Simple predicate only. Typical workflow: land files in a volume, Auto Loader writes this table, then create the project from the table. New Auto Loader rows are picked up with Actions → Sync new table rows. Paths may be <code>dbfs:/Volumes/...</code> or <code>/Volumes/...</code>; bytes still load from the volume.
                {tablePreview?.row_count != null && (
                  <> Catalog reports {tablePreview.row_count} rows.</>
                )}
              </div>
            </div>
            {tableFqn && (
              <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.35rem' }}>
                {tableFqn}
              </div>
            )}
          </div>
        )}

        {/* Optional model assist — not needed to create or label */}
        <details
          style={{
            marginBottom: '1.5rem',
            padding: '1.25rem',
            background: 'var(--bg-card)',
            border: '1px solid var(--border-color)',
            borderRadius: 12,
          }}
        >
          <summary
            style={{
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: '0.5rem',
              flexWrap: 'wrap',
            }}
          >
            <span style={{ fontSize: '1.05rem', fontWeight: 700 }}>Model-assisted pre-labeling</span>
            <span
              className="badge"
              style={{
                fontSize: '0.7rem',
                fontWeight: 600,
                padding: '0.15rem 0.45rem',
                background: 'var(--bg-input)',
                color: 'var(--text-secondary)',
                border: '1px solid var(--border-color)',
              }}
            >
              Optional
            </span>
            <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)', fontWeight: 400 }}>
              Expand to attach an endpoint
            </span>
          </summary>
          <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', margin: '0.75rem 0 1.25rem' }}>
            Skip this to create a project and label images yourself. You can attach an endpoint later
            on the project page and run pre-labeling there.
            {taskType !== 'classification' && (
              <> SAM 3.1 can draft boxes (detection) or masks (segmentation) when configured.</>
            )}
          </p>

          {taskType !== 'classification' && (
            <div style={{ marginBottom: '1rem' }}>
              <label style={labelStyle}>SAM Text Prompt</label>
              <textarea
                value={samPrompt}
                onChange={(e) => setSamPrompt(e.target.value)}
                placeholder={classList.length > 0 ? `Default: "${classList.join('. ')}"` : 'e.g. "car. person. traffic sign"'}
                rows={2}
                style={{ ...inputStyle, resize: 'vertical' }}
              />
              <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>
                Text prompt sent to SAM 3.1. Leave blank to auto-generate from the class list (classes joined with ". ").
              </div>
            </div>
          )}

          <div>
            <label style={labelStyle}>Model Serving Endpoint</label>
            <input
              type="text"
              value={servingEndpoint}
              onChange={(e) => setServingEndpoint(e.target.value)}
              placeholder="Leave blank to label without a model"
              style={inputStyle}
            />
            <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>
              Optional. Databricks Model Serving endpoint
              {taskType !== 'classification' ? ' running SAM 3.1' : ''}
              . Add it as an App resource with &quot;Can query&quot; permission if you use it.
            </div>
            {workspaceEndpointDefault && servingEndpoint !== workspaceEndpointDefault && (
              <button
                type="button"
                className="btn-secondary"
                onClick={() => setServingEndpoint(workspaceEndpointDefault)}
                style={{ marginTop: '0.5rem', fontSize: '0.75rem', padding: '0.3rem 0.65rem' }}
              >
                Use workspace default: {workspaceEndpointDefault}
              </button>
            )}
          </div>
        </details>

        {/* Error */}
        {error && (
          <div style={{
            background: 'rgba(255, 50, 50, 0.1)',
            border: '1px solid rgba(255, 50, 50, 0.3)',
            borderRadius: 8,
            padding: '0.75rem 1rem',
            marginBottom: '1rem',
            color: '#ff6b6b',
            fontSize: '0.85rem',
          }}>
            {error}
          </div>
        )}

        {/* Submit */}
        <div style={{ marginTop: '1.5rem' }}>
          <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap', alignItems: 'center' }}>
            <button
              type="submit"
              disabled={submitting || !canSubmit}
              className="btn-primary"
              style={{ padding: '0.6rem 2rem' }}
            >
              {submitting ? 'Creating...' : 'Create Project'}
            </button>
            <button
              type="button"
              onClick={() => navigate('/projects')}
              className="btn-secondary"
              style={{ padding: '0.6rem 1.5rem' }}
            >
              Cancel
            </button>
          </div>
          {!canSubmit && !submitting && (
            <p
              style={{
                marginTop: '0.65rem',
                fontSize: '0.8rem',
                color: 'var(--text-muted)',
                lineHeight: 1.45,
              }}
            >
              <strong style={{ color: 'var(--text-secondary)' }}>Create is disabled until:</strong>{' '}
              {submitBlockers.join(' · ')}.
            </p>
          )}
        </div>
      </form>
    </div>
  );
}

const labelStyle = {
  display: 'block',
  fontSize: '0.8rem',
  fontWeight: 500,
  color: 'var(--text-secondary)',
  marginBottom: '0.35rem',
};

const inputStyle = {
  width: '100%',
  padding: '0.5rem 0.75rem',
  background: 'var(--bg-input)',
  color: 'var(--text-primary)',
  border: '1px solid var(--border-color)',
  borderRadius: 6,
  fontSize: '0.85rem',
};
