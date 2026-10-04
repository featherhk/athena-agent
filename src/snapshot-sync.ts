import type { Snapshot } from './api';

/** Structural updates must not wait for the model to stop streaming text. */
export function mergeSnapshot(current: Snapshot, incoming: Snapshot, messageVersions: Map<string, number>, structuralFloor: number): Snapshot | null {
  if (incoming.cursor < structuralFloor) return null;
  const currentMessages = new Map(current.messages.map(message => [message.id, message]));
  const incomingIds = new Set(incoming.messages.map(message => message.id));
  const messages = incoming.messages.map(message =>
    (messageVersions.get(message.id) || 0) > incoming.cursor
      ? currentMessages.get(message.id) || message
      : message);
  for (const message of current.messages) {
    if (!incomingIds.has(message.id) && (messageVersions.get(message.id) || 0) > incoming.cursor) messages.push(message);
  }
  return { ...incoming, messages, cursor: Math.max(current.cursor, incoming.cursor),
    submission_revision: current.submission_revision === undefined ? incoming.submission_revision
      : Math.max(current.submission_revision, incoming.submission_revision ?? 0) };
}
