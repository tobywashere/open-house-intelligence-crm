// Signals only; every listener obtains the current record from the protected API.
export const approvalsAvailable = () => window.dispatchEvent(new Event('ohi-approvals-available'))
export const approvalDecided = () => window.dispatchEvent(new Event('ohi-approval-decided'))
export function onApprovalsAvailable(listener: () => void): () => void {
  window.addEventListener('ohi-approvals-available', listener)
  return () => window.removeEventListener('ohi-approvals-available', listener)
}
export function onApprovalDecided(listener: () => void): () => void {
  window.addEventListener('ohi-approval-decided', listener)
  return () => window.removeEventListener('ohi-approval-decided', listener)
}
