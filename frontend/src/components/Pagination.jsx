/**
 * Pagination controls for gallery views.
 */

export default function Pagination({ page, totalPages, onPageChange }) {
  if (totalPages <= 1) return null;

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: '1rem',
        padding: '1rem 0',
      }}
    >
      <button
        className="btn-secondary"
        onClick={() => onPageChange(Math.max(0, page - 1))}
        disabled={page === 0}
        style={{ opacity: page === 0 ? 0.4 : 1 }}
      >
        Prev
      </button>
      <span style={{ fontSize: '0.875rem', color: 'var(--text-secondary)' }}>
        Page {page + 1} of {totalPages}
      </span>
      <button
        className="btn-secondary"
        onClick={() => onPageChange(Math.min(totalPages - 1, page + 1))}
        disabled={page >= totalPages - 1}
        style={{ opacity: page >= totalPages - 1 ? 0.4 : 1 }}
      >
        Next
      </button>
    </div>
  );
}
