import { Routes, Route, Navigate } from 'react-router-dom';
import TopBar from './components/layout/TopBar';
import Sidebar from './components/layout/Sidebar';
import ErrorBoundary from './components/layout/ErrorBoundary';
import HomePage from './pages/HomePage';
import EnvPage from './pages/EnvPage';
import DatabasePage from './pages/DatabasePage';
import ParsingTelemetryPage from './pages/ParsingTelemetryPage';
import EdiInspectorPage from './pages/EdiInspectorPage';
import ClaimsPage from './pages/ClaimsPage';

export default function App() {
  return (
    <div className="min-h-screen bg-slate-50">
      <TopBar />
      <Sidebar />
      <main className="ml-56 mt-10 p-4">
        <ErrorBoundary>
          <Routes>
            <Route path="/" element={<HomePage />} />
            <Route path="/edi" element={<EdiInspectorPage />} />
            <Route path="/claims" element={<ClaimsPage />} />
            <Route path="/db" element={<DatabasePage />} />
            <Route path="/telemetry" element={<ParsingTelemetryPage />} />
            <Route path="/env" element={<EnvPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </ErrorBoundary>
      </main>
    </div>
  );
}
