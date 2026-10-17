import type { UploadFileInfo } from '../../data/adapter'

// Turns picked or dropped files and folders into upload entries that keep the
// relative path inside the folder (UI.md 14). No file type is blocked (UI.md 15).

export const SUPPORTED_EXTENSIONS = ['pdf', 'xlsx', 'xlsm']
export const MAX_FILE_BYTES = 50 * 1024 * 1024

export function extensionOf(path: string): string {
  const name = path.split('/').pop() ?? path
  return name.includes('.') ? name.split('.').pop()!.toLowerCase() : ''
}

export function isSupported(path: string): boolean {
  return SUPPORTED_EXTENSIONS.includes(extensionOf(path))
}

export function fromFileList(files: FileList | File[]): UploadFileInfo[] {
  return [...files].map((f) => ({ relativePath: f.webkitRelativePath || f.name, sizeBytes: f.size, file: f }))
}

/** Adds new entries, replacing earlier ones with the same path. Keeps order of first appearance. */
export function mergeFiles(current: UploadFileInfo[], added: UploadFileInfo[]): UploadFileInfo[] {
  const byPath = new Map(current.map((f) => [f.relativePath, f]))
  for (const f of added) byPath.set(f.relativePath, f)
  return [...byPath.values()]
}

// --- Drag and drop with folders (FileSystemEntry API) ---

function readAllEntries(reader: FileSystemDirectoryReader): Promise<FileSystemEntry[]> {
  // readEntries returns batches; keep reading until an empty batch.
  return new Promise((resolve, reject) => {
    const all: FileSystemEntry[] = []
    const next = () =>
      reader.readEntries((batch) => {
        if (batch.length === 0) resolve(all)
        else {
          all.push(...batch)
          next()
        }
      }, reject)
    next()
  })
}

async function walk(entry: FileSystemEntry): Promise<UploadFileInfo[]> {
  if (entry.isFile) {
    const file = await new Promise<File>((resolve, reject) => (entry as FileSystemFileEntry).file(resolve, reject))
    return [{ relativePath: entry.fullPath.replace(/^\//, ''), sizeBytes: file.size, file }]
  }
  if (entry.isDirectory) {
    const children = await readAllEntries((entry as FileSystemDirectoryEntry).createReader())
    return (await Promise.all(children.map(walk))).flat()
  }
  return []
}

export async function fromDataTransfer(dt: DataTransfer): Promise<UploadFileInfo[]> {
  const entries = [...dt.items]
    .filter((i) => i.kind === 'file')
    .map((i) => i.webkitGetAsEntry?.())
    .filter((e): e is FileSystemEntry => !!e)
  if (entries.length === 0) return fromFileList(dt.files)
  return (await Promise.all(entries.map(walk))).flat()
}
