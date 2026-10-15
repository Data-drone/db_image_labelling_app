import { createContext, useState } from 'react';

const ProjectContext = createContext(null);

const STORAGE_KEY = 'cv-explorer-project';

export function ProjectProvider({ children }) {
  const [project, setProjectState] = useState(() => {
    try {
      const stored = sessionStorage.getItem(STORAGE_KEY);
      return stored ? JSON.parse(stored) : null;
    } catch {
      return null;
    }
  });

  const setProject = (p) => {
    setProjectState(p);
    if (p) {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(p));
    } else {
      sessionStorage.removeItem(STORAGE_KEY);
    }
  };

  return (
    <ProjectContext.Provider value={{ project, setProject }}>
      {children}
    </ProjectContext.Provider>
  );
}
