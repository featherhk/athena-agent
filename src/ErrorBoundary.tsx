import { Component } from 'react';
import type { ReactNode } from 'react';
import { translate } from './i18n';

type Props = { children: ReactNode; panel?: boolean; close?: () => void };
export default class ErrorBoundary extends Component<Props, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: Error) { console.error('Athena UI render error', error); }
  async reset() {
    try {
      await fetch('/api/reset', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    } finally {
      // Reset cannot depend on the failing component, its state or the agent.
      window.location.reload();
    }
  }
  render() {
    if (!this.state.failed) return this.props.children;
    return <section className={this.props.panel ? 'developer-panel recovery-panel' : 'recovery-shell'} role="alert">
      <strong>{this.props.panel ? translate('dev.title') : 'Athena'}</strong>
      <p>{translate('error.generic')}</p>
      {this.props.panel ? <button className="quiet" onClick={this.props.close}>{translate('close')}</button> : <>
        <button className="primary" onClick={() => window.location.reload()}>{translate('retry')}</button>
        <button className="quiet" onClick={() => void this.reset()}>{translate('reset')}</button>
      </>}
    </section>;
  }
}
