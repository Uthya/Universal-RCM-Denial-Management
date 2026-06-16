import { Routes, Route, Navigate } from 'react-router-dom';
import Sidebar from './components/layout/Sidebar';
import ErrorBoundary from './components/layout/ErrorBoundary';
import UploadPage from './pages/UploadPage';
import ClaimsPage from './pages/ClaimsPage';
import ClaimDetailPage from './pages/ClaimDetailPage';

function App() {
  return (
    <div className="min-h-screen bg-gray-50">
      <Sidebar />
      <main className="ml-64 p-6">
        <ErrorBoundary>
          <Routes>
            <Route path="/" element={<Navigate to="/upload" replace />} />
            <Route path="/upload" element={<UploadPage />} />
            <Route path="/claims" element={<ClaimsPage />} />
            <Route path="/claims/:id" element={<ClaimDetailPage />} />
            <Route path="*" element={<Navigate to="/upload" replace />} />
          </Routes>
        </ErrorBoundary>
      </main>
    </div>
  );
}

export default App;
