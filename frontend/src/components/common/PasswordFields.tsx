import { MIN_PASSWORD } from '../../api/auth'

/** Password plus confirmation, with the length rule in one place.
 *
 * Moved out of `admin/UserDialogs.tsx`, where it was module-private, when
 * accepting an invitation needed the same pair. `idPrefix` exists only so the
 * two forms can coexist in the DOM without duplicate ids; the 12-character rule
 * still has exactly one definition, `MIN_PASSWORD` in `api/auth.ts`, mirrored
 * from `MIN_PASSWORD_LENGTH` on the server.
 */
export function PasswordFields({
  password,
  confirm,
  onPassword,
  onConfirm,
  idPrefix = 'user',
}: {
  password: string
  confirm: string
  onPassword: (value: string) => void
  onConfirm: (value: string) => void
  idPrefix?: string
}) {
  const tooShort = password.length > 0 && password.length < MIN_PASSWORD
  const mismatch = confirm.length > 0 && confirm !== password

  return (
    <>
      <label htmlFor={`${idPrefix}-password`}>Password</label>
      <input
        id={`${idPrefix}-password`}
        type="password"
        autoComplete="new-password"
        value={password}
        onChange={(e) => onPassword(e.target.value)}
      />
      {tooShort && <p className="err small">At least {MIN_PASSWORD} characters.</p>}
      <p className="muted small">
        A passphrase of a few words is easier to remember and harder to guess.
      </p>

      <label htmlFor={`${idPrefix}-password-confirm`}>Confirm password</label>
      <input
        id={`${idPrefix}-password-confirm`}
        type="password"
        autoComplete="new-password"
        value={confirm}
        onChange={(e) => onConfirm(e.target.value)}
      />
      {mismatch && <p className="err small">The two passwords do not match.</p>}
    </>
  )
}

export const usable = (password: string, confirm: string) =>
  password.length >= MIN_PASSWORD && password === confirm
