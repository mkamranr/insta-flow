import { useQuery } from '@tanstack/react-query'
import { getContacts } from '../api/analytics'

export function Contacts() {
  const { data: contacts, isLoading } = useQuery({ queryKey: ['contacts'], queryFn: getContacts })

  const asCsv = () => {
    const rows = [
      ['email', 'instagram_user_id', 'captured_at'],
      ...(contacts ?? []).map((c) => [c.email, c.instagram_user_id, c.created_at ?? '']),
    ]
    return rows.map((r) => r.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(',')).join('\n')
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Contacts</h1>
          <p className="muted">Emails captured by automations with the email gate switched on.</p>
        </div>
        {contacts && contacts.length > 0 && (
          <a
            href={`data:text/csv;charset=utf-8,${encodeURIComponent(asCsv())}`}
            download="social-flow-contacts.csv"
          >
            <button className="secondary">Download CSV</button>
          </a>
        )}
      </div>

      {isLoading && <p className="muted">Loading…</p>}

      {contacts?.length === 0 && (
        <div className="card">
          <h2>No contacts yet</h2>
          <p className="muted">
            Switch on <strong>Collect email before sending</strong> in an automation, and
            addresses people reply with will appear here.
          </p>
        </div>
      )}

      {contacts && contacts.length > 0 && (
        <div className="card">
          <table className="table">
            <thead>
              <tr>
                <th>Email</th>
                <th>Instagram ID</th>
                <th>Captured</th>
              </tr>
            </thead>
            <tbody>
              {contacts.map((contact) => (
                <tr key={contact.id}>
                  <td>{contact.email}</td>
                  <td className="muted small">{contact.instagram_user_id}</td>
                  <td className="muted small">
                    {contact.created_at ? new Date(contact.created_at).toLocaleDateString() : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
