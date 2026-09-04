import { useMemo } from 'react'
import type { Catalogue, CatalogueModule } from '../../api/admin'

const permOf = (moduleKey: string, actionKey: string) => `${moduleKey}.${actionKey}`
const supports = (module: CatalogueModule, actionKey: string) =>
  module.actions.some((a) => a.key === actionKey)

/**
 * Toggle one cell, keeping View coherent with the rest of its row.
 *
 * Pure and module-scoped on purpose: this repo has no frontend test runner yet,
 * so the riskiest logic is kept in a function that can be covered the day it
 * grows one.
 *
 * The symmetry matters. Ticking any action switches View on, because a
 * permission to change something you cannot see does nothing. Unticking View
 * clears the row, rather than silently refusing the click -- a checkbox that
 * visibly ignores you is the worst thing a checkbox can do.
 */
export function togglePermission(
  value: string[],
  module: CatalogueModule,
  actionKey: string,
): string[] {
  const next = new Set(value)
  const target = permOf(module.key, actionKey)
  const hasView = supports(module, 'view')
  const others = module.actions
    .filter((a) => a.key !== 'view')
    .map((a) => permOf(module.key, a.key))

  if (next.has(target)) {
    next.delete(target)
    if (hasView && actionKey === 'view') others.forEach((p) => next.delete(p))
  } else {
    next.add(target)
    if (hasView && actionKey !== 'view') next.add(permOf(module.key, 'view'))
  }
  return [...next]
}

export function PermissionGrid({
  catalogue,
  value,
  onChange,
  disabled = false,
}: {
  catalogue: Catalogue
  value: string[]
  onChange: (next: string[]) => void
  disabled?: boolean
}) {
  const selected = useMemo(() => new Set(value), [value])
  const columns = catalogue.actions
  const modules = catalogue.modules

  const rowFull = (m: CatalogueModule) =>
    m.actions.every((a) => selected.has(permOf(m.key, a.key)))
  const columnFull = (actionKey: string) => {
    const applicable = modules.filter((m) => supports(m, actionKey))
    return (
      applicable.length > 0 &&
      applicable.every((m) => selected.has(permOf(m.key, actionKey)))
    )
  }

  const toggleRow = (module: CatalogueModule) => {
    const next = new Set(selected)
    const clearing = rowFull(module)
    module.actions.forEach((a) => {
      const key = permOf(module.key, a.key)
      if (clearing) next.delete(key)
      else next.add(key)
    })
    onChange([...next])
  }

  const toggleColumn = (actionKey: string) => {
    const next = new Set(selected)
    const clearing = columnFull(actionKey)
    for (const module of modules) {
      if (!supports(module, actionKey)) continue
      if (clearing) {
        next.delete(permOf(module.key, actionKey))
        // The same View implication as a single click, applied in bulk.
        if (actionKey === 'view') {
          module.actions.forEach((a) => next.delete(permOf(module.key, a.key)))
        }
      } else {
        next.add(permOf(module.key, actionKey))
        if (actionKey !== 'view' && supports(module, 'view')) {
          next.add(permOf(module.key, 'view'))
        }
      }
    }
    onChange([...next])
  }

  return (
    <>
      <div className="row perm-toolbar">
        <span className="muted small">
          {selected.size} {selected.size === 1 ? 'permission' : 'permissions'} selected
        </span>
        <button
          type="button"
          className="secondary"
          disabled={disabled}
          onClick={() =>
            onChange(modules.flatMap((m) => m.actions.map((a) => permOf(m.key, a.key))))
          }
        >
          Select everything
        </button>
        <button type="button" className="link" disabled={disabled} onClick={() => onChange([])}>
          Clear all
        </button>
      </div>

      <div className="perm-scroll">
        <table className="table perm-grid">
          <thead>
            <tr>
              <th className="perm-module perm-module-head">Module</th>
              {columns.map((action) => (
                <th key={action.key} scope="col" className="perm-action">
                  <span className="perm-action-label">{action.label}</span>
                  <button
                    type="button"
                    className="link perm-all"
                    disabled={disabled}
                    onClick={() => toggleColumn(action.key)}
                  >
                    {columnFull(action.key) ? 'Clear' : 'All'}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {modules.map((module) => (
              <tr key={module.key}>
                <th scope="row" className="perm-module">
                  <div className="perm-module-name">
                    {module.label}
                    <button
                      type="button"
                      className="link perm-all"
                      disabled={disabled}
                      onClick={() => toggleRow(module)}
                    >
                      {rowFull(module) ? 'Clear' : 'All'}
                    </button>
                  </div>
                  <div className="muted small">{module.description}</div>
                </th>

                {columns.map((action) => {
                  const supported = module.actions.find((a) => a.key === action.key)
                  return supported ? (
                    <td key={action.key} className="perm-cell">
                      <label className="perm-check">
                        <input
                          type="checkbox"
                          checked={selected.has(permOf(module.key, action.key))}
                          disabled={disabled}
                          aria-label={`${module.label}: ${supported.label}`}
                          onChange={() => onChange(togglePermission(value, module, action.key))}
                        />
                      </label>
                    </td>
                  ) : (
                    // A disabled unchecked box would read as "off". "This module
                    // has no such action" is a different claim, so it gets the
                    // dash this app already uses for "no value".
                    <td key={action.key} className="perm-cell perm-na">
                      —
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="muted small perm-note">
        Ticking any action switches <strong>View</strong> on for that module, and switching View
        off clears the rest — a permission to change something you cannot see does nothing.
      </p>
    </>
  )
}
