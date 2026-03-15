import { Routes, Route } from 'react-router-dom';
import Layout from './components/Layout';
import HomePage from './pages/HomePage';
import BrowseVolumes from './pages/BrowseVolumes';
import DatasetExplorer from './pages/DatasetExplorer';
import LabelingView from './pages/LabelingView';
import SearchPage from './pages/SearchPage';
import Dashboard from './pages/Dashboard';

function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/browse" element={<BrowseVolumes />} />
        <Route path="/explorer" element={<DatasetExplorer />} />
        <Route path="/labeling" element={<LabelingView />} />
        <Route path="/search" element={<SearchPage />} />
        <Route path="/dashboard" element={<Dashboard />} />
      </Routes>
    </Layout>
  );
}

export default App;
