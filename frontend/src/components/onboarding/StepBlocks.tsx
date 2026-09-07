import type { Block } from '../../api/onboarding'
import { renderInline } from './inline'

/** Draws everything that is pure content. The three interactive kinds --
 *  `copy`, `embed` and `check` -- are handed back to the caller, so the
 *  dependencies on `['meta-app']` and `['instagram-accounts']` stay confined
 *  to one file. */
export function StepBlocks({
  blocks,
  interactive,
}: {
  blocks: Block[]
  interactive?: (block: Block, index: number) => React.ReactNode
}) {
  return (
    <>
      {blocks.map((block, index) => {
        switch (block.kind) {
          case 'text':
            return <p key={index}>{renderInline(block.text)}</p>
          case 'note':
            return (
              <div className="notice" key={index}>
                {renderInline(block.text)}
              </div>
            )
          case 'warn':
            return (
              <div className="card warn-card" key={index}>
                <div>{renderInline(block.text)}</div>
              </div>
            )
          case 'code':
            return (
              <pre className="code-block" key={index}>
                {block.text}
              </pre>
            )
          case 'list':
            return block.ordered ? (
              <ol className="steps" key={index}>
                {block.items.map((item, i) => (
                  <li key={i}>{renderInline(item)}</li>
                ))}
              </ol>
            ) : (
              <ul key={index}>
                {block.items.map((item, i) => (
                  <li key={i}>{renderInline(item)}</li>
                ))}
              </ul>
            )
          case 'table':
            return (
              <div className="wiz-table" key={index}>
                <table className="table">
                  <thead>
                    <tr>
                      {block.head.map((cell) => (
                        <th key={cell}>{cell}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {block.rows.map((row, i) => (
                      <tr key={i}>
                        {row.map((cell, j) => (
                          <td key={j}>{renderInline(cell)}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          case 'links':
            return (
              <p key={index}>
                {block.links.map((link) => (
                  <a
                    className="link"
                    key={link.href}
                    href={link.href}
                    target={link.href.startsWith('http') ? '_blank' : undefined}
                    rel={link.href.startsWith('http') ? 'noreferrer' : undefined}
                  >
                    {link.label}
                  </a>
                ))}
              </p>
            )
          case 'copy':
          case 'embed':
          case 'check':
            return <div key={index}>{interactive?.(block, index)}</div>
          default: {
            // A kind added on the server that this renderer does not draw is a
            // build error, not a blank space on the page.
            const never: never = block
            return never
          }
        }
      })}
    </>
  )
}
