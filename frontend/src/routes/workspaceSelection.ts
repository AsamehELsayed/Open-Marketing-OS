export function isCurrentWorkspaceSelection(
  actionProjectId: string,
  actionEpoch: number,
  selectedProjectId: string | null,
  currentEpoch: number,
): boolean {
  return actionProjectId === selectedProjectId && actionEpoch === currentEpoch;
}
