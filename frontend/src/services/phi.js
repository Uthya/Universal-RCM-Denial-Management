/**
 * PHI redaction state.
 *
 * Default OFF (committed code is safe for screenshots). Toggle persists in
 * localStorage so the per-session preference survives refreshes.
 *
 * Per UI-plan §8: the PHIToggle wraps strings that COULD contain PHI
 * (claim_number, member_id, patient name, NPI). When OFF, the toggle
 * renders '***' or a hash. Components opt-in by calling redactIfPhiOff().
 */

import { useEffect, useState } from 'react';

const STORAGE_KEY = 'rcm.dev.showPhi';

function readStored() {
  try {
    return localStorage.getItem(STORAGE_KEY) === '1';
  } catch {
    return false;
  }
}

let listeners = new Set();
let currentValue = readStored();

function setValue(v) {
  currentValue = v;
  try {
    localStorage.setItem(STORAGE_KEY, v ? '1' : '0');
  } catch {
    /* ignore */
  }
  for (const l of listeners) l(v);
}

export function usePhi() {
  const [showPhi, setShow] = useState(currentValue);
  useEffect(() => {
    const l = (v) => setShow(v);
    listeners.add(l);
    return () => listeners.delete(l);
  }, []);
  return {
    showPhi,
    togglePhi: () => setValue(!currentValue),
  };
}

export function redactIfPhiOff(value, showPhi) {
  if (showPhi) return value;
  if (value === null || value === undefined || value === '') return value;
  const s = String(value);
  if (s.length <= 4) return '***';
  return `${s.slice(0, 2)}***${s.slice(-2)}`;
}
