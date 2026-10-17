/** Steps of the upload flow. A new client starts with the documents, no form (UI.md 14). */
export const NEW_CLIENT_STEPS = ['Dokumente hochladen', 'Prüfung und Profil erstellen', 'Abschluss']
export const MORE_DOCUMENTS_STEPS = ['Dokumente hochladen', 'Prüfung der neuen Werte', 'Abschluss']

export function stepsFor(isNew: boolean) {
  return isNew ? NEW_CLIENT_STEPS : MORE_DOCUMENTS_STEPS
}

export const STEP = { upload: 0, review: 1, done: 2 } as const
