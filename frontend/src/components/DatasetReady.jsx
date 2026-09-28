import { useState } from 'react';

export default function DatasetReady({ dataset }) {
  const [copyStatus, setCopyStatus] = useState('');
  const isDetection = dataset.format === 'coco_reference' || dataset.task_type === 'detection';
  const loadingCode = dataset.loading_code || dataset.huggingface_code;
  const imageCount = dataset.images ?? dataset.image_count;
  const annotationCount = dataset.annotations ?? dataset.annotation_count;

  const copyCode = async () => {
    try {
      await navigator.clipboard.writeText(loadingCode);
      setCopyStatus('Loading code copied');
    } catch {
      setCopyStatus('Clipboard unavailable. Select and copy the code below.');
    }
  };

  return (
    <div style={{ marginTop: '0.75rem', fontSize: '0.85rem' }}>
      <p>
        Dataset ready: <strong>{imageCount}</strong> labeled images
        {isDetection && <> · <strong>{annotationCount}</strong> boxes</>}
        {' · '}Project v{dataset.version}
        {dataset.exported_at && <> · {new Date(dataset.exported_at).toLocaleString()}</>}
      </p>
      <p style={{ color: 'var(--text-secondary)' }}>
        Labels are saved. Training reads the original Volume images; keep those files unchanged.
        Image availability is checked when training reads them.
      </p>
      {isDetection && (
        <p style={{ color: 'var(--text-secondary)' }}>
          Boxes are stored as normalized coordinates. The loader reads image dimensions and materializes
          standard pixel COCO in memory without copying images.
        </p>
      )}
      <code style={{ display: 'block', overflowWrap: 'anywhere' }}>{dataset.export_path}</code>
      {dataset.labeled_table && (
        <p style={{ color: 'var(--text-secondary)' }}>
          Labeled Delta table: <code>{dataset.labeled_table}</code>
        </p>
      )}
      {dataset.labeled_table_error && (
        <p style={{ color: '#e2a03f' }}>
          Labeled Delta table could not be created: {dataset.labeled_table_error}
        </p>
      )}
      {loadingCode && (
        <details style={{ marginTop: '0.75rem' }}>
          <summary style={{ cursor: 'pointer' }}>
            {isDetection ? 'Materialize standard COCO on Databricks' : 'Use with Hugging Face on Databricks'}
          </summary>
          <p>
            Run on compute with read access to the dataset and source Volumes.
            {isDetection ? ' Install Pillow first.' : ' Install datasets and Pillow first.'}
          </p>
          <button className="btn btn-secondary" onClick={copyCode}>Copy loading code</button>
          <span role="status" style={{ marginLeft: '0.5rem' }}>{copyStatus}</span>
          <pre style={{ overflowX: 'auto', padding: '0.75rem', background: 'var(--bg-secondary)' }}>
            {loadingCode}
          </pre>
          <p>
            {isDetection
              ? 'The resulting coco object can be indexed with pycocotools or adapted to your training framework.'
              : 'Create a validation split before training. Vision-language SFT also needs model-specific message formatting.'}
          </p>
        </details>
      )}
    </div>
  );
}
