import { isValidElement, useRef, useState, type ReactNode } from 'react'
import { api, type Settings } from '../api'
import { Icon, type IconName } from '../icons'
import { Modal } from './Modal'
import { NumberSetting } from './NumberSetting'
import { StericColourSelect } from './Sterics'
import { thermoSection } from './ThermoSettings'

/** One setting: its name and a line or two of help beside its control; `more` is the rest of a
 * long explanation, shown in place by "More…" (D183). */
export type SettingRow = { name: string; help: ReactNode; more?: ReactNode; control: ReactNode }

/** A section of the Settings dialog. `scope` says where its settings are kept: in this
 * computer's settings, for every investigation, or in the open investigation (D159). */
export type SettingSection = {
  id: string
  title: string
  icon: IconName
  scope: 'computer' | 'investigation'
  badge: string
  /** A line for the dialog's foot, saying where the section's changes go. */
  note?: ReactNode
  rows: SettingRow[]
}

/** The words of a piece of help, for the search. */
function textOf(node: ReactNode): string {
  if (node === null || node === undefined || typeof node === 'boolean') return ''
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(textOf).join('')
  if (isValidElement<{ children?: ReactNode }>(node)) return textOf(node.props.children)
  return ''
}

function matches(text: string, words: string[]): boolean {
  const lower = text.toLowerCase()
  return words.every((word) => lower.includes(word))
}

function Row({ row, words }: { row: SettingRow; words: string[] }) {
  const [open, setOpen] = useState(false)
  // A search that finds a setting by words of its longer explanation shows that explanation.
  const shown = `${row.name} ${textOf(row.help)}`
  const found =
    words.length > 0 &&
    row.more !== undefined &&
    !matches(shown, words) &&
    matches(`${shown} ${textOf(row.more)}`, words)
  const expanded = open || found
  return (
    <div className="setting-row">
      <div className="setting-text">
        <div className="setting-name">{row.name}</div>
        <div className="setting-help">
          {row.help}
          {row.more !== undefined && (
            <>
              {' '}
              {expanded && <>{row.more} </>}
              {!found && (
                <button className="link" aria-expanded={expanded} onClick={() => setOpen(!open)}>
                  {expanded ? 'Less' : 'More…'}
                </button>
              )}
            </>
          )}
        </div>
      </div>
      <div className="setting-control">{row.control}</div>
    </div>
  )
}

/** D183: Settings as a two-pane page: the sections on the left, grouped by where they are kept
 * (this computer, or the open investigation), and a search over every setting's name and help;
 * on the right one row per setting, each section headed by a badge naming its scope. Each control
 * saves as it did before: a choice at once, a number when its field loses focus. */
export function SettingsDialog({
  settings,
  investigation,
  onSettings,
  onThermoSaved,
  onClose,
}: {
  settings: Settings
  investigation: { name: string; folder: string } | null
  onSettings: (settings: Settings) => void
  onThermoSaved: (settings: Settings) => void
  onClose: () => void
}) {
  const save = (fields: Parameters<typeof api.saveSettings>[0]) => api.saveSettings(fields).then(onSettings)
  const thermo = thermoSection({
    settings,
    investigationName: investigation?.name ?? null,
    resetKey: `${settings.thermo_scope}:${investigation?.folder ?? ''}`,
    onSaved: onThermoSaved,
  })
  const computer: SettingSection[] = [
    {
      id: 'general',
      title: 'General',
      icon: 'sliders',
      scope: 'computer',
      badge: 'All investigations',
      rows: [
        {
          name: 'Energy unit',
          help: 'Relative energies use this unit; absolute energies are shown in hartree.',
          control: (
            <select
              aria-label="Energy unit"
              value={settings.energy_unit}
              onChange={(event) => save({ energy_unit: event.target.value })}
            >
              {settings.energy_units.map((unit) => (
                <option key={unit}>{unit}</option>
              ))}
            </select>
          ),
        },
      ],
    },
    {
      id: 'geometry',
      title: 'Geometry matching',
      icon: 'target',
      scope: 'computer',
      badge: 'All investigations',
      rows: [
        {
          name: 'Same geometry',
          help: 'RMSD after alignment within which a job step’s results attach to a node (ID-7).',
          control: (
            <NumberSetting
              label="Same geometry"
              unit="Å"
              value={settings.geometry_tolerance}
              onSave={(geometry_tolerance) => save({ geometry_tolerance })}
            />
          ),
        },
        {
          name: 'Possible duplicate',
          help: 'An import within this RMSD of another node is flagged as a possible duplicate (ID-8).',
          control: (
            <NumberSetting
              label="Possible duplicate"
              unit="Å"
              value={settings.duplicate_tolerance}
              onSave={(duplicate_tolerance) => save({ duplicate_tolerance })}
            />
          ),
        },
      ],
    },
    {
      id: 'structures',
      title: 'Structures',
      icon: 'cube',
      scope: 'computer',
      badge: 'All investigations',
      rows: [
        {
          name: 'Hydrogens',
          help: (
            <>
              The 3D views and structure cards. “Hide those bonded to carbon” keeps hydrides, O–H and N–H, and C–H
              hydrogens close to a metal (agostic).
            </>
          ),
          control: (
            <select
              aria-label="Hydrogens"
              value={settings.hydrogens}
              onChange={(event) => save({ hydrogens: event.target.value as Settings['hydrogens'] })}
            >
              <option value="all">Show all</option>
              <option value="polar">Hide those bonded to carbon</option>
              <option value="none">Hide all</option>
            </select>
          ),
        },
      ],
    },
    {
      id: 'sterics',
      title: 'Steric maps',
      icon: 'group',
      scope: 'computer',
      badge: 'All investigations',
      rows: [
        {
          name: 'Map colours',
          help: 'From low (far below the centre) to high (crowding it). Difference maps stay blue–grey–red.',
          control: <StericColourSelect bare />,
        },
      ],
    },
    {
      id: 'crest',
      title: 'CREST import',
      icon: 'file',
      scope: 'computer',
      badge: 'All investigations',
      rows: [
        {
          name: 'Conformers kept on import',
          help: 'The lowest this many conformers of an ensemble are ticked in the import preview (D34).',
          control: (
            <NumberSetting
              label="Conformers kept on import"
              value={settings.crest_count}
              onSave={(count) => save({ crest_count: Math.max(1, Math.round(count)) })}
            />
          ),
        },
      ],
    },
  ]
  // D159: with no investigation open, the thermochemistry is this computer's default for new ones.
  const own = thermo.scope === 'investigation'
  const groups: { name: string; sections: SettingSection[] }[] = own
    ? [
        { name: 'This computer', sections: computer },
        { name: 'This investigation', sections: [thermo] },
      ]
    : [{ name: 'This computer', sections: [...computer, thermo] }]

  const [query, setQuery] = useState('')
  const words = query.toLowerCase().split(/\s+/).filter(Boolean)
  const shown = groups
    .map((group) => ({
      ...group,
      sections: group.sections
        .map((section) => ({
          ...section,
          // A section found by its title shows all its settings.
          rows: matches(section.title, words)
            ? section.rows
            : section.rows.filter((row) => matches(`${row.name} ${textOf(row.help)} ${textOf(row.more)}`, words)),
        }))
        .filter((section) => section.rows.length > 0),
    }))
    .filter((group) => group.sections.length > 0)
  const sections = shown.flatMap((group) => group.sections)

  // The section list follows the scroll: the last section whose head has passed the top, or the
  // one picked in the list when the page cannot scroll it up that far.
  const content = useRef<HTMLDivElement>(null)
  const picked = useRef<string | null>(null)
  const [active, setActive] = useState('')
  const follow = () => {
    const box = content.current
    if (!box) return
    const top = box.getBoundingClientRect().top
    const heads = [...box.querySelectorAll<HTMLElement>('[data-section]')]
    let passed = heads[0]?.dataset.section
    for (const head of heads) if (head.getBoundingClientRect().top - top <= 24) passed = head.dataset.section
    const atEnd = box.scrollTop + box.clientHeight >= box.scrollHeight - 2
    if (atEnd && picked.current && heads.some((head) => head.dataset.section === picked.current))
      passed = picked.current
    else picked.current = null
    if (passed) setActive(passed)
  }
  const go = (id: string) => {
    const box = content.current
    const head = box?.querySelector<HTMLElement>(`[data-section="${id}"]`)
    if (!box || !head) return
    picked.current = id
    setActive(id)
    box.scrollTop += head.getBoundingClientRect().top - box.getBoundingClientRect().top
  }
  // A new search starts the page at its top, on the first section left.
  const search = (text: string) => {
    setQuery(text)
    setActive('')
    picked.current = null
    if (content.current) content.current.scrollTop = 0
  }
  const current = sections.some((section) => section.id === active) ? active : sections[0]?.id
  return (
    <Modal
      title="Settings"
      className="settings-dialog"
      head={
        <span className="settings-search">
          <Icon name="search" />
          <input
            type="search"
            aria-label="Search settings"
            placeholder="Search settings"
            value={query}
            onChange={(event) => {
              search(event.target.value)
            }}
            onKeyDown={(event) => {
              // Escape empties the search first; with nothing typed it closes the dialog.
              if (event.key === 'Escape' && query) {
                event.stopPropagation()
                search('')
              }
            }}
          />
        </span>
      }
      note={thermo.note}
      onClose={onClose}
      actions={<button onClick={onClose}>Done</button>}
    >
      <nav className="settings-nav" aria-label="Settings sections">
        {shown.map((group) => (
          <div key={group.name} className="settings-group">
            <div className="settings-group-name">{group.name}</div>
            {group.sections.map((section) => (
              <button
                key={section.id}
                aria-current={current === section.id ? 'true' : undefined}
                onClick={() => go(section.id)}
              >
                <Icon name={section.icon} />
                {section.title}
              </button>
            ))}
          </div>
        ))}
      </nav>
      <div className="settings-content" ref={content} onScroll={follow}>
        {sections.map((section) => (
          <section key={section.id} className="settings-section" data-section={section.id} aria-label={section.title}>
            <h3>
              {section.title}
              <span className={section.scope === 'investigation' ? 'scope-badge investigation' : 'scope-badge'}>
                {section.badge}
              </span>
            </h3>
            {section.rows.map((row) => (
              <Row key={row.name} row={row} words={words} />
            ))}
          </section>
        ))}
        {sections.length === 0 && <p className="muted settings-none">No setting matches “{query.trim()}”.</p>}
      </div>
    </Modal>
  )
}
