// Python writes CRLF to pipes on Windows and LF on POSIX. The caller splits
// stdout on LF, so strip the remaining CR before parsing the readiness marker.
export function backendUrlFromLine(line: string): string | null {
  return /^WS_URL=(\S+)$/.exec(line.replace(/\r$/, ""))?.[1] ?? null;
}