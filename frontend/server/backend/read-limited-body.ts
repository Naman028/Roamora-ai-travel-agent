import "server-only";

export class BodyLimitError extends Error {
  constructor() {
    super("Body exceeded the configured byte limit.");
    this.name = "BodyLimitError";
  }
}

export async function readLimitedBody(
  body: ReadableStream<Uint8Array> | null,
  maximumBytes: number,
  declaredLength?: string | null,
): Promise<string> {
  if (declaredLength) {
    const parsedLength = Number.parseInt(declaredLength, 10);
    if (Number.isFinite(parsedLength) && parsedLength > maximumBytes) {
      throw new BodyLimitError();
    }
  }

  if (!body) {
    return "";
  }

  const reader = body.getReader();
  const chunks: Uint8Array[] = [];
  let totalBytes = 0;

  try {
    while (true) {
      const result = await reader.read();
      if (result.done) {
        break;
      }
      totalBytes += result.value.byteLength;
      if (totalBytes > maximumBytes) {
        await reader.cancel();
        throw new BodyLimitError();
      }
      chunks.push(result.value);
    }
  } finally {
    reader.releaseLock();
  }

  const bytes = new Uint8Array(totalBytes);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
}
