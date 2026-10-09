import React from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import DesktopWidgetPage from './DesktopWidgetPage';
import { desktopRole } from './desktop-bridge';
import ErrorBoundary from './ErrorBoundary';
import { I18nProvider } from './i18n';
import './style.css';
import './desktop.css';

const Root = desktopRole() === 'widget' ? DesktopWidgetPage : App;
createRoot(document.getElementById('root')!).render(<React.StrictMode><I18nProvider><ErrorBoundary><Root /></ErrorBoundary></I18nProvider></React.StrictMode>);
