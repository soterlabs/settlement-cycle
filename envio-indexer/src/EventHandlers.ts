/**
 * Vat.LogNote handler — emits one VatDebtEvent per frob/grab dart contribution.
 *
 * Mirrors src/settle/queries/debt_timeseries.sql exactly:
 *   - match selector (LogNote topic0) ∈ {frob 0x76088703, grab 0x7bab3f40}
 *   - match ilk (topic1 = arg1) against the ALLOCATOR-* prefix
 *   - decode dart (int256) at byte offset 164 of the note's calldata payload
 *     (SQL's 1-indexed substr(input, 165, 32)); store raw, in WAD, NOT scaled.
 *
 * The Python side (EnvioDebtSource) aggregates per-day and cumsums, so this
 * stays a flat, un-scaled event log.
 *
 * V3 API: handlers register via `indexer.onEvent({ contract, event }, cb)` from
 * the `envio` package (the old `Contract.Event.handler` + `generated` import
 * were removed in HyperIndex v3).
 */
import { indexer } from "envio";

// 4-byte selectors, left-aligned in the anonymous LogNote's topic0.
const SEL_FROB = "0x76088703";
const SEL_GRAB = "0x7bab3f40";

// "ALLOCATOR-" in ASCII → 0x414c4c4f4341544f522d (10 bytes / 20 hex chars).
// Every prime we settle uses an ALLOCATOR-<name>-<letter> ilk (SPARK, BLOOM, …),
// so this one prefix captures them all and keeps non-allocator vault frobs out.
const ALLOCATOR_ILK_PREFIX = "0x414c4c4f4341544f522d";

// The note modifier copies calldata[0:224] into `data`. Within it:
//   [0:4]=selector  [4:36]=ilk  [36:68]=u  [68:100]=v
//   [100:132]=w     [132:164]=dink  [164:196]=dart
const DART_BYTE_OFFSET = 164;

/** Two's-complement decode of a 32-byte hex word (no 0x) → signed bigint. */
function toInt256(word32Hex: string): bigint {
  let v = BigInt("0x" + word32Hex);
  const TWO_255 = 1n << 255n;
  const TWO_256 = 1n << 256n;
  if (v >= TWO_255) v -= TWO_256;
  return v;
}

/** Extract the dart word from the decoded `bytes data` payload (0x-prefixed). */
function decodeDart(data: string): bigint {
  const hex = data.startsWith("0x") ? data.slice(2) : data;
  const start = DART_BYTE_OFFSET * 2;
  const word = hex.slice(start, start + 64);
  if (word.length !== 64) {
    throw new Error(
      `note payload too short for dart: got ${hex.length} hex chars, ` +
        `need >= ${start + 64}`,
    );
  }
  return toInt256(word);
}

indexer.onEvent(
  { contract: "Vat", event: "LogNote" },
  async ({ event, context }) => {
    const sig = event.params.sig.toLowerCase();
    if (sig !== SEL_FROB && sig !== SEL_GRAB) return;

    const ilk = event.params.arg1.toLowerCase();
    if (!ilk.startsWith(ALLOCATOR_ILK_PREFIX)) return;

    const dart = decodeDart(event.params.data);

    context.VatDebtEvent.set({
      id: `${event.chainId}_${event.block.number}_${event.logIndex}`,
      ilk,
      sig,
      dart,
      urn: event.params.arg2.toLowerCase(),
      blockNumber: event.block.number,
      blockTimestamp: event.block.timestamp,
      txHash: event.transaction.hash,
    });
  },
);
