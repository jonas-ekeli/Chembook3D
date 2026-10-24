// D182 (UI10): the start page. The three ways in as cards, the recently opened investigations by
// name with their folder, when they were last opened and their size, and the Scan editor and Shut
// down set apart at the bottom.

import type { ReactNode } from 'react'
import type { RecentInfo, Settings } from '../api'
import { Icon, type IconName } from '../icons'
import { ShutDownButton } from './ShutDown'

/** "just now", "5 min ago", "2 h ago", "yesterday", "4 days ago", then the date. */
function relativeTime(iso: string, now: Date = new Date()): string {
  const when = new Date(iso)
  const minutes = Math.round((now.getTime() - when.getTime()) / 60000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes} min ago`
  if (minutes < 24 * 60) return `${Math.round(minutes / 60)} h ago`
  const midnight = (date: Date) => new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime()
  const days = Math.round((midnight(now) - midnight(when)) / 86400000)
  if (days <= 1) return 'yesterday'
  if (days < 7) return `${days} days ago`
  return when.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
}

function count(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`
}

/** The last part of a folder path, on Windows or elsewhere. */
function folderName(folder: string): string {
  return folder.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || folder
}

function Action({
  icon,
  name,
  description,
  primary = false,
  onClick,
}: {
  icon: IconName
  name: string
  description: string
  primary?: boolean
  onClick: () => void
}) {
  const id = `start-${icon}`
  // The card's name is its title only, so it reads (and is found) as before D182.
  return (
    <button
      className={`start-action${primary ? ' primary-card' : ''}`}
      aria-label={name}
      aria-describedby={id}
      onClick={onClick}
    >
      <b>
        <Icon name={icon} />
        {name}
      </b>
      <span id={id}>{description}</span>
    </button>
  )
}

function RecentRow({
  folder,
  info,
  repeated,
  onOpen,
}: {
  folder: string
  info: RecentInfo | undefined
  repeated: boolean
  onOpen: () => void
}) {
  const name = info?.name || folderName(folder)
  const counts = [
    info?.nodes != null && count(info.nodes, 'node', 'nodes'),
    info?.branches != null && count(info.branches, 'branch', 'branches'),
  ].filter(Boolean)
  return (
    <li>
      <button className="recent-row" onClick={onOpen} title={folder}>
        <span className="recent-icon">
          <Icon name="flask" />
        </span>
        <span className="recent-what">
          <span className="recent-name">
            {name}
            {/* Two investigations of one name are told apart by their folders. */}
            {repeated && <span className="muted"> ({folderName(folder)})</span>}
          </span>
          <span className="recent-folder">{folder}</span>
        </span>
        <span className="recent-when">
          {info?.opened && (
            <time dateTime={info.opened} title={new Date(info.opened).toLocaleString()}>
              {relativeTime(info.opened)}
            </time>
          )}
          {counts.length > 0 && <span>{counts.join(' · ')}</span>}
        </span>
      </button>
    </li>
  )
}

export function StartPage({
  settings,
  error,
  onNew,
  onOpen,
  onClone,
  onScanEditor,
  onOpenFolder,
  notices,
  children,
}: {
  settings: Settings | null
  error: string | null
  onNew: () => void
  onOpen: () => void
  onClone: () => void
  onScanEditor: () => void
  onOpenFolder: (folder: string) => void
  /** The launcher's notices, under the cards. */
  notices: ReactNode
  /** The dialogs the cards open. */
  children: ReactNode
}) {
  const recent = settings?.recent ?? []
  const names = recent.map((folder) => settings?.recent_info[folder]?.name || folderName(folder))
  return (
    <main className="start">
      <div className="start-hero">
        <div className="start-logo">
          <Icon name="hexagon" />
        </div>
        <div>
          <h1>Chembook3D</h1>
          <p className="muted">A notebook for computational chemistry mechanism investigations.</p>
        </div>
      </div>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <div className="start-actions">
        <Action
          icon="plus"
          name="New investigation…"
          description="An empty canvas in a folder you choose"
          primary
          onClick={onNew}
        />
        <Action icon="folder" name="Open investigation…" description="A folder on this computer" onClick={onOpen} />
        <Action icon="branch" name="Open from GitHub…" description="Clone a synced investigation" onClick={onClone} />
      </div>
      {notices}
      {recent.length > 0 && (
        <section>
          <h2 className="start-heading">Recently opened</h2>
          <ul className="recent" aria-label="Recently opened">
            {recent.map((folder, index) => (
              <RecentRow
                key={folder}
                folder={folder}
                info={settings?.recent_info[folder]}
                repeated={names.filter((n) => n === names[index]).length > 1}
                onOpen={() => onOpenFolder(folder)}
              />
            ))}
          </ul>
        </section>
      )}
      <div className="start-bottom">
        <Icon name="wave" />
        <span>The Scan editor works without an investigation.</span>
        <button
          onClick={onScanEditor}
          title="Put several scans together into one path, without an investigation (D135)"
        >
          Scan editor
        </button>
        <span className="spacer" />
        <ShutDownButton />
      </div>
      {children}
    </main>
  )
}
