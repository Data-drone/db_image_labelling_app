import { createContext, useContext, useState } from 'react';

const DatasetContext = createContext(null);

const STORAGE_KEY = 'cv-explorer-dataset';

export function DatasetProvider({ children }) {
  const [dataset, setDatasetState] = useState(() => {
    try {
      const stored = sessionStorage.getItem(STORAGE_KEY);
      return stored ? JSON.parse(stored) : null;
    } catch {
      return null;
    }
  });

  const setDataset = (ds) => {
    setDatasetState(ds);
    if (ds) {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(ds));
    } else {
      sessionStorage.removeItem(STORAGE_KEY);
    }
  };

  return (
    <DatasetContext.Provider value={{ dataset, setDataset }}>
      {children}
    </DatasetContext.Provider>
  );
}

export function useDataset() {
  const ctx = useContext(DatasetContext);
  if (!ctx) throw new Error('useDataset must be used within DatasetProvider');
  return ctx;
}
