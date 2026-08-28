export const ROUTE_SHORTCUTS = {
  '1': '/pos',
  '2': '/dashboard',
  '3': '/catalogue',
  '4': '/inventory',
  '5': '/purchasing',
  '6': '/settings',
} as const;

export function routeForShortcut(key: string, altKey: boolean): string | null {
  if (!altKey) return null;
  return ROUTE_SHORTCUTS[key as keyof typeof ROUTE_SHORTCUTS] ?? null;
}

export function isEditableShortcutTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || target.matches('input, textarea, select, [role="textbox"]');
}
