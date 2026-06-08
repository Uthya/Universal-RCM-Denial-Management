import React from 'react';

// Top-level error boundary so a thrown error in any child component
// does not blank the whole app. Renders a recoverable fallback that
// lets the user retry without losing their session/route.
class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    // Keep this lightweight — no PHI in the error payload.
    // eslint-disable-next-line no-console
    console.error('ErrorBoundary caught:', error, info?.componentStack);
  }

  reset = () => this.setState({ error: null });

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="m-8 p-6 rounded border border-red-300 bg-red-50">
        <h2 className="text-lg font-semibold text-red-800">Something went wrong</h2>
        <p className="mt-2 text-sm text-red-700">
          The page hit an error and couldn't finish rendering. You can retry
          or navigate to a different page from the sidebar.
        </p>
        <pre className="mt-3 text-xs overflow-auto max-h-32 bg-white p-2 rounded">
          {String(this.state.error?.message || this.state.error)}
        </pre>
        <button
          onClick={this.reset}
          className="mt-3 px-3 py-1.5 rounded bg-red-600 text-white text-sm hover:bg-red-700"
        >
          Retry
        </button>
      </div>
    );
  }
}

export default ErrorBoundary;
