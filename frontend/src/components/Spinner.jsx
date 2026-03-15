export default function Spinner({ size = 32, label = 'Loading...' }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '0.75rem', padding: '2rem' }}>
      <div
        style={{
          width: size,
          height: size,
          border: '3px solid var(--border-color)',
          borderTopColor: 'var(--accent-teal)',
          borderRadius: '50%',
          animation: 'spin 0.8s linear infinite',
        }}
      />
      {label && (
        <span style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>{label}</span>
      )}
    </div>
  );
}
