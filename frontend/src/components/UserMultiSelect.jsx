import { useMemo, useState } from 'react';

export default function UserMultiSelect({
  users = [],
  selected = [],
  onChange,
  exclude = [],
  placeholder = 'Search active Databricks users…',
  emptyLabel = 'No users available',
}) {
  const [query, setQuery] = useState('');
  const excluded = useMemo(() => new Set(exclude), [exclude]);
  const selectedSet = useMemo(() => new Set(selected), [selected]);
  const choices = useMemo(() => {
    const term = query.trim().toLowerCase();
    return users
      .filter((user) => !selectedSet.has(user.email) && !excluded.has(user.email))
      .filter((user) => (
        !term
        || user.email.toLowerCase().includes(term)
        || (user.display_name || '').toLowerCase().includes(term)
      ))
      .slice(0, 20);
  }, [users, selectedSet, excluded, query]);

  const byEmail = useMemo(
    () => Object.fromEntries(users.map((user) => [user.email, user])),
    [users],
  );

  const add = (email) => {
    onChange([...selected, email]);
    setQuery('');
  };

  const remove = (email) => onChange(selected.filter((value) => value !== email));

  return (
    <div>
      {selected.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.35rem', marginBottom: '0.45rem' }}>
          {selected.map((email) => (
            <span
              key={email}
              className="badge badge-blue"
              style={{ display: 'inline-flex', alignItems: 'center', gap: '0.35rem' }}
            >
              {byEmail[email]?.display_name || email}
              <button
                type="button"
                onClick={() => remove(email)}
                aria-label={`Remove ${email}`}
                title={email}
                style={{
                  border: 0, background: 'transparent', color: 'inherit',
                  padding: 0, cursor: 'pointer', fontSize: '0.75rem',
                }}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}
      <input
        type="search"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        placeholder={placeholder}
        className="input"
        style={{ width: '100%', padding: '0.45rem 0.65rem', fontSize: '0.82rem' }}
      />
      {(query || choices.length > 0) && (
        <div style={{
          marginTop: '0.25rem',
          border: '1px solid var(--border-color)',
          borderRadius: 6,
          maxHeight: 180,
          overflowY: 'auto',
          background: 'var(--bg-card)',
        }}>
          {choices.length === 0 ? (
            <div style={{ padding: '0.6rem', color: 'var(--text-muted)', fontSize: '0.78rem' }}>
              {emptyLabel}
            </div>
          ) : choices.map((user) => (
            <button
              key={user.email}
              type="button"
              onClick={() => add(user.email)}
              style={{
                display: 'block', width: '100%', border: 0,
                borderBottom: '1px solid var(--border-color)',
                padding: '0.45rem 0.65rem', textAlign: 'left',
                background: 'transparent', color: 'var(--text-primary)', cursor: 'pointer',
              }}
            >
              <div style={{ fontSize: '0.82rem', fontWeight: 600 }}>
                {user.display_name || user.email}
              </div>
              {(user.display_name && user.display_name !== user.email) && (
                <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>{user.email}</div>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
