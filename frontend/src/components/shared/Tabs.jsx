/**
 * Headless-UI tab strip with monospace dev-console styling.
 *
 * Pattern:
 *   <Tabs tabs={[
 *     { id: 'tables',   label: 'Tables (47)',  render: () => <TablesPanel /> },
 *     { id: 'mvs',      label: 'MVs (12)',     render: () => <MvsPanel /> },
 *   ]} />
 *
 * Reused across DatabasePage / EdiInspector / ClaimDetail / future pages.
 */

import { Tab } from '@headlessui/react';

function classNames(...c) { return c.filter(Boolean).join(' '); }

export default function Tabs({ tabs, defaultIndex = 0 }) {
  return (
    <Tab.Group defaultIndex={defaultIndex}>
      <Tab.List className="flex border-b border-slate-300 bg-slate-100">
        {tabs.map((t) => (
          <Tab
            key={t.id}
            className={({ selected }) =>
              classNames(
                'px-3 py-2 text-xs font-mono border-r border-slate-300 focus:outline-none',
                selected
                  ? 'bg-white text-slate-900 border-b-2 border-b-blue-600 -mb-px'
                  : 'text-slate-600 hover:bg-slate-200',
              )
            }
          >
            {t.label}
          </Tab>
        ))}
      </Tab.List>
      <Tab.Panels className="bg-white">
        {tabs.map((t) => (
          <Tab.Panel key={t.id} className="p-3">
            {t.render()}
          </Tab.Panel>
        ))}
      </Tab.Panels>
    </Tab.Group>
  );
}
