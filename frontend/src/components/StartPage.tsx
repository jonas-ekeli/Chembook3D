// D182 (UI10): the start page. The three ways in as cards, the recently opened investigations by
// name with their folder, when they were last opened and their size, and the Scan editor and Shut
// down set apart at the bottom. D229: a folder that no longer holds an investigation is marked
// as not found, and any row can be taken off the list (its folder is never touched). D235: each
// investigation's playgrounds are listed under it, indented, and its menu makes a new one.

import { Fragment, type ReactNode } from 'react'
import type { PlaygroundRow, RecentInfo, Settings } from '../api'
import { Icon, type IconName } from '../icons'
import { relativeTime } from '../util'
import { MoreMenu } from './MoreMenu'
import { ShutDownButton } from './ShutDown'

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
  // The card's name is its title only, so it reads (and is found) as before D182; the line
  // under it is its description. The name comes from the title itself, not an aria-label, so
  // the name holds the visible text (WCAG 2.5.3, e2e/accessibility.spec.ts).
  return (
    <button className={`start-action${primary ? ' primary-card' : ''}`} aria-describedby={id} onClick={onClick}>
      <b>
        <Icon name={icon} />
        {name}
      </b>
      <span id={id} aria-hidden="true">
        {description}
      </span>
    </button>
  )
}

function RecentRow({
  folder,
  info,
  repeated,
  missing,
  onOpen,
  onForget,
  onNewPlayground,
}: {
  folder: string
  info: RecentInfo | undefined
  repeated: boolean
  missing: boolean
  onOpen: () => void
  onForget: () => void
  onNewPlayground: () => void
}) {
  const name = info?.name || folderName(folder)
  const counts = [
    info?.nodes != null && count(info.nodes, 'node', 'nodes'),
    info?.branches != null && count(info.branches, 'branch', 'branches'),
  ].filter(Boolean)
  return (
    <li className={missing ? 'missing' : undefined}>
      <button
        className="recent-row"
        onClick={onOpen}
        title={missing ? `${folder}\nNo investigation was found in this folder` : folder}
      >
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
          {missing && (
            <span className="recent-missing">
              <Icon name="warn" />
              Not found
            </span>
          )}
          {info?.opened && (
            <time dateTime={info.opened} title={new Date(info.opened).toLocaleString()}>
              {relativeTime(info.opened)}
            </time>
          )}
          {!missing && counts.length > 0 && <span>{counts.join(' · ')}</span>}
        </span>
      </button>
      <MoreMenu
        label={`More for ${name}`}
        items={[
          {
            label: 'New playground…',
            icon: 'playground',
            title: 'A working copy to try things in, kept in this folder; the original is not changed (D235)',
            disabled: missing,
            onSelect: onNewPlayground,
          },
        ]}
      />
      <button
        className="quiet recent-forget"
        aria-label={`Remove ${name} from the list`}
        title="Remove from this list (the folder and its files are not touched)"
        onClick={onForget}
      >
        <Icon name="x" />
      </button>
    </li>
  )
}

/** "original changed 3 things since", from the original's history since the playground was made. */
function changedSince(changed: number | null): string | null {
  if (changed === null) return null
  if (changed === 0) return 'original unchanged since'
  return `original changed ${count(changed, 'thing', 'things')} since`
}

/** D235: a playground, indented under its original. */
function PlaygroundRowItem({
  row,
  original,
  onOpen,
  onMerge,
  onDetach,
  onDiscard,
}: {
  row: PlaygroundRow
  original: string
  onOpen: () => void
  onMerge: () => void
  onDetach: () => void
  onDiscard: () => void
}) {
  const since = changedSince(row.changed)
  return (
    <li className="playground-row">
      <button
        className="recent-row"
        onClick={onOpen}
        title={row.folder}
        aria-label={`${row.name}, playground of ${original}`}
      >
        <span className="recent-icon">
          <Icon name="playground" />
        </span>
        <span className="recent-what">
          <span className="recent-name">{row.name}</span>
          <span className="recent-folder">Playground of {original}</span>
        </span>
        <span className="recent-when">
          {row.foreign && (
            <span className="recent-missing" title="Its records were not copied from the investigation it is in">
              <Icon name="warn" />
              Made from another investigation
            </span>
          )}
          {row.created_at && (
            <span>
              made{' '}
              <time dateTime={row.created_at} title={new Date(row.created_at).toLocaleString()}>
                {relativeTime(row.created_at)}
              </time>
            </span>
          )}
          {row.merged_at ? (
            <span>
              merged{' '}
              <time dateTime={row.merged_at} title={new Date(row.merged_at).toLocaleString()}>
                {relativeTime(row.merged_at)}
              </time>
            </span>
          ) : (
            since && <span>{since}</span>
          )}
        </span>
      </button>
      <MoreMenu
        label={`More for ${row.name}`}
        items={[
          {
            label: 'Merge…',
            icon: 'playground',
            title: row.merged_at
              ? 'Already merged; a playground merges once'
              : 'Open the original and review what this playground would bring into it',
            disabled: !!row.merged_at || row.foreign,
            onSelect: onMerge,
          },
          {
            label: 'Make its own investigation…',
            icon: 'folder',
            title: 'Move this playground out of its original into a folder of its own (asks where first)',
            onSelect: onDetach,
          },
          {
            label: 'Discard…',
            icon: 'trash',
            danger: true,
            title: 'Send this playground to the recycle bin (asks first); the original is not changed',
            onSelect: onDiscard,
          },
        ]}
      />
      {/* in line with the remove buttons of the rows above */}
      <span className="recent-forget-space" />
    </li>
  )
}

export function StartPage({
  settings,
  error,
  onNew,
  onBuild,
  onOpen,
  onClone,
  onScanEditor,
  onOpenFolder,
  onForget,
  playgrounds,
  onNewPlayground,
  onDiscardPlayground,
  onMergePlayground,
  onDetachPlayground,
  notices,
  children,
}: {
  settings: Settings | null
  error: string | null
  onNew: () => void
  /** D263a: "Build mechanism from folder…": a new investigation, then its start dialog. */
  onBuild: () => void
  onOpen: () => void
  onClone: () => void
  onScanEditor: () => void
  onOpenFolder: (folder: string) => void
  /** D229: take a folder off the recent list. */
  onForget: (folder: string) => void
  /** D235: the playgrounds of each recent folder. */
  playgrounds: Record<string, PlaygroundRow[]>
  /** D235: New playground… on a recent folder's menu (its folder and name). */
  onNewPlayground: (folder: string, name: string) => void
  /** D235g: Discard… on a playground's row. */
  onDiscardPlayground: (folder: string) => void
  /** D235h: Merge… on a playground's row. */
  onMergePlayground: (folder: string) => void
  /** D236: Make its own investigation… on a playground's row. */
  onDetachPlayground: (folder: string) => void
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
        <Action
          icon="spark"
          name="Build mechanism from folder…"
          description="A new investigation that Claude builds from a folder of outputs"
          onClick={onBuild}
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
              <Fragment key={folder}>
                <RecentRow
                  folder={folder}
                  info={settings?.recent_info[folder]}
                  repeated={names.filter((n) => n === names[index]).length > 1}
                  missing={settings?.recent_missing?.includes(folder) ?? false}
                  onOpen={() => onOpenFolder(folder)}
                  onForget={() => onForget(folder)}
                  onNewPlayground={() => onNewPlayground(folder, names[index])}
                />
                {(playgrounds[folder] ?? []).map((row) => (
                  <PlaygroundRowItem
                    key={row.folder}
                    row={row}
                    original={names[index]}
                    onOpen={() => onOpenFolder(row.folder)}
                    onMerge={() => onMergePlayground(row.folder)}
                    onDetach={() => onDetachPlayground(row.folder)}
                    onDiscard={() => onDiscardPlayground(row.folder)}
                  />
                ))}
              </Fragment>
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
