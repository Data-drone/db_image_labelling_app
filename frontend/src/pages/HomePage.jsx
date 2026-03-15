/**
 * Home page - Welcome screen with quick-start cards.
 * Mirrors the Streamlit app.py home page.
 */

import { useNavigate } from 'react-router-dom';

export default function HomePage() {
  const navigate = useNavigate();

  const cards = [
    {
      step: 'Step 1',
      title: 'Explore Datasets',
      desc: 'Browse your image datasets with a gallery view, bounding-box overlays, and class/tag filters.',
      action: () => navigate('/explorer'),
      label: 'Open Dataset Explorer',
    },
    {
      step: 'Step 2',
      title: 'Label Images',
      desc: 'Annotate images with classification labels, bounding boxes, or polygons. Supports keyboard shortcuts and autosave.',
      action: () => navigate('/labeling'),
      label: 'Open Labeling',
    },
    {
      step: 'Step 3',
      title: 'Track Progress',
      desc: 'View dataset statistics, class distributions, labeling progress, and confidence histograms.',
      action: () => navigate('/dashboard'),
      label: 'Open Dashboard',
    },
  ];

  return (
    <div>
      <h1
        style={{
          fontSize: '2rem',
          fontWeight: 700,
          marginBottom: '0.5rem',
          display: 'flex',
          alignItems: 'center',
          gap: '0.75rem',
        }}
      >
        <span
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: 44,
            height: 44,
            borderRadius: 10,
            background: 'linear-gradient(135deg, var(--accent-blue), var(--accent-blue-dark))',
            fontSize: '1.3rem',
          }}
        >
          CV
        </span>
        CV Dataset Explorer
      </h1>
      <p
        style={{
          color: 'var(--text-secondary)',
          maxWidth: 700,
          marginBottom: '2rem',
          lineHeight: 1.7,
        }}
      >
        Browse image datasets, explore them with filters and bounding-box overlays,
        label images, and track annotation progress -- all from a single interface.
      </p>

      <div
        style={{
          borderTop: '1px solid var(--border-color)',
          paddingTop: '1.5rem',
          marginBottom: '2rem',
        }}
      />

      {/* Quick-start cards */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
          gap: '1.25rem',
          marginBottom: '2rem',
        }}
      >
        {cards.map((card, i) => (
          <div key={i} className="card" style={{ display: 'flex', flexDirection: 'column' }}>
            <div
              style={{
                fontSize: '0.75rem',
                fontWeight: 600,
                color: 'var(--accent-blue)',
                textTransform: 'uppercase',
                letterSpacing: '0.05em',
                marginBottom: '0.5rem',
              }}
            >
              {card.step}
            </div>
            <h3 style={{ fontSize: '1.1rem', fontWeight: 600, marginBottom: '0.5rem' }}>
              {card.title}
            </h3>
            <p
              style={{
                fontSize: '0.85rem',
                color: 'var(--text-secondary)',
                flex: 1,
                marginBottom: '1rem',
              }}
            >
              {card.desc}
            </p>
            <button
              className="btn-primary"
              onClick={card.action}
              style={{ alignSelf: 'flex-start' }}
            >
              {card.label}
            </button>
          </div>
        ))}
      </div>

      <div style={{ borderTop: '1px solid var(--border-color)', paddingTop: '1rem' }}>
        <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
          Built with React + FastAPI + SQLAlchemy | Deploys as a Databricks App
        </p>
      </div>
    </div>
  );
}
